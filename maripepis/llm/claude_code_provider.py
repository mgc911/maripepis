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
    `config.toml`); por eso `accepts_tools = False`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator

from .base import LLMProvider

# Sin esta nota el modelo imita el formato del historial y contesta con "Tú:"
# delante, que en voz alta se oye fatal.
NOTA_HISTORIAL = (
    "El bloque «Conversación previa» es solo contexto. Responde únicamente al "
    "último mensaje del usuario, sin prefijos ni etiquetas."
)


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
        if con_historial:
            args += ["--append-system-prompt", NOTA_HISTORIAL]
        return args

    def _env(self) -> dict:
        env = os.environ.copy()
        # Todo el sentido de este backend es la suscripción: con una clave de API
        # en el entorno, Claude Code la preferiría y el turno se cobraría por
        # token sin que se note. Para eso ya está el backend `claude`.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        return env

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
