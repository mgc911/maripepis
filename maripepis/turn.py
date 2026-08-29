"""Un turno de respuesta, compartido por la REPL y el demonio de la tecla.

Aquí vive lo que no depende de cómo se pidió el turno (Enter, VAD o ALT+Z):
pedirle la respuesta al LLM (con herramientas si el modelo las soporta), irla
hablando frase a frase y guardarla en el historial.

En el historial va la respuesta y nada más. Hubo una versión que le añadía una
nota con lo que las herramientas habían hecho («escribir_fichero → Hecho: he
escrito /home/…»), para que el turno siguiente supiera la ruta de verdad. Salió
al revés: medido sobre qwen2.5:7b, con esa nota el turno siguiente no llamaba a
**ninguna** herramienta (0 de 6) —leía que ya estaba hecho y se quedaba tan
ancho—, y sin ella sí llamaba. Para saber qué hay en un fichero está
`leer_fichero`, que para eso se hizo.
"""

from __future__ import annotations

from collections.abc import Callable

from .llm.conversation import Conversation
from .utils.phrases import normalize
from .utils.sentences import iter_sentences
from .veracidad import lo_que_no_ha_hecho

# Pistas de que la respuesta ya está reconociendo que algo no ha ido bien. Si
# aparece alguna, el aviso sobra; si no, se añade.
_SUENA_A_FALLO = (
    "no he ", "no ha ", "no pude", "no puedo", "no se ha ", "no existe",
    "no encontr", "no esta instalad", "ha fallado", "fallo", "error", "problema",
    "no funciona", "no lo he", "no la he", "sin exito",
)


def _admite_el_fallo(texto: str) -> bool:
    """¿La respuesta del modelo reconoce que la acción no salió?"""
    limpio = f" {normalize(texto)} "
    return any(pista in limpio for pista in _SUENA_A_FALLO)


def stream_reply_text(provider, conversation: Conversation, speech=None,
                      on_token: Callable[[str], None] | None = None) -> str:
    """Transmite la respuesta token a token, la va hablando y la devuelve entera."""
    chunks: list[str] = []

    def _emit(tok: str) -> None:
        if on_token is not None:
            on_token(tok)
        chunks.append(tok)

    tokens = provider.stream_reply(conversation.system_prompt, conversation.messages)
    for sentence in iter_sentences(tokens, on_token=_emit):
        if speech:
            speech.say(sentence)
    return "".join(chunks)


def _desmentir_si_hace_falta(reply: str, execute, logger) -> str:  # noqa: ANN001
    """Añade la verdad si el modelo canta victoria sobre algo que no ha pasado.

    Son dos mentiras distintas, y la segunda es peor. Una: la herramienta dijo
    «NO he ejecutado nada» y el modelo remata el turno con un «ya lo tienes».
    Otra: el modelo no llama a ninguna herramienta y narra el éxito igual —el
    fichero se queda como estaba y no hay ni un fallo que enseñar—. Esta segunda
    es la que se cuela en una conversación larga, cuando el modelo se cree que ya
    lo hizo porque lo dijo antes.

    Quien escucha no ve la pantalla: sin esto, las dos suenan exactamente igual
    que si hubiera funcionado. Y no basta con pedírselo por el system prompt,
    que es lo que ya se le pide y no lo cumple.
    """
    if _admite_el_fallo(reply):
        return reply

    motivo = getattr(execute, "ultimo_fallo", None)
    if motivo:
        logger.warning("El modelo daba por hecho algo que falló (%s); lo desmiento.", motivo)
        aviso = f"Aviso: en realidad no ha funcionado, {motivo}."
    elif (sin_hacer := lo_que_no_ha_hecho(reply, execute)):
        logger.warning("El modelo presume de algo que no ha hecho (%r); lo desmiento.",
                       " ".join(reply.split())[:120])
        aviso = f"Aviso: en realidad {sin_hacer}. Pídemelo otra vez."
    else:
        return reply

    return f"{reply.rstrip()} {aviso}" if reply.strip() else aviso


def reply_turn(provider, conversation: Conversation, user_text: str, logger, *,
               speech=None, tools=None, execute=None,
               on_token: Callable[[str], None] | None = None) -> str | None:
    """Responde a `user_text` y actualiza el historial.

    Usa las herramientas si las hay, cayendo a la respuesta normal si el modelo
    no las soporta. Devuelve el texto de la respuesta, o ``None`` si el proveedor
    falló (en ese caso deshace el turno de usuario y calla la voz).
    """
    conversation.add_user(user_text)
    # Turno nuevo: lo del anterior ya no cuenta. Va aquí y no dentro de la rama
    # de herramientas porque lo que se apunta (fallos, llamadas, registro) se
    # consulta pase lo que pase, también si se cae al streaming.
    if execute is not None and hasattr(execute, "reset"):
        execute.reset()

    try:
        # Un proveedor con herramientas propias (Claude Code) no acepta las
        # nuestras: con él se va por la vía normal, que además va en streaming.
        if tools and execute is not None and getattr(provider, "accepts_tools", True):
            try:
                reply = provider.run_tools_turn(
                    conversation.system_prompt, conversation.messages, tools, execute
                )
            except Exception as e:  # noqa: BLE001 - modelo sin tool-calling, etc.
                logger.warning("Herramientas no disponibles (%s); respondo sin ellas.", e)
                reply = stream_reply_text(provider, conversation, speech, on_token)
            else:
                reply = _desmentir_si_hace_falta(reply, execute, logger)
                if speech:
                    for sentence in iter_sentences(iter([reply])):
                        speech.say(sentence)
        else:
            reply = stream_reply_text(provider, conversation, speech, on_token)
    except Exception as e:  # noqa: BLE001
        logger.error("Fallo hablando con el proveedor LLM: %s", e)
        conversation.undo_last_user()
        if speech:
            speech.stop()
        return None

    conversation.add_assistant(reply)
    return reply
