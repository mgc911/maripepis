"""Un turno de respuesta, compartido por la REPL y el demonio de la tecla.

Aquí vive lo que no depende de cómo se pidió el turno (Enter, VAD o ALT+Z):
pedirle la respuesta al LLM (con herramientas si el modelo las soporta), irla
hablando frase a frase y guardarla en el historial.
"""

from __future__ import annotations

from collections.abc import Callable

from .llm.conversation import Conversation
from .utils.phrases import normalize
from .utils.sentences import iter_sentences

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
    """Añade la verdad si el modelo canta victoria sobre algo que ha fallado.

    Un 7B se salta el «NO he ejecutado nada» de la herramienta y remata el turno
    con un «ya lo tienes». Quien lo escucha no ve la pantalla: se queda tan
    contento con una carpeta que no existe. Antes que fiarlo todo al prompt, se
    comprueba lo que las herramientas dijeron de verdad.
    """
    motivo = getattr(execute, "ultimo_fallo", None)
    if not motivo:
        return reply
    if _admite_el_fallo(reply):
        return reply
    logger.warning("El modelo daba por hecho algo que falló (%s); lo desmiento.", motivo)
    aviso = f"Aviso: en realidad no ha funcionado, {motivo}."
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

    try:
        # Un proveedor con herramientas propias (Claude Code) no acepta las
        # nuestras: con él se va por la vía normal, que además va en streaming.
        if tools and execute is not None and getattr(provider, "accepts_tools", True):
            if hasattr(execute, "reset"):
                execute.reset()
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
