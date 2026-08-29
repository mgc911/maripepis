#!/usr/bin/env python3
"""Ventana de chat de Maripepis: mira lo que pasa, no manda nada.

La abre el demonio al pulsar ALT+Z y va escribiendo la conversación en el
monitor secundario, para poder leer lo que ha entendido, lo que ha ejecutado y
lo que contesta sin fiarlo todo al oído (y para tener a mano lo que ya dijo).

Tres cosas que conviene entender antes de tocarla:

* **Proceso aparte, con el Python del sistema.** GTK4 llega por `python-gobject`,
  que el `.venv` del proyecto no ve. Además, una ventana atascada no puede
  atascar un turno de voz: aquí no corre nada del demonio.
* **Sin importar `maripepis`.** Solo biblioteca estándar y `gi`, para poder
  lanzarla por ruta sin depender de PYTHONPATH ni de cómo esté instalado el
  paquete. Lo único que comparte con el demonio es el protocolo: una línea JSON
  por evento (documentado en `hotkey/protocol.py`).
* **Solo escucha.** Se suscribe al socket y pinta lo que llega; no manda órdenes.
  Si el demonio se reinicia, se reengancha sola.

A mano, para probarla:

    python3 maripepis/ui/chat.py --socket $XDG_RUNTIME_DIR/maripepis.sock
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402  (gi exige require_version antes)

try:  # libadwaita da los colores del tema (acento, tarjetas) y el modo oscuro
    gi.require_version("Adw", "1")
    from gi.repository import Adw
except (ImportError, ValueError):  # pragma: no cover - degrada a GTK pelado
    Adw = None

# El `app_id` de Wayland (la «class» de `hyprctl clients`) sale de aquí: es a lo
# que se ata la regla de ventana de Hyprland que la manda al monitor secundario.
# Tiene que ser un nombre válido de D-Bus, con puntos: «maripepis-chat» no vale.
APP_ID = "org.maripepis.Chat"
PRGNAME = "maripepis-chat"      # solo el nombre del proceso (ps, journalctl)
TITULO = "Maripepis · chat"

MAX_MENSAJES = 200        # burbujas en pantalla; las viejas se van cayendo
RECONEXION_MIN = 1.0      # espera antes de reintentar la conexión (s)
RECONEXION_MAX = 15.0

ESTADOS = {
    "loading": "⏳ arrancando…",
    "idle": "· en reposo",
    "recording": "🎙️ te escucho…",
    "processing": "🧠 pensando…",
    "speaking": "🗣️ hablando…",
}
SIN_CONEXION = "⚠️ sin demonio"

CSS = """
.mp-mensajes { padding: 14px; }
.mp-quien { font-size: 0.78em; opacity: 0.55; }
.mp-burbuja {
  padding: 9px 13px;
  border-radius: 14px;
}
.mp-yo {
  background: @accent_bg_color;
  color: @accent_fg_color;
  border-bottom-right-radius: 4px;
}
.mp-ella {
  background: @card_bg_color;
  color: @window_fg_color;
  border-bottom-left-radius: 4px;
}
.mp-comando {
  font-family: monospace;
  font-size: 0.82em;
  opacity: 0.7;
  padding: 1px 4px;
}
.mp-comando-mal { color: @error_color; opacity: 0.9; }
.mp-aviso { font-size: 0.85em; opacity: 0.6; font-style: italic; }
.mp-error { color: @error_color; font-size: 0.9em; }
.mp-separador { font-size: 0.8em; opacity: 0.45; }
.mp-estado { font-size: 0.85em; opacity: 0.75; }
"""


# ── conexión con el demonio ──────────────────────────────────────────────


class Eventos(threading.Thread):
    """Sigue el socket del demonio y entrega los eventos al hilo de GTK.

    Reconecta sola con espera creciente: la ventana sobrevive a un
    `systemctl --user restart maripepis` sin que haya que volver a abrirla.
    """

    def __init__(self, path: str, al_evento, al_enlace) -> None:
        super().__init__(daemon=True)
        self.path = path
        self.al_evento = al_evento      # callable(dict), en el hilo de GTK
        self.al_enlace = al_enlace      # callable(bool), en el hilo de GTK
        self._parar = threading.Event()

    def parar(self) -> None:
        self._parar.set()

    def run(self) -> None:
        espera = RECONEXION_MIN
        while not self._parar.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(2)
                    s.connect(self.path)
                    s.sendall(b'{"cmd": "subscribe"}\n')
                    s.settimeout(None)   # a partir de aquí se espera sin prisa
                    GLib.idle_add(self.al_enlace, True)
                    espera = RECONEXION_MIN
                    self._leer(s)
            except OSError:
                pass
            GLib.idle_add(self.al_enlace, False)
            if self._parar.wait(espera):
                return
            espera = min(espera * 2, RECONEXION_MAX)

    def _leer(self, s: socket.socket) -> None:
        """Lee líneas JSON hasta que el demonio cierre."""
        buf = b""
        while not self._parar.is_set():
            trozo = s.recv(65536)
            if not trozo:
                return                       # el demonio se ha ido
            buf += trozo
            while b"\n" in buf:
                linea, buf = buf.split(b"\n", 1)
                if not linea.strip():
                    continue
                try:
                    ev = json.loads(linea)
                except ValueError:
                    continue                 # línea rota: ni caso
                if isinstance(ev, dict):
                    GLib.idle_add(self.al_evento, ev)


# ── Hyprland ─────────────────────────────────────────────────────────────


def monitor_enfocado() -> str:
    """El monitor con el foco ahora mismo (vacío si no hay Hyprland)."""
    if not shutil.which("hyprctl"):
        return ""
    try:
        salida = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True,
                                text=True, timeout=2).stdout
        for m in json.loads(salida):
            if m.get("focused"):
                return str(m.get("name") or "")
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    return ""


def devolver_foco(monitor: str) -> None:
    """Devuelve el foco al monitor donde estaba trabajando el usuario.

    Aunque la regla de Hyprland lleve `no_initial_focus`, abrir una ventana en el
    otro monitor mueve el **monitor activo** y deja el teclado sin ventana
    enfocada. En Lua, que es lo que acepta el `dispatch` de esta configuración.
    """
    try:
        subprocess.run(
            ["hyprctl", "dispatch", f'hl.dsp.focus({{ monitor = "{monitor}" }})'],
            check=False, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass


# ── la ventana ───────────────────────────────────────────────────────────


class Ventana(Gtk.ApplicationWindow):
    """La conversación, en burbujas, y el estado del demonio en la cabecera."""

    def __init__(self, app, *, foco_previo: str = "") -> None:
        super().__init__(application=app, title=TITULO)
        self.set_default_size(520, 760)
        self.foco_previo = foco_previo

        self._respuesta: Gtk.Label | None = None   # burbuja que se está escribiendo
        self._texto_respuesta = ""
        self._mensajes = 0
        self._pegado = True                        # ¿seguimos el final del hilo?

        self.estado = Gtk.Label(label=ESTADOS["loading"])
        self.estado.add_css_class("mp-estado")
        titulo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        marca = Gtk.Label(label="🐙 Maripepis")
        marca.add_css_class("title")
        titulo.append(marca)
        titulo.append(self.estado)

        cabecera = Gtk.HeaderBar()
        cabecera.set_title_widget(titulo)
        self.set_titlebar(cabecera)

        self.hilo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.hilo.add_css_class("mp-mensajes")
        self.scroll = Gtk.ScrolledWindow(vexpand=True)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_child(self.hilo)
        self.set_child(self.scroll)

        ajuste = self.scroll.get_vadjustment()
        ajuste.connect("value-changed", self._al_mover)
        ajuste.connect("changed", self._al_crecer)

        if self.foco_previo:
            self.connect("map", self._al_aparecer)

    # ── foco ─────────────────────────────────────────────────────────────

    def _al_aparecer(self, *_a) -> None:
        """Devuelve el foco al monitor de antes, en cuanto Hyprland nos coloque.

        Dos intentos porque `map` es «GTK ya ha entregado la superficie», no «el
        compositor ya la ha colocado»: el primero suele bastar y el segundo cubre
        el arranque lento. Repetirlo es inofensivo.
        """
        for retraso in (300, 900):
            GLib.timeout_add(retraso, self._foco_una_vez)

    def _foco_una_vez(self) -> bool:
        devolver_foco(self.foco_previo)
        return False  # GLib.timeout_add: no repetir

    # ── scroll ───────────────────────────────────────────────────────────

    def _al_mover(self, ajuste) -> None:
        """Si el usuario sube a releer, se deja de seguir el final."""
        fin = ajuste.get_upper() - ajuste.get_page_size()
        self._pegado = ajuste.get_value() >= fin - 40

    def _al_crecer(self, ajuste) -> None:
        if self._pegado:
            ajuste.set_value(ajuste.get_upper() - ajuste.get_page_size())

    # ── pintar ───────────────────────────────────────────────────────────

    def _añadir(self, widget: Gtk.Widget) -> None:
        self.hilo.append(widget)
        self._mensajes += 1
        while self._mensajes > MAX_MENSAJES:
            viejo = self.hilo.get_first_child()
            if viejo is None:
                break
            self.hilo.remove(viejo)
            self._mensajes -= 1

    def burbuja(self, texto: str, *, yo: bool, hora: str | None = None) -> Gtk.Label:
        """Una burbuja de conversación. Devuelve la etiqueta, para poder crecerla.

        `hora=""` deja el rótulo sin hora: lo que viene en la bienvenida se dijo
        antes de abrir la ventana y ponerle la hora de ahora sería mentir.
        """
        fila = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        fila.set_halign(Gtk.Align.END if yo else Gtk.Align.START)

        if hora is None:
            hora = time.strftime("%H:%M")
        nombre = "tú" if yo else "Maripepis"
        quien = Gtk.Label(label=f"{hora} · {nombre}" if hora else nombre)
        quien.add_css_class("mp-quien")
        quien.set_halign(Gtk.Align.END if yo else Gtk.Align.START)

        cuerpo = Gtk.Label(label=texto, wrap=True, selectable=True, xalign=0)
        cuerpo.set_max_width_chars(42)
        cuerpo.add_css_class("mp-burbuja")
        cuerpo.add_css_class("mp-yo" if yo else "mp-ella")

        fila.append(quien)
        fila.append(cuerpo)
        self._añadir(fila)
        return cuerpo

    def comando(self, texto: str, *, salio: bool) -> None:
        """Lo que ha ejecutado: una línea discreta, en monoespaciada.

        Va del lado de Maripepis (es ella quien lo hace) y sin burbuja: es de
        dónde salió la respuesta, no parte de lo que dice.
        """
        etiqueta = Gtk.Label(label=f"{'⚙️' if salio else '⚠️'} {texto}",
                             wrap=True, selectable=True, xalign=0)
        etiqueta.set_max_width_chars(46)
        etiqueta.add_css_class("mp-comando")
        if not salio:
            etiqueta.add_css_class("mp-comando-mal")
        etiqueta.set_halign(Gtk.Align.START)
        self._añadir(etiqueta)

    def suelto(self, texto: str, clase: str) -> None:
        """Una línea centrada y discreta: avisos, errores, separadores."""
        etiqueta = Gtk.Label(label=texto, wrap=True, xalign=0.5)
        etiqueta.add_css_class(clase)
        etiqueta.set_halign(Gtk.Align.CENTER)
        self._añadir(etiqueta)

    def _cerrar_respuesta(self) -> None:
        self._respuesta = None
        self._texto_respuesta = ""

    # ── eventos del demonio ──────────────────────────────────────────────

    def al_enlace(self, vivo: bool) -> bool:
        if not vivo:
            self.estado.set_text(SIN_CONEXION)
            self._cerrar_respuesta()
        return False  # GLib.idle_add: no repetir

    def al_evento(self, ev: dict) -> bool:
        tipo = ev.get("event")
        texto = str(ev.get("text") or "")

        if tipo == "hello":
            self._estado(ev)
            # Solo la primera vez: al reconectar ya está pintada y duplicarla
            # sería contar la conversación dos veces.
            if self._mensajes == 0:
                for m in ev.get("history") or []:
                    if m.get("content"):
                        self.burbuja(str(m["content"]), yo=m.get("role") == "user",
                                     hora="")
        elif tipo == "state":
            self._estado(ev)
        elif tipo == "user":
            self._cerrar_respuesta()
            self.burbuja(texto, yo=True)
        elif tipo == "tool":
            self.comando(texto, salio=bool(ev.get("ok", True)))
        elif tipo == "delta":
            if self._respuesta is None:
                self._respuesta = self.burbuja("", yo=False)
            self._texto_respuesta += texto
            self._respuesta.set_text(self._texto_respuesta)
        elif tipo == "reply":
            # La definitiva manda: la de los `delta` puede quedarse corta (las
            # herramientas no van en streaming) o sin el desmentido del final.
            if self._respuesta is None:
                self.burbuja(texto, yo=False)
            else:
                self._respuesta.set_text(texto)
            self._cerrar_respuesta()
        elif tipo == "reset":
            self.suelto("— conversación nueva —", "mp-separador")
        elif tipo == "notice":
            self.suelto(texto, "mp-aviso")
        elif tipo == "error":
            self._cerrar_respuesta()
            self.suelto(f"⚠️ {texto}", "mp-error")
        return False

    def _estado(self, ev: dict) -> None:
        estado = str(ev.get("state") or "")
        rotulo = ESTADOS.get(estado, f"· {estado}")
        if estado == "recording" and ev.get("mode") == "dictation":
            rotulo = "🎙️ dictando…"
        self.estado.set_text(rotulo)


# ── arranque ─────────────────────────────────────────────────────────────


def socket_por_defecto() -> str:
    """La misma ruta que calcula `hotkey/protocol.py`, para poder abrirla a mano."""
    env = os.environ.get("MARIPEPIS_SOCKET")
    if env:
        return os.path.expanduser(env)
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, "maripepis.sock")


def estilo() -> None:
    """Los cuatro colores del chat, encima del tema (que es quien pone la paleta)."""
    proveedor = Gtk.CssProvider()
    if hasattr(proveedor, "load_from_string"):     # GTK ≥ 4.12
        proveedor.load_from_string(CSS)
    else:  # pragma: no cover - GTK viejo
        proveedor.load_from_data(CSS.encode())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), proveedor,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ventana de chat de Maripepis")
    parser.add_argument("--socket", default="", help="socket del demonio")
    parser.add_argument("--restore-focus", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="devolver el foco al monitor de antes al abrirse")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    GLib.set_prgname(PRGNAME)
    GLib.set_application_name("Maripepis")

    # Antes de que exista la ventana: después ya seríamos nosotros los enfocados.
    foco = monitor_enfocado() if args.restore_focus else ""
    ruta = args.socket or socket_por_defecto()

    app = (Adw.Application if Adw else Gtk.Application)(application_id=APP_ID)
    estado: dict[str, object] = {}

    def al_activar(_app) -> None:
        ventana = estado.get("ventana")
        if ventana is None:
            estilo()
            ventana = Ventana(app, foco_previo=foco)
            hilo = Eventos(ruta, ventana.al_evento, ventana.al_enlace)
            hilo.start()
            estado["ventana"], estado["hilo"] = ventana, hilo
        # GTK deduplica la aplicación: si el demonio la lanza otra vez teniéndola
        # abierta, ese proceso no crea nada, solo llega aquí y la trae al frente.
        ventana.present()

    app.connect("activate", al_activar)
    return app.run([sys.argv[0]])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
