"""Proveedor local: habla con un servidor Ollama vía HTTP (`/api/chat`)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

import httpx

from ..veracidad import anuncia_sin_hacer, falta_llamar, lo_que_no_ha_hecho
from .base import LLMProvider

log = logging.getLogger("maripepis.ollama")

# Dos y no una: a la primera el modelo suele reaccionar, pero llamando a la
# herramienta equivocada (vuelve a mirar el tiempo en vez de escribir). A partir
# de la tercera ya es dar vueltas, y el turno se cierra con el desmentido.
_MAX_EMPUJONES = 2


def _empujon(faltan: set[str]) -> str:
    """Lo que se le dice para que haga lo que ha dado por hecho."""
    if faltan:
        cuales = " o ".join(sorted(faltan))
        return (
            f"NO está hecho: no has llamado a {cuales}. Llama AHORA a {cuales}, en "
            "este mismo turno, y a ninguna otra: los datos ya los tienes. Si el "
            'fichero existe, usa modo="sobrescribir" con el texto entero. Nada de '
            "contarme lo que vas a hacer ni de darlo por hecho."
        )
    return (
        "No has llamado a ninguna herramienta, así que eso NO está hecho. Hazlo "
        "AHORA, en este mismo turno, con la herramienta que haga falta. No me "
        "cuentes lo que vas a hacer: hazlo, y luego dime cómo ha quedado."
    )

class OllamaProvider(LLMProvider):
    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        temperature: float = 0.7,
        timeout: float = 120.0,
        context: int = 8192,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.context = context

    def _options(self, temperature: float | None = None) -> dict:
        """Opciones de generación.

        `num_ctx` va explícito porque el servidor de Ollama arranca con 4096
        tokens para todo el mundo, y aquí no llegan: entre el system prompt, la
        memoria, las descripciones de las herramientas y lo que devuelven, una
        petición ronda los 2500 antes de que nadie diga nada. Al pasarse, el
        contexto se recorta por el principio y la conversación deja de tener la
        forma que el modelo espera: contesta con la llamada a la herramienta
        escrita en el texto —que acaba dicha en voz alta, comando incluido— o
        directamente con una palabra suelta sin sentido.
        """
        opciones = {"temperature": self.temperature if temperature is None else temperature}
        if self.context:
            opciones["num_ctx"] = self.context
        return opciones

    @property
    def label(self) -> str:
        return f"Ollama · {self.model}"

    def stream_reply(self, system: str, messages: list[dict]) -> Iterator[str]:
        payload = {
            "model": self.model,
            # Ollama acepta el system como un mensaje con role "system".
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            "options": self._options(),
        }
        try:
            with httpx.stream(
                "POST", f"{self.host}/api/chat", json=payload, timeout=self.timeout
            ) as r:
                if r.status_code != 200:
                    body = r.read().decode("utf-8", "replace").strip()
                    raise RuntimeError(f"Ollama respondió {r.status_code}: {body}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise RuntimeError(f"Ollama: {data['error']}")
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"No pude conectar con Ollama en {self.host}. "
                "¿Está corriendo `ollama serve`?"
            ) from e

    def run_tools_turn(self, system, messages, tools, execute, max_iters: int = 8) -> str:
        """El turno con herramientas: llamar, ejecutar, volver, hasta que remate.

        Ocho vueltas y no cinco porque una petición normal ya encadena tres
        (mirar el tiempo, leer el fichero, escribirlo) y el modelo gasta alguna
        más corrigiéndose: con cinco, el turno se acababa a mitad de frase.
        """
        msgs: list[dict] = [{"role": "system", "content": system}, *messages]
        ollama_tools = [t.to_ollama() for t in tools]
        nombres = {t.name for t in tools}
        texto = ""
        mudos = 0
        empujones = 0

        for intento in range(max_iters):
            r = httpx.post(
                f"{self.host}/api/chat",
                json={
                    "model": self.model,
                    "messages": msgs,
                    "tools": ollama_tools,
                    "stream": False,
                    # Temperatura baja: la decisión de herramienta es más fiable.
                    # Salvo tras un turno mudo: repetir la misma petición igual
                    # suele dar el mismo silencio, así que se le mete algo de
                    # variación para que salga del bucle.
                    "options": self._options(min(self.temperature, 0.3) + 0.25 * mudos),
                },
                timeout=self.timeout,
            )
            if r.status_code != 200:
                raise RuntimeError(f"Ollama respondió {r.status_code}: {r.text.strip()}")
            message = r.json().get("message", {})
            texto = (message.get("content") or "").strip()
            tool_calls = message.get("tool_calls") or []

            # Un 7B escribe a veces la llamada en el texto en vez de emitirla como
            # tal, y Ollama la deja pasar tal cual. Si se le hace caso al texto, el
            # comando acaba dicho en voz alta —que es exactamente lo contrario de
            # ejecutarlo—, así que se rescata de ahí y se ejecuta.
            if not tool_calls:
                rescatadas, texto = rescatar_llamadas(texto, nombres)
                if rescatadas:
                    log.info("La llamada venía escrita en el texto; la ejecuto igual.")
                    tool_calls = rescatadas
                    message = {"role": "assistant", "content": texto, "tool_calls": tool_calls}

            if not tool_calls:
                # Turno mudo: el modelo ha generado algo que Ollama se ha comido
                # entero (pasa de vez en cuando con una llamada mal formada). Sin
                # esto, maripepis se queda callada y parece que no ha oído nada.
                if not texto and intento + 1 < max_iters:
                    mudos += 1
                    log.warning("Respuesta vacía de %s; lo intento otra vez.", self.model)
                    continue
                # El turno se acaba dejando el trabajo a medias, de dos maneras:
                # anunciando lo que hará («ahora voy a actualizarlo») o dándolo por
                # hecho sin haber llamado a la herramienta («te lo he guardado»).
                # Desmentirlo luego en voz alta es el último recurso; lo que de
                # verdad arregla el turno es insistirle aquí, que casi siempre lo
                # hace a la segunda. Una vez y solo una: más es dar vueltas.
                if empujones < _MAX_EMPUJONES and intento + 1 < max_iters:
                    pendiente = lo_que_no_ha_hecho(texto, execute)
                    if pendiente or anuncia_sin_hacer(texto):
                        empujones += 1
                        log.info("El turno se quedaba a medias (%s); le insisto (%d).",
                                 pendiente or "solo anunciaba la acción", empujones)
                        msgs.append({"role": "assistant", "content": texto})
                        msgs.append({"role": "user", "content": _empujon(
                            falta_llamar(texto, execute))})
                        continue
                break
            msgs.append(message)  # turno del asistente con las llamadas
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except json.JSONDecodeError:
                        args = {}
                result = execute(name, args)
                msgs.append({"role": "tool", "content": result, "tool_name": name})
        else:
            # Se acabaron las vueltas con el modelo todavía llamando a
            # herramientas. Lo que hay en `texto` es medio turno («el contenido
            # del fichero es el siguiente:») y no una respuesta: se le pide el
            # cierre sin herramientas, para que cuente lo que ya ha hecho en vez
            # de dejar la frase colgada.
            log.info("Turno agotado tras %d vueltas; pido el cierre.", max_iters)
            texto = self._cerrar_el_turno(msgs) or texto

        return texto or "No he podido terminar la acción; inténtalo otra vez."

    def _cerrar_el_turno(self, msgs: list[dict]) -> str:
        """Una última petición SIN herramientas: que resuma lo que acaba de hacer."""
        remate = {
            "role": "user",
            "content": ("Resume en una o dos frases lo que acabas de hacer, según lo que "
                        "han devuelto las herramientas. No llames a ninguna más, y no "
                        "digas que has hecho nada que no salga en esos resultados."),
        }
        try:
            r = httpx.post(
                f"{self.host}/api/chat",
                json={"model": self.model, "messages": [*msgs, remate],
                      "stream": False, "options": self._options()},
                timeout=self.timeout,
            )
            if r.status_code != 200:
                return ""
            return (r.json().get("message", {}).get("content") or "").strip()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("No pude cerrar el turno agotado: %s", e)
            return ""


def rescatar_llamadas(texto: str, nombres: set[str]) -> tuple[list[dict], str]:
    """Saca del texto las llamadas a herramientas que Ollama no ha reconocido.

    El modelo las escribe como `<tool_call>{"name": ..., "arguments": {...}}`, a
    veces sin las etiquetas y a veces con una palabra suelta delante. Se busca
    cualquier objeto JSON con el nombre de una herramienta nuestra; el JSON se
    decodifica de verdad (no con una expresión regular) porque lleva llaves
    anidadas dentro.

    Devuelve las llamadas en el formato de Ollama y el texto que queda una vez
    quitadas, que suele ser basura suelta y no algo que decir en voz alta.
    """
    decoder = json.JSONDecoder()
    llamadas: list[dict] = []
    resto: list[str] = []
    i = 0
    while (j := texto.find("{", i)) >= 0:
        try:
            datos, fin = decoder.raw_decode(texto, j)
        except ValueError:            # una llave suelta que no abre nada
            resto.append(texto[i : j + 1])
            i = j + 1
            continue
        nombre = datos.get("name") if isinstance(datos, dict) else None
        if nombre in nombres:
            llamadas.append({
                "function": {
                    "name": nombre,
                    "arguments": datos.get("arguments") or datos.get("parameters") or {},
                }
            })
            resto.append(texto[i:j])  # lo de delante se guarda; la llamada, no
        else:
            resto.append(texto[i:fin])
        i = fin
    resto.append(texto[i:])

    limpio = "".join(resto)
    if llamadas:
        limpio = limpio.replace("<tool_call>", "").replace("</tool_call>", "")
    return llamadas, " ".join(limpio.split())
