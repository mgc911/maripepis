"""Cliente de la tecla de hablar: manda una orden al demonio y sale.

Hyprland lo ejecuta en cada pulsación de ALT+Z, así que tiene que arrancar en
milisegundos: solo importa la biblioteca estándar. Nada de config, Whisper ni
httpx — de eso ya se encarga el demonio, que está siempre cargado.
"""

from __future__ import annotations

import socket
import subprocess
import sys

from .protocol import decode, encode, parse_argv, socket_path, usage

TIMEOUT = 0.5  # nunca dejar colgado al proceso que lanza el compositor


def _avisa(mensaje: str, cuerpo: str) -> None:
    """Aviso de escritorio de último recurso (el demonio no contesta)."""
    try:
        subprocess.run(
            ["notify-send", "-a", "Maripepis", "-u", "critical", mensaje, cuerpo],
            check=False, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001 - sin notify-send tampoco pasa nada
        pass


def send(req: dict, path: str | None = None) -> dict | None:
    """Envía una orden y devuelve la respuesta, o ``None`` si no hay demonio."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.connect(path or socket_path())
            sock.sendall(encode(req))
            sock.shutdown(socket.SHUT_WR)

            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        return decode(buf) if buf else {"ok": True}
    except OSError:  # ConnectionRefused, FileNotFound, timeout...
        return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    req = parse_argv(args)
    if req["cmd"] == "?":
        print(usage(), file=sys.stderr)
        return 2

    reply = send(req)

    if reply is None:
        # Los `stop` sueltos deben ser gratis: si sueltas la tecla sin demonio,
        # o tras un corte automático, no hay nada que avisar.
        if req["cmd"] == "start":
            _avisa("Maripepis no responde", "systemctl --user status maripepis")
        else:
            print("maripepis: el demonio no responde", file=sys.stderr)
        return 1

    if req["cmd"] in ("status", "ping"):
        print(reply.get("state", "?"))

    if not reply.get("ok", False):
        error = reply.get("error", "")
        if error:
            print(f"maripepis: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
