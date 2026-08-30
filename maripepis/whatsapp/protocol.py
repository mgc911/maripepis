"""Protocolo entre quien quiere mandar un WhatsApp y el demonio que lo manda.

Una línea JSON por orden, como en `hotkey/protocol.py`. Se depura igual de bien
a mano:

    socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/maripepis-whatsapp.sock
    {"accion": "estado"}

Órdenes: ``estado``, ``enviar`` (``destino`` + ``texto``), ``revocar`` (retira lo
último enviado), ``grupos`` (``filtro``) y ``ping``.

Este módulo no importa `neonize` a propósito: es el único trozo que se puede
probar sin sesión, sin red y sin la biblioteca instalada, y ahí es donde vive la
validación de a quién se escribe.
"""

from __future__ import annotations

import json
import os
import re

SOCKET_ENV = "MARIPEPIS_WHATSAPP_SOCKET"
SOCKET_NAME = "maripepis-whatsapp.sock"

ORDENES = ("estado", "enviar", "revocar", "grupos", "ping")

#: Dónde vive la sesión. Es un dispositivo vinculado a tu cuenta: ese fichero
#: **es** tu WhatsApp, y quien lo copie escribe como tú.
SESION = "~/.local/share/maripepis/whatsapp/session.sqlite3"

#: Los dos servidores de WhatsApp que nos importan: personas y grupos.
PERSONAS = "s.whatsapp.net"
GRUPOS = "g.us"

#: El mismo tope que la herramienta. Aquí se repite porque el demonio no puede
#: fiarse de que quien le habla lo haya mirado.
MAX_TEXTO = 1000

#: Cuántos grupos se devuelven como mucho. No es capricho: una cuenta normal
#: está en cientos de grupos (la de este equipo, en 269), y volcarlos todos no es
#: una agenda, es un listín — que además acabaría viajando al modelo.
MAX_GRUPOS = 20


def partes_destino(destino: str) -> tuple[str, str]:
    """Parte un destino en (usuario, servidor), o ``("", "")`` si no vale.

    Acepta las dos cosas que Maripepis sabe nombrar:

    - un teléfono ya normalizado, solo dígitos con prefijo → chat de una persona;
    - un identificador de grupo, ``1203...-1600...@g.us`` → chat de grupo.

    Los grupos no tienen teléfono y por eso el enlace `whatsapp://` nunca pudo
    con ellos: su identificador solo se ve desde dentro de la sesión.

    Devolver ``("", "")`` corta el envío. Es la última barrera antes de escribirle
    a alguien: más vale no mandar nada que mandárselo a un desconocido.
    """
    d = (destino or "").strip()
    if not d:
        return "", ""

    if "@" in d:
        usuario, _, servidor = d.partition("@")
        if servidor != GRUPOS or not re.fullmatch(r"[\d-]{5,40}", usuario):
            return "", ""
        return usuario, GRUPOS

    if not re.fullmatch(r"\d{8,15}", d):      # los topes de E.164
        return "", ""
    return d, PERSONAS


def es_grupo(destino: str) -> bool:
    return partes_destino(destino)[1] == GRUPOS


def socket_path(configured: str | None = None) -> str:
    """Ruta del socket: la de config, o $MARIPEPIS_WHATSAPP_SOCKET, o el runtime."""
    if configured:
        return os.path.expanduser(configured)
    env = os.environ.get(SOCKET_ENV)
    if env:
        return os.path.expanduser(env)
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, SOCKET_NAME)


def sesion_path(configured: str | None = None) -> str:
    return os.path.expanduser(configured or SESION)


def encode(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def decode(raw: bytes | str) -> dict:
    """Nunca lanza: un JSON roto se convierte en una orden desconocida."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"accion": "?"}
    return data if isinstance(data, dict) else {"accion": "?"}


def error(motivo: str, **campos) -> dict:
    return {"ok": False, "error": motivo, **campos}
