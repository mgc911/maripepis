"""Proveedor nube por suscripción: habla con Claude a través del CLI de Claude Code.

A diferencia del backend `claude` (SDK oficial + ANTHROPIC_API_KEY, que se paga
por token), este lanza el binario `claude` en modo `--print` y le lee la
respuesta en streaming. La autenticación es la de Claude Code: si has entrado
con tu cuenta (`claude` → `/login`), los turnos van contra tu **suscripción** y
no gastan crédito de API.

Dos cosas que el CLI impone y explican el diseño de este fichero:
  - No acepta un historial estructurado: la conversación viaja aplanada dentro
    del prompt (ver :meth:`ClaudeCodeProvider.build_prompt`).
  - No admite herramientas nuestras. Las que use son las suyas (`tools` en
    `config.toml`); por eso `accepts_tools = False`. Como no pasan por el
    `Acciones` de maripepis, nadie se enteraría de que corren: lo que las saca a
    la luz es el aviso `on_tool` (ver abajo), que el demonio engancha para
    pintarlas en la ventana de chat.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator

from ..utils.turnos import TURNO_ENV, nuevo_turno
from .base import LLMProvider

# Sin esta nota el modelo imita el formato del historial y contesta con "Tú:"
# delante, que en voz alta se oye fatal.
NOTA_HISTORIAL = (
    "El bloque «Conversación previa» es solo contexto. Responde únicamente al "
    "último mensaje del usuario, sin prefijos ni etiquetas."
)

# Sin esta nota, «hazme un markdown con la lista de la compra» se resuelve con un
# `cat > fichero << EOF` por Bash —medido: 1 de 1 la primera vez que se probó—, y
# entonces la ventana de chat no puede enseñar el documento: de un comando de
# shell no hay forma de saber qué fichero ha escrito sin ponerse a interpretar
# redirecciones, y equivocarse ahí es enseñar un documento que no es.
NOTA_FICHEROS = (
    "Para crear o cambiar un fichero usa SIEMPRE la herramienta Write (o Edit), y "
    "para leerlo la herramienta Read; nunca un `cat`, un `echo >` ni un `tee` por "
    "Bash."
)


# El argumento que se enseña de cada herramienta de Claude Code: el JSON entero
# no dice nada y `description` es prosa que ya cuenta la respuesta. Lo que hace
# falta ver es QUÉ ha ejecutado (el comando, la búsqueda, el fichero).
_ARGUMENTO_PRINCIPAL = {
    "Bash": "command",
    "WebSearch": "query",
    "WebFetch": "url",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "Task": "description",
}
_MAX_RESUMEN = 120
# Las suyas que andan con un fichero concreto, y de qué argumento sale la ruta.
# Con esas, la ventana enseña el documento entero además de la línea de ⚙️: las
# de escribir para verlo recién hecho, `Read` para «enséñame el resumen».
_TOCAN_FICHERO = {
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
    "Read": "file_path",
}


def resumen_de_la_herramienta(nombre: str, args: dict) -> str:
    """Una línea legible de la llamada, con la misma pinta que las de maripepis.

    ``("Bash", {"command": "free -h"})`` → ``Bash · free -h``
    """
    if not isinstance(args, dict):
        args = {}
    clave = _ARGUMENTO_PRINCIPAL.get(nombre)
    valor = str(args.get(clave) or "") if clave else ""
    if not valor.strip():
        valor = ", ".join(f"{k}={v}" for k, v in args.items() if str(v).strip())
    valor = " ".join(valor.split())
    if len(valor) > _MAX_RESUMEN:
        valor = valor[: _MAX_RESUMEN - 1].rstrip() + "…"
    return f"{nombre} · {valor}" if valor else nombre


class ClaudeCodeProvider(LLMProvider):
    # Las herramientas de maripepis no se le pueden pasar al CLI: las suyas las
    # pone Claude Code. Con esto, el turno no intenta usarlas y va en streaming.
    accepts_tools = False

    def __init__(
        self,
        cli: str = "claude",
        model: str = "",
        tools: str = "",
        permission_mode: str = "",
        safe_mode: bool = True,
        timeout_s: float = 120.0,
        cwd: str = "",
    ) -> None:
        ruta = shutil.which(cli)
        if ruta is None:
            raise RuntimeError(
                f"No encuentro el ejecutable {cli!r} de Claude Code. Instálalo "
                "(npm install -g @anthropic-ai/claude-code) y entra con tu cuenta: "
                "ejecuta `claude` y usa /login."
            )
        self.cli = ruta
        self.model = model
        self.tools = tools
        self.permission_mode = permission_mode
        self.safe_mode = safe_mode
        self.timeout_s = float(timeout_s)
        self.cwd = cwd
        #: Aviso de que una herramienta suya se ha puesto en marcha:
        #: ``on_tool(nombre, resumen, salio_bien)``. Lo engancha el demonio para
        #: pintarla en la ventana de chat, igual que hace con las de maripepis
        #: por `Acciones.on_call`. Sin esto, un turno que se pasa diez segundos
        #: buscando en internet no enseña absolutamente nada por el camino.
        #: Aviso de que una herramienta suya se ha puesto en marcha:
        #: ``on_tool(nombre, resumen, salio_bien)``.
        self.on_tool: Callable[[str, str, bool], None] | None = None
        #: Aviso de que ha dejado un fichero escrito: ``on_file(ruta)``. Va
        #: aparte de `on_tool` y llega **más tarde** a propósito: la llamada se
        #: canta al lanzarla (que es lo que tapa la espera) y entonces el fichero
        #: todavía no existe. Este salta con el resultado, con el fichero ya en
        #: el disco y sabiendo que no ha fallado.
        self.on_file: Callable[[str], None] | None = None

    @property
    def label(self) -> str:
        return f"Claude Code · {self.model or 'modelo por defecto'} (suscripción)"

    # ------------------------------------------------------------------ armado

    @staticmethod
    def build_prompt(messages: list[dict]) -> tuple[str, bool]:
        """Aplana el historial neutro en un prompt de texto.

        Devuelve ``(prompt, hay_historial)``. El último mensaje es el turno que
        toca contestar; los anteriores van arriba como contexto.
        """
        if not messages:
            return "", False

        actual = messages[-1]["content"]
        previos = messages[:-1]
        if not previos:
            return actual, False

        lineas = [
            f"{'Usuario' if m['role'] == 'user' else 'Tú'}: {m['content']}"
            for m in previos
        ]
        prompt = (
            "Conversación previa:\n"
            + "\n".join(lineas)
            + "\n\nMensaje actual del usuario:\n"
            + actual
        )
        return prompt, True

    def build_args(self, system: str, con_historial: bool = False) -> list[str]:
        """Línea de comandos del CLI para un turno."""
        args = [
            self.cli,
            "--print",
            "--output-format", "stream-json",
            "--include-partial-messages",   # deltas de texto: podemos ir hablando
            "--verbose",                    # el CLI lo exige junto a stream-json
            "--no-session-persistence",     # el historial lo llevamos nosotros
            "--system-prompt", system,      # sustituye al system prompt de Claude Code
            # Cadena vacía = sin ninguna herramienta (asistente de voz a secas).
            # Una lista ("Bash,WebSearch") o "default" le devuelve las suyas.
            "--tools", self.tools,
        ]
        if self.safe_mode:
            # Ignora CLAUDE.md, plugins, hooks y servidores MCP: sin esto, cada
            # turno arrastra decenas de miles de tokens de contexto que no pintan
            # nada en una conversación hablada.
            args.append("--safe-mode")
        if self.model:
            args += ["--model", self.model]
        if self.permission_mode:
            args += ["--permission-mode", self.permission_mode]
        # Las dos notas van juntas en un solo `--append-system-prompt`: repetir la
        # opción no está dicho en ninguna parte que sume, y no se va a averiguar
        # a base de que un turno salga raro.
        notas = []
        if con_historial:
            notas.append(NOTA_HISTORIAL)
        if self.tools == "default" or any(
            h in self.tools for h in ("Write", "Edit", "Read")
        ):
            notas.append(NOTA_FICHEROS)
        if notas:
            args += ["--append-system-prompt", " ".join(notas)]
        return args

    def _env(self) -> dict:
        env = os.environ.copy()
        # Todo el sentido de este backend es la suscripción: con una clave de API
        # en el entorno, Claude Code la preferiría y el turno se cobraría por
        # token sin que se note. Para eso ya está el backend `claude`.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        # La marca del turno, que hereda todo lo que lance el CLI con su `Bash`.
        # Es lo que permite que la orden de WhatsApp distinga «el usuario ha
        # dicho que sí» de «el modelo se ha contestado a sí mismo»: los dos pasos
        # de un mismo turno traen la misma marca, y con la misma marca no se
        # manda nada. Aquí y no en `build_args` porque esto se llama una vez por
        # turno, que es exactamente lo que hay que contar.
        env[TURNO_ENV] = nuevo_turno()
        return env

    def _avisar(self, aviso, *datos) -> None:  # noqa: ANN001
        """Llama a un aviso sin dejar que se lleve el turno por delante.

        Es código de fuera (el demonio, un test) metido en mitad del bucle que va
        leyendo al CLI: si revienta, lo que se pierde es una línea en la ventana,
        no la respuesta que el usuario está esperando.
        """
        if aviso is None:
            return
        try:
            aviso(*datos)
        except Exception:  # noqa: BLE001, S110 - un visor no puede cortar el turno
            pass

    # --------------------------------------------------------------- streaming

    def stream_reply(self, system: str, messages: list[dict]) -> Iterator[str]:
        prompt, con_historial = self.build_prompt(messages)
        args = self.build_args(system, con_historial)

        # stderr a un fichero temporal, no a una tubería: nadie la va leyendo
        # mientras dura el turno y una tubería llena bloquearía al CLI.
        errores = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
        try:
            proc = subprocess.Popen(  # noqa: S603 - argumentos propios, sin shell
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errores,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self.cwd or None,
                env=self._env(),
            )
        except OSError as e:
            errores.close()
            raise RuntimeError(f"No pude lanzar Claude Code ({self.cli}): {e}") from e

        limite = time.monotonic() + self.timeout_s
        algo_dicho = False
        # id → (nombre, fichero que deja): el `tool_result`, que es el que dice si
        # salió bien y el momento en que el fichero ya existe, llega después y
        # solo trae el id.
        en_marcha: dict[str, tuple[str, str]] = {}
        try:
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass  # murió al arrancar; el motivo lo dirá stderr más abajo

            for linea in proc.stdout:
                if time.monotonic() > limite:
                    raise RuntimeError(
                        f"Claude Code no terminó en {self.timeout_s:g}s."
                    )
                evento = _json_o_none(linea)
                if evento is None:
                    continue

                tipo = evento.get("type")
                if tipo == "stream_event":
                    trozo = _delta_de_texto(evento.get("event") or {})
                    if trozo:
                        algo_dicho = True
                        yield trozo
                elif tipo == "assistant":
                    # El mensaje entero ya trae la llamada con sus argumentos
                    # completos; los `input_json_delta` del streaming llegan a
                    # cachos y habría que recomponerlos a mano.
                    for nombre, id_, args in _llamadas(evento):
                        clave = _TOCAN_FICHERO.get(nombre)
                        en_marcha[id_] = (nombre,
                                          str(args.get(clave) or "") if clave else "")
                        self._avisar(self.on_tool, nombre,
                                     resumen_de_la_herramienta(nombre, args), True)
                elif tipo == "user":
                    # La vuelta de la herramienta. La llamada NO se vuelve a
                    # cantar (ya se anunció al lanzarla); aquí solo se cuenta lo
                    # que falló, y se enseña el fichero de la que salió bien, que
                    # es el primer momento en que existe en el disco.
                    for id_, mal, motivo in _resultados(evento):
                        nombre, fichero = en_marcha.pop(id_, ("herramienta", ""))
                        if mal:
                            self._avisar(self.on_tool, nombre,
                                         f"{nombre} · {motivo}", False)
                        elif fichero:
                            self._avisar(self.on_file, fichero)
                elif tipo == "result":
                    if evento.get("is_error") or evento.get("subtype") != "success":
                        raise RuntimeError(_motivo_del_fallo(evento))
                    if not algo_dicho:
                        # Red de seguridad: si no llegaron deltas (turno que solo
                        # usó herramientas, versión del CLI sin ellos...), al menos
                        # devolvemos la respuesta entera.
                        final = (evento.get("result") or "").strip()
                        if final:
                            algo_dicho = True
                            yield final

            codigo = _esperar(proc, limite)
            if codigo not in (0, None):
                raise RuntimeError(
                    f"Claude Code terminó con código {codigo}. {_cola(errores)}".strip()
                )
            if not algo_dicho:
                raise RuntimeError(
                    f"Claude Code no devolvió texto. {_cola(errores)}".strip()
                )
        finally:
            _rematar(proc)
            errores.close()


# --------------------------------------------------------------------- ayudas


def _json_o_none(linea: str) -> dict | None:
    linea = linea.strip()
    if not linea.startswith("{"):
        return None
    try:
        return json.loads(linea)
    except json.JSONDecodeError:
        return None


def _delta_de_texto(event: dict) -> str:
    """Texto de un `content_block_delta`; "" para todo lo demás.

    Deja fuera el pensamiento y los argumentos de las herramientas: al usuario
    solo le interesa (y solo se le habla) la respuesta.
    """
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta") or {}
    if delta.get("type") != "text_delta":
        return ""
    return delta.get("text") or ""


def _bloques(evento: dict) -> list:
    """Los bloques de contenido de un evento `assistant`/`user`, o lista vacía."""
    contenido = (evento.get("message") or {}).get("content")
    return contenido if isinstance(contenido, list) else []


def _llamadas(evento: dict):
    """``(nombre, id, argumentos)`` de cada herramienta que arranca en el evento."""
    for b in _bloques(evento):
        if isinstance(b, dict) and b.get("type") == "tool_use":
            yield (str(b.get("name") or "herramienta"),
                   str(b.get("id") or ""),
                   b.get("input") if isinstance(b.get("input"), dict) else {})


def _resultados(evento: dict, maximo: int = 120):
    """``(id, ha_fallado, motivo)`` de cada herramienta que vuelve en el evento."""
    for b in _bloques(evento):
        if isinstance(b, dict) and b.get("type") == "tool_result":
            motivo = " ".join(str(b.get("content") or "ha fallado").split())
            if len(motivo) > maximo:
                motivo = motivo[: maximo - 1].rstrip() + "…"
            yield str(b.get("tool_use_id") or ""), bool(b.get("is_error")), motivo


def _motivo_del_fallo(evento: dict) -> str:
    partes = [
        str(evento.get("subtype") or "error"),
        str(evento.get("api_error_status") or ""),
        str(evento.get("result") or ""),
    ]
    return "Claude Code falló: " + " · ".join(p for p in partes if p)


def _cola(errores, maximo: int = 400) -> str:
    try:
        errores.seek(0)
        texto = " ".join(errores.read().split())
    except (OSError, ValueError):
        return ""
    return texto[-maximo:]


def _esperar(proc, limite: float) -> int | None:
    try:
        return proc.wait(timeout=max(1.0, limite - time.monotonic()))
    except subprocess.TimeoutExpired:
        return None


def _rematar(proc) -> None:
    """Cierra tuberías y mata el proceso si sigue vivo (p.ej. tras un barge-in)."""
    for tuberia in (proc.stdin, proc.stdout):
        try:
            if tuberia is not None and not tuberia.closed:
                tuberia.close()
        except OSError:
            pass
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
