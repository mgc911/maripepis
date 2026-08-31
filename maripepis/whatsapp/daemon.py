"""El proceso que sostiene la sesión de WhatsApp y manda los mensajes.

Existe porque no queda otra. `neonize.connect()` bloquea el hilo y no vuelve
nunca —ni llamándole a `disconnect()` desde dentro de sus propios callbacks—,
así que no hay forma de abrir la sesión, mandar un mensaje y cerrar. O la tienes
puesta siempre, o no la tienes.

De ahí el reparto de hilos, que es al revés de lo que uno escribiría primero:

- **La sesión va en un hilo secundario.** Si `connect()` se quedara con el hilo
  principal, Python no volvería a mirar una señal en su vida: un `systemctl stop`
  se quedaría esperando el `TimeoutStopSec` para acabar en SIGKILL.
- **El socket se queda con el principal**, igual que en `hotkey/daemon.py`, donde
  el `accept()` sí se interrumpe con SIGTERM y se puede cerrar con educación.

Al final se sale con `os._exit`: el hilo de la sesión está metido en código Go y
no hay manera de despertarlo. Es feo y es lo correcto — lo que había que limpiar
(el socket) ya está limpio para entonces.

Lo que este proceso NO hace, y conviene tenerlo claro: no pregunta. Recibe un
destino y un texto y lo envía, sin más. La confirmación hablada —leerte a quién
va y qué pone antes de que salga— vive en la herramienta, que es quien habla con
el modelo. Aquí solo quedan dos frenos, y son de los que protegen de un error
tonto, no de una mala decisión: el tope de texto y un límite de mensajes por
minuto, para que un bucle no le vacíe la batería del móvil a nadie.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path

from .protocol import (
    MAX_GRUPOS,
    MAX_TEXTO,
    decode,
    encode,
    error,
    partes_destino,
    sesion_path,
    socket_path,
)

log = logging.getLogger("maripepis.whatsapp")

#: Cuánto se espera a que la sesión esté lista antes de contestar que no lo está.
#: Un turno hablado no aguanta más: si WhatsApp no ha conectado en este tiempo,
#: es mejor decirlo que dejar al usuario mirando al vacío.
ESPERA_SESION = 10.0

#: Lo que se espera antes de volver a intentar la sesión, y hasta dónde crece.
#: Empieza corto porque el fallo típico —arrancar antes de que haya DNS— se
#: arregla solo en segundos, y crece porque si WhatsApp está caído de verdad no
#: se gana nada llamando cada cinco segundos toda la tarde.
ESPERA_REINTENTO = 5.0
MAX_ESPERA_REINTENTO = 300.0

#: Freno de mano: mensajes por minuto. Nadie dicta seis wasaps en un minuto; un
#: bucle, sí.
MAX_POR_MINUTO = 6


def vinculado(ruta: str | os.PathLike) -> bool:
    """¿Hay un dispositivo emparejado? Sin preguntárselo a WhatsApp.

    La sesión es una base de whatsmeow, y `whatsmeow_device` con cero filas es
    exactamente «esto no está vinculado». Mirarlo aquí ahorra una conexión entera
    y evita el caso feo: arrancar sin sesión deja al demonio esperando un QR que
    nadie va a escanear, en un servicio donde nadie está mirando.
    """
    ruta = Path(ruta)
    if not ruta.exists():
        return False
    try:
        con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
        try:
            return con.execute("select count(*) from whatsmeow_device").fetchone()[0] > 0
        finally:
            con.close()
    except sqlite3.Error:
        return False


def preparar_casa(ruta: str | os.PathLike) -> Path:
    """Deja el sitio de la sesión con permisos de secreto, que es lo que es.

    El `umask` no es cosmética: la base la crea la parte Go a mitad de `connect()`,
    cuando ya no hay dónde meter un `chmod`, y sin esto sale en 644 — legible por
    cualquiera que entre en el equipo. Y ese fichero es tu WhatsApp entero.
    """
    os.umask(0o077)
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(ruta.parent, 0o700)
    if ruta.exists():
        os.chmod(ruta, 0o600)
    return ruta


class Demonio:
    """La sesión de WhatsApp, viva, y un socket para pedirle cosas."""

    def __init__(self, cfg: dict | None = None) -> None:
        cfg = cfg or {}
        self.socket_path = socket_path(cfg.get("socket"))
        self.sesion = preparar_casa(sesion_path(cfg.get("sesion")))

        self._cli = None                       # el NewClient, cuando conecte
        self._listo = threading.Event()
        self._sock: socket.socket | None = None
        self._running = False
        self._ultimo: dict | None = None       # lo último enviado, para revocarlo
        self._envios: deque[float] = deque(maxlen=MAX_POR_MINUTO)

    # ── la sesión ────────────────────────────────────────────────────────

    def _hilo_sesion(self) -> None:
        """Abre la sesión y se queda ahí dentro para siempre. Literalmente.

        Con un matiz que costó una mañana: `connect()` no vuelve mientras haya
        sesión, pero **sí vuelve —lanzando— si no ha llegado a haberla**, y eso
        pasa en todos los arranques en los que el servicio se levanta antes que
        la red: un `lookup web.whatsapp.com: Temporary failure in name
        resolution` y a la calle. Por eso se reintenta aquí dentro.

        No vale dejar que se caiga el proceso y que systemd lo levante: el que
        se muere es este hilo, no el demonio, así que `Restart=on-failure` nunca
        llega a verlo. El proceso se queda vivo, atendiendo el socket y
        contestando a cada wasap que la sesión no está lista, hasta que alguien
        se da cuenta a mano. Que es exactamente lo que pasó.
        """
        from neonize.client import NewClient       # noqa: PLC0415 - opcional
        from neonize.events import ConnectedEv     # noqa: PLC0415

        espera = ESPERA_REINTENTO
        while self._running:
            # Cliente nuevo en cada vuelta: el anterior viene de un fallo y no hay
            # forma de saber cómo quedó por dentro (la mitad de él es Go). El uuid
            # sale del nombre —la ruta de la sesión—, así que el de ahora ocupa el
            # sitio del de antes en vez de sumarse.
            cliente = NewClient(str(self.sesion))

            @cliente.event(ConnectedEv)
            def _(cli, _ev) -> None:               # noqa: ANN001
                self._cli = cli
                self._listo.set()
                preparar_casa(self.sesion)         # la base ya existe: 600
                try:
                    yo = cli.get_me()
                    log.info("WhatsApp conectado como %s (+%s).", yo.PushName, yo.JID.User)
                except Exception:                  # noqa: BLE001
                    log.info("WhatsApp conectado.")

            try:
                cliente.connect()                  # no vuelve mientras haya sesión
                motivo = "se ha cerrado"
            except Exception as e:                 # noqa: BLE001
                motivo = str(e)

            # Si llegó a conectar, el reloj vuelve a cero: una sesión que aguantó
            # seis horas y se cortó no es el caso del que crece la espera.
            hubo_sesion = self._listo.is_set()
            self._listo.clear()
            self._cli = None
            if not self._running:
                break

            log.error("La sesión de WhatsApp se ha caído: %s", motivo)
            if hubo_sesion:
                espera = ESPERA_REINTENTO
            log.info("Vuelvo a intentarlo en %.0f s.", espera)
            time.sleep(espera)
            espera = min(espera * 2, MAX_ESPERA_REINTENTO)

    def _sesion_lista(self) -> bool:
        return self._listo.wait(ESPERA_SESION) and self._cli is not None

    # ── órdenes ──────────────────────────────────────────────────────────

    def handle(self, req: dict) -> dict:
        accion = str(req.get("accion") or "")
        if accion == "ping":
            return {"ok": True}
        if accion == "estado":
            return self._estado()
        if accion == "enviar":
            return self._enviar(req)
        if accion == "revocar":
            return self._revocar(req)
        if accion == "grupos":
            return self._grupos(req)
        return error(f"no sé qué es «{accion}»")

    def _estado(self) -> dict:
        """Si la sesión está viva, de quién es. No espera: el estado es ahora."""
        listo = self._listo.is_set() and self._cli is not None
        datos = {"ok": True, "conectado": listo, "vinculado": vinculado(self.sesion)}
        if listo:
            try:
                yo = self._cli.get_me()
                datos |= {"numero": yo.JID.User, "nombre": yo.PushName}
            except Exception as e:                 # noqa: BLE001
                datos |= {"conectado": False, "detalle": str(e)}
        return datos

    def _freno(self) -> str:
        """``""`` si se puede enviar; si no, por qué no."""
        ahora = time.monotonic()
        while self._envios and ahora - self._envios[0] > 60:
            self._envios.popleft()
        if len(self._envios) >= MAX_POR_MINUTO:
            return (f"llevo {MAX_POR_MINUTO} mensajes en un minuto y he parado. "
                    "Si de verdad hacían falta, espera un poco.")
        return ""

    def _enviar(self, req: dict) -> dict:
        texto = str(req.get("texto") or "")
        usuario, servidor = partes_destino(str(req.get("destino") or ""))

        if not usuario:
            return error("ese destino no me cuadra ni como teléfono ni como grupo")
        if not texto.strip():
            return error("no hay texto que mandar")
        if len(texto) > MAX_TEXTO:
            return error(f"el mensaje ocupa {len(texto)} caracteres, y el tope son {MAX_TEXTO}")
        if (freno := self._freno()):
            return error(freno)
        if not self._sesion_lista():
            return error("la sesión de WhatsApp no está lista")

        from neonize.utils.jid import build_jid    # noqa: PLC0415

        try:
            resp = self._cli.send_message(build_jid(usuario, servidor), texto)
        except Exception as e:                     # noqa: BLE001
            log.error("No he podido enviar a %s@%s: %s", usuario, servidor, e)
            return error(f"WhatsApp no lo ha aceptado: {e}")

        self._envios.append(time.monotonic())
        self._ultimo = {"usuario": usuario, "servidor": servidor, "id": resp.ID}
        log.info("Enviado a %s@%s (%d caracteres, id %s).",
                 usuario, servidor, len(texto), resp.ID)
        return {"ok": True, "id": resp.ID, "destino": f"{usuario}@{servidor}"}

    def _revocar(self, req: dict) -> dict:
        """Retira un mensaje («eliminar para todos»). Sin argumentos, el último.

        Esto es lo que sustituye al Enter que antes dabas tú: si el mensaje sale
        solo, lo que te devuelve el control es poder decir «bórralo» acto seguido.
        WhatsApp lo permite un rato nada más, así que el fallo por tiempo es una
        respuesta legítima y hay que contarla tal cual.
        """
        objetivo = self._ultimo
        if req.get("id") and req.get("destino"):
            usuario, servidor = partes_destino(str(req["destino"]))
            if usuario:
                objetivo = {"usuario": usuario, "servidor": servidor, "id": str(req["id"])}
        if not objetivo:
            return error("no tengo constancia de haber enviado nada")
        if not self._sesion_lista():
            return error("la sesión de WhatsApp no está lista")

        from neonize.utils.jid import JIDToNonAD, build_jid    # noqa: PLC0415

        try:
            chat = build_jid(objetivo["usuario"], objetivo["servidor"])
            resp = self._cli.revoke_message(
                chat, JIDToNonAD(self._cli.get_me().JID), objetivo["id"])
        except Exception as e:                     # noqa: BLE001
            log.error("No he podido retirar %s: %s", objetivo["id"], e)
            return error(f"no he podido retirarlo: {e}")

        if objetivo is self._ultimo:
            self._ultimo = None
        log.info("Retirado el mensaje %s.", objetivo["id"])
        return {"ok": True, "id": resp.ID}

    def _grupos(self, req: dict) -> dict:
        """Busca grupos por nombre. Con filtro, siempre.

        Sin filtro devuelve **el recuento y nada más**, y no por pereza: esta
        cuenta está en 269 grupos. Una lista así no es una agenda, y si acabara
        en la descripción de la herramienta viajaría entera al modelo en cada
        frase que se le diga — con el backend de Claude, a la nube. Se apuntan a
        mano los tres o cuatro a los que de verdad se escribe.
        """
        if not self._sesion_lista():
            return error("la sesión de WhatsApp no está lista")
        try:
            todos = self._cli.get_joined_groups()
        except Exception as e:                     # noqa: BLE001
            return error(f"no he podido leer los grupos: {e}")

        filtro = str(req.get("filtro") or "").strip().lower()
        if not filtro:
            return {"ok": True, "total": len(todos), "grupos": [],
                    "detalle": "dime parte del nombre y te digo cuáles encajan"}

        encajan = []
        for g in todos:
            nombre = str(getattr(g.GroupName, "Name", g.GroupName))
            if filtro in nombre.lower():
                encajan.append({"nombre": nombre, "jid": f"{g.JID.User}@{g.JID.Server}"})
        return {"ok": True, "total": len(todos), "grupos": encajan[:MAX_GRUPOS]}

    # ── socket ───────────────────────────────────────────────────────────

    def _ya_hay_demonio(self) -> bool:
        """Sonda: si alguien contesta en el socket, es que ya corre otro."""
        if not os.path.exists(self.socket_path):
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(self.socket_path)
            return True
        except OSError:
            return False                           # socket huérfano de un cierre brusco

    def serve(self) -> int:
        if not vinculado(self.sesion):
            log.error(
                "No hay ninguna sesión de WhatsApp vinculada en %s. "
                "Párame y vincula el móvil: `maripepis-wa vincular`.", self.sesion)
            return 1

        if self._ya_hay_demonio():
            log.error("Ya hay un demonio de WhatsApp en %s.", self.socket_path)
            return 1

        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)
        sock.listen(8)
        self._sock = sock
        self._running = True

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._on_signal)

        # La sesión, a un hilo aparte: el principal se queda para el accept() y
        # las señales. Al revés, un `systemctl stop` acabaría siempre en SIGKILL.
        threading.Thread(target=self._hilo_sesion, name="whatsapp", daemon=True).start()

        log.info("WhatsApp: escucho en %s (sesión en %s).", self.socket_path, self.sesion)
        try:
            self._accept_loop(sock)
        finally:
            self._shutdown()
        return 0

    def _accept_loop(self, sock: socket.socket) -> None:
        while self._running:
            try:
                conn, _ = sock.accept()
            except OSError:
                break                              # el socket se cerró (señal de parada)
            self._serve_conn(conn)

    def _serve_conn(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(ESPERA_SESION + 5)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            conn.sendall(encode(self.handle(decode(buf))))
        except OSError as e:
            log.debug("Conexión perdida: %s", e)
        finally:
            conn.close()

    def _on_signal(self, signum, frame) -> None:   # noqa: ANN001
        log.info("Señal %s: cierro.", signum)
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()                 # desbloquea el accept()
            except OSError:
                pass

    def _shutdown(self) -> None:
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        log.info("WhatsApp: cerrado.")
        # `os._exit` no vacía nada, y con la salida redirigida (el journal, sin ir
        # más lejos) eso se traga justo las líneas que explican por qué se cerró.
        logging.shutdown()
        # El hilo de la sesión está dentro de código Go y no hay quien lo
        # despierte; esperarlo es colgarse. Lo que había que limpiar ya está.
        os._exit(0)


def vincular(cfg: dict | None = None) -> int:
    """Empareja el móvil enseñando un QR. En primer plano, y a solas.

    A solas de verdad: el demonio tiene la base de la sesión abierta, así que
    esto se hace con el servicio parado. Si no, SQLite se queja con un bloqueo
    que no se entiende desde fuera.
    """
    from neonize.client import NewClient           # noqa: PLC0415
    from neonize.events import ConnectedEv         # noqa: PLC0415

    ruta = preparar_casa(sesion_path((cfg or {}).get("sesion")))
    if vinculado(ruta):
        print(f"Ya hay una sesión vinculada en {ruta}.")
        return 0

    print("\nEscanea el QR con el móvil: WhatsApp → Dispositivos vinculados.\n")
    cliente = NewClient(str(ruta))

    @cliente.event(ConnectedEv)
    def _(cli, _ev) -> None:                       # noqa: ANN001
        yo = cli.get_me()
        preparar_casa(ruta)
        print(f"\n✓ Vinculado como {yo.PushName} (+{yo.JID.User})")
        print("  Ya puedes arrancar el servicio: systemctl --user start maripepis-whatsapp")
        os._exit(0)                                # `connect()` no vuelve; ver arriba

    cliente.connect()
    return 1


def main(argv: list[str] | None = None) -> int:
    """`maripepis-whatsappd`: el servicio. Lo arranca systemd, no tú."""
    import argparse                                # noqa: PLC0415

    p = argparse.ArgumentParser(prog="maripepis-whatsappd",
                                description="Sostiene la sesión de WhatsApp y envía los mensajes.")
    p.add_argument("--config", default=None, help="Ruta de config.toml.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        from ..config import load_config           # noqa: PLC0415
        cfg = load_config(args.config).get("tools", {}).get("whatsapp", {})
    except FileNotFoundError:
        cfg = {}

    try:
        return Demonio(cfg).serve()
    except ImportError:
        log.error("Falta `neonize`. Instálalo con: pip install -e '.[whatsapp]'")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
