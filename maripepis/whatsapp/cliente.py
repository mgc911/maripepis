"""Hablar con el demonio de WhatsApp. Tres líneas y la biblioteca estándar.

Lo usa la herramienta (`tools/whatsapp.py`) y lo usas tú desde la terminal para
ver qué pasa. Aquí no se importa `neonize` —ni nada que tarde en cargar— porque
esto se ejecuta dentro de un turno hablado, con el usuario esperando: la sesión
la sostiene el demonio, que ya está puesto.

La única excepción es `vincular`, que sí necesita la biblioteca y por eso la
importa dentro de su rama, no arriba.
"""

from __future__ import annotations

import socket
import sys

from .protocol import ORDENES, decode, encode, socket_path

#: Margen ancho a propósito: el demonio espera hasta 10 s a que la sesión esté
#: lista, y un envío pasa por la red de WhatsApp. Cortar antes sería contar como
#: fallo un mensaje que va a salir igualmente — la peor mentira posible aquí.
TIMEOUT = 20.0


def pedir(req: dict, path: str | None = None, timeout: float = TIMEOUT) -> dict | None:
    """Manda una orden y devuelve la respuesta. ``None`` si no hay demonio.

    Distinguir «no hay demonio» de «el demonio dice que no» importa: lo primero
    se arregla arrancando un servicio y hay que decirlo así; lo segundo es una
    respuesta de verdad, con su motivo.
    """
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path or socket_path())
            sock.sendall(encode(req))
            sock.shutdown(socket.SHUT_WR)

            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        return decode(buf) if buf else None
    except OSError:            # no está, no contesta, o ha tardado demasiado
        return None


def enviar(destino: str, texto: str, path: str | None = None) -> dict | None:
    """Envía. De verdad. Sin preguntar: quien pregunta es la herramienta."""
    return pedir({"accion": "enviar", "destino": destino, "texto": texto}, path)


def revocar(path: str | None = None) -> dict | None:
    """Retira el último mensaje enviado, si WhatsApp todavía lo permite."""
    return pedir({"accion": "revocar"}, path)


def estado(path: str | None = None) -> dict | None:
    return pedir({"accion": "estado"}, path)


SIN_DEMONIO = (
    "No hay demonio de WhatsApp escuchando. Arráncalo con:\n"
    "  systemctl --user start maripepis-whatsapp"
)


def main(argv: list[str] | None = None) -> int:
    """`maripepis-wa`: mirar y probar el demonio desde la terminal."""
    args = list(sys.argv[1:] if argv is None else argv)
    orden = args[0] if args else ""

    if orden == "vincular":
        from .daemon import vincular               # noqa: PLC0415 - carga lenta
        return vincular()

    if orden not in ORDENES:
        print(f"uso: maripepis-wa vincular | {' | '.join(ORDENES)}", file=sys.stderr)
        print("  enviar <telefono|jid@g.us> <texto>   ESTO SALE DE VERDAD", file=sys.stderr)
        return 2

    req: dict = {"accion": orden}
    if orden == "enviar":
        if len(args) < 3:
            print("uso: maripepis-wa enviar <telefono|jid@g.us> <texto>", file=sys.stderr)
            return 2
        req |= {"destino": args[1], "texto": " ".join(args[2:])}
    elif orden == "grupos" and len(args) > 1:
        req["filtro"] = " ".join(args[1:])

    resp = pedir(req)
    if resp is None:
        print(SIN_DEMONIO, file=sys.stderr)
        return 1
    if not resp.get("ok"):
        print(f"✗ {resp.get('error', 'no ha podido ser')}", file=sys.stderr)
        return 1

    if orden == "grupos":
        grupos = resp.get("grupos") or []
        print(f"{len(grupos)} de {resp.get('total', 0)} grupos"
              + (f" · {resp['detalle']}" if resp.get("detalle") else ""))
        for g in grupos:
            print(f'  "{g["jid"]}"   # {g["nombre"]}')
    else:
        print(" · ".join(f"{k}={v}" for k, v in resp.items() if k != "ok"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
