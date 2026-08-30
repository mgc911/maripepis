"""Protocolo entre la tecla (cliente) y el demonio: una línea JSON por orden.

Se puede depurar a mano:

    socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/maripepis.sock
    {"cmd": "status"}

Órdenes: ``start`` (con ``mode``: "assistant" | "dictation"), ``stop``,
``cancel``, ``status``, ``ping`` y ``backend`` (con ``value``: "claude" |
"claude-code"). **``stop`` no lleva modo**: el demonio recuerda el del ``start``,
así soltar ALT+SHIFT+Z en dos tiempos no lía nada.

``backend`` cambia de motor sin reiniciar ni perder la conversación. Los dos que
quedan son el mismo Claude por dos caminos que se pagan distinto —la API por
token y el CLI con la suscripción—, así que sirve para saltar de uno a otro
cuando se agota alguno. Va por una conexión **aparte**, corta, como cualquier
orden del cliente de la tecla; no por la de ``subscribe``, que se queda muda a
propósito (ver abajo) y leer de ella rompería el modo en que se detecta que una
ventana se ha cerrado.

Hay una orden más, ``subscribe``, que no usa la tecla sino la ventana de chat:
en vez de contestar y cerrar, el demonio se queda con la conexión y le va
empujando **eventos** (una línea JSON cada uno, con la clave ``event``) hasta que
el visor la cierra. El trato es que **el visor no vuelve a escribir**: el demonio
no lee de ahí, y usa justo eso para saber que una ventana se ha cerrado (lo que
llegue por ese socket solo puede ser el EOF). Se ve igual de bien a mano:

    socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/maripepis.sock
    {"cmd": "subscribe"}
"""

from __future__ import annotations

import json
import os

SOCKET_ENV = "MARIPEPIS_SOCKET"
SOCKET_NAME = "maripepis.sock"

COMMANDS = ("start", "stop", "cancel", "status", "ping", "backend")
MODES = ("assistant", "dictation")
#: Los motores entre los que se puede cambiar en caliente. El primero es el que
#: se da por supuesto cuando no hay otra cosa dicha.
BACKENDS = ("claude-code", "claude")

# La orden de la ventana de chat. Fuera de COMMANDS a propósito: `parse_argv` es
# lo que acepta el cliente de la tecla, y suscribirse desde ahí no tiene sentido
# (el cliente manda una orden y muere; el visor se queda escuchando).
SUBSCRIBE = "subscribe"

# Eventos que el demonio empuja a los suscriptores:
#   hello  estado y conversación en curso, nada más conectar (para no abrir en blanco)
#   state  cambio de estado, con `mode`:
#            idle | recording | processing (transcribiendo) |
#            thinking (el LLM trabajando, herramientas incluidas) | speaking
#   reset  se ha empezado conversación nueva (caducó el contexto)
#   user   lo que ha entendido Whisper
#   tool   una herramienta en marcha: la orden que se ha ejecutado y si salió
#   document un fichero recién escrito (`path` + `text`), para verlo en el chat
#            plegable y con el Markdown pintado. No vuelve al modelo.
#   delta  un trozo de la respuesta, según se genera
#   reply  la respuesta entera y cerrada
#   notice aviso sin importancia (no te he oído, copiado al portapapeles…)
#   backend se ha cambiado de motor (o se ha intentado y no ha podido)
#   error  algo ha fallado
EVENTS = ("hello", "state", "reset", "user", "tool", "document", "delta",
          "reply", "notice", "backend", "error")


def event(kind: str, **fields) -> dict:
    """Un evento para los suscriptores: el tipo viaja en la clave ``event``."""
    return {"event": kind, **fields}


def socket_path(configured: str | None = None) -> str:
    """Ruta del socket: la de config, o $MARIPEPIS_SOCKET, o $XDG_RUNTIME_DIR."""
    if configured:
        return os.path.expanduser(configured)
    env = os.environ.get(SOCKET_ENV)
    if env:
        return os.path.expanduser(env)
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, SOCKET_NAME)


def encode(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def decode(raw: bytes | str) -> dict:
    """Nunca lanza: un JSON roto se convierte en una orden desconocida."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"cmd": "?"}
    return data if isinstance(data, dict) else {"cmd": "?"}


def parse_argv(argv: list[str]) -> dict:
    """Convierte los argumentos del cliente en una orden.

    ``["start", "dictation"]`` → ``{"cmd": "start", "mode": "dictation"}``
    ``["start"]``              → modo asistente (el habitual)
    """
    if not argv:
        return {"cmd": "?"}

    cmd = argv[0].lower()
    if cmd not in COMMANDS:
        return {"cmd": "?"}

    if cmd == "backend":
        valor = argv[1].lower() if len(argv) > 1 else ""
        if valor not in BACKENDS:
            return {"cmd": "?"}
        return {"cmd": "backend", "value": valor}

    if cmd != "start":
        return {"cmd": cmd}

    mode = argv[1].lower() if len(argv) > 1 else MODES[0]
    if mode not in MODES:
        return {"cmd": "?"}
    return {"cmd": "start", "mode": mode}


def usage() -> str:
    return (
        "uso: maripepis-hotkey start [assistant|dictation] | stop | cancel | "
        "status | ping | backend [claude-code|claude]"
    )
