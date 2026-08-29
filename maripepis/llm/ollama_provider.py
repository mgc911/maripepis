"""Proveedor local: habla con un servidor Ollama vía HTTP (`/api/chat`)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

import httpx

from .base import LLMProvider

log = logging.getLogger("maripepis.ollama")


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

    def run_tools_turn(self, system, messages, tools, execute, max_iters: int = 5) -> str:
        msgs: list[dict] = [{"role": "system", "content": system}, *messages]
        ollama_tools = [t.to_ollama() for t in tools]
        nombres = {t.name for t in tools}
        texto = ""
        mudos = 0

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

        return texto or "No he podido terminar la acción; inténtalo otra vez."


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
