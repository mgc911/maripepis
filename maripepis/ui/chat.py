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
* **Escucha por un sitio y habla por otro.** La conexión de `subscribe` es de
  una sola dirección y se queda muda: el demonio no lee de ella, y usa justo eso
  para enterarse de que la ventana se ha cerrado (lo único que puede llegar por
  ahí es el EOF). El switch de motor, que sí manda una orden, abre una conexión
  **aparte y corta** —igual que el cliente de la tecla—, y la cierra. Si el
  demonio se reinicia, la ventana se reengancha sola.
* **El botón de reiniciar no pasa por el demonio.** Va directo a systemd. Tiene
  que ser así: cuando de verdad hace falta reiniciar es cuando el demonio no
  contesta, y pedírselo a él sería justo lo que no funciona.

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

# El vecino de al lado, no el paquete: al ejecutar un fichero por su ruta, Python
# mete su directorio el primero en `sys.path`. Así la ventana sigue sin depender
# de PYTHONPATH ni de cómo esté instalado maripepis.
import marcado  # noqa: E402

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
# Un fichero más largo que esto se abre plegado: cabe de sobra una lista de la
# compra o una nota, y no se te va la conversación de la pantalla por un README.
DOC_PLEGADO_LINEAS = 40
RECONEXION_MIN = 1.0      # espera antes de reintentar la conexión (s)
RECONEXION_MAX = 15.0

# `processing` es transcribir y `thinking` es el LLM: dos esperas distintas, y
# con Claude Code la segunda dura lo suyo. Un estado que no esté aquí se pinta
# tal cual (`· loquesea`), así que una ventana vieja contra un demonio nuevo se
# queda fea pero no se rompe.
ESTADOS = {
    "loading": "⏳ arrancando…",
    "idle": "· en reposo",
    "recording": "🎙️ te escucho…",
    "processing": "✍️ transcribiendo…",
    "thinking": "🧠 pensando…",
    "speaking": "🗣️ hablando…",
}
SIN_CONEXION = "⚠️ sin demonio"

# El servicio de systemd que hay detrás (packaging/maripepis.service). Reiniciarlo
# desde aquí sale bien porque la ventana se lanza con `uwsm-app`, fuera del cgroup
# del servicio (ver la cabecera de `hotkey/window.py`): sin eso, el reinicio se
# llevaría por delante a la propia ventana que lo ha pedido.
UNIDAD = "maripepis.service"
REINICIO_TIMEOUT = 20.0
# Icono del botón. Símbolo del tema, no emoji: al lado de la ✕ de la cabecera,
# el 🔄 sale plano y descolorido (y de otro tamaño).
ICONO_REINICIAR = "view-refresh-symbolic"

# El switch de la cabecera: apagado = local, encendido = nube. Los nombres son
# los del protocolo; lo de al lado es lo que se lee, que «ollama» no le dice nada
# a nadie a las ocho de la mañana.
BACKEND_LOCAL = "ollama"
BACKEND_NUBE = "claude"
ROTULOS = {BACKEND_LOCAL: "🏠 local", BACKEND_NUBE: "☁️ Claude",
           "claude-code": "☁️ Claude Code"}

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
.mp-motor { font-size: 0.82em; opacity: 0.8; margin-right: 4px; }
.mp-reiniciar { font-size: 1.05em; }
.mp-documento {
  background: @card_bg_color;
  border-radius: 10px;
  padding: 8px 12px;
}
.mp-doc-titulo { font-size: 0.85em; opacity: 0.75; }
.mp-doc-texto { font-size: 0.92em; }
.mp-doc-crudo { font-family: monospace; font-size: 0.85em; }
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


def mandar(ruta: str, orden: dict, timeout: float = 3.0) -> dict:
    """Manda UNA orden al demonio por una conexión propia y devuelve su respuesta.

    Aparte de la de `subscribe` a propósito: aquella es de una dirección y el
    demonio no lee de ella (ver la cabecera del módulo). Aquí se abre, se dice lo
    que hay que decir, se lee la contestación y se cierra, como el cliente de la
    tecla.

    Nunca lanza: esto lo llama un hilo suelto y un fallo de socket no puede
    tumbar la ventana. Lo que devuelve, si algo va mal, es un `ok: False` con el
    motivo, que es lo que se pinta.
    """
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(ruta)
            s.sendall((json.dumps(orden, ensure_ascii=False) + "\n").encode())
            crudo = s.recv(65536).decode("utf-8", "replace").strip()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    try:
        return json.loads(crudo.splitlines()[0]) if crudo else {"ok": False,
                                                                "error": "sin respuesta"}
    except (ValueError, IndexError):
        return {"ok": False, "error": "respuesta ilegible"}


def reiniciar_servicio(unidad: str = UNIDAD,
                       timeout: float = REINICIO_TIMEOUT) -> tuple[bool, str]:
    """Reinicia el demonio por systemd. Devuelve ``(salió_bien, motivo)``.

    Nunca lanza, igual que `mandar`: lo llama un hilo suelto y un fallo aquí no
    puede tumbar la ventana. El motivo se pinta en el hilo, que es donde se mira.

    Se va a `systemctl` y no al socket a propósito (ver la cabecera del módulo).
    Si Maripepis no corre como servicio —a mano, `python -m maripepis --daemon`—
    esto falla y lo dice: es lo honrado, porque tampoco habría nada que reiniciar
    desde aquí.
    """
    if not shutil.which("systemctl"):
        return False, "no encuentro systemctl"
    try:
        fin = subprocess.run(  # noqa: S603 - argumentos propios, sin shell
            ["systemctl", "--user", "restart", unidad],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"systemctl no terminó en {timeout:g}s"
    except OSError as e:
        return False, str(e)
    if fin.returncode == 0:
        return True, ""
    motivo = " ".join((fin.stderr or fin.stdout or "").split())
    return False, motivo or f"systemctl salió con código {fin.returncode}"


def boton_reiniciar() -> Gtk.Button:
    """El botón de reiniciar: icono del tema si lo hay, emoji si no.

    Se pregunta antes en vez de confiar: si el tema de iconos no trae el símbolo,
    GTK pinta el cuadro de «imagen rota», que es peor que el emoji.
    """
    display = Gdk.Display.get_default()
    if display is not None:
        tema = Gtk.IconTheme.get_for_display(display)
        if tema.has_icon(ICONO_REINICIAR):
            return Gtk.Button(icon_name=ICONO_REINICIAR)
    return Gtk.Button(label="🔄")


class Ventana(Gtk.ApplicationWindow):
    """La conversación, en burbujas, y el estado del demonio en la cabecera."""

    def __init__(self, app, *, foco_previo: str = "", socket_path: str = "") -> None:
        super().__init__(application=app, title=TITULO)
        self.set_default_size(520, 760)
        self.foco_previo = foco_previo
        self.socket_path = socket_path

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

        # Reiniciar el demonio de un clic. Es lo que se acaba haciendo a mano en
        # una terminal: cuando se cuelga, y cuando se toca `config.toml` (que solo
        # se lee al arrancar). A la izquierda, que la derecha ya es del switch.
        # No se apaga al perder la conexión —a diferencia del switch— porque sin
        # demonio es justo cuando sirve para algo.
        self.reiniciar = boton_reiniciar()
        self.reiniciar.add_css_class("flat")
        self.reiniciar.add_css_class("mp-reiniciar")
        self.reiniciar.set_tooltip_text(
            f"Reiniciar Maripepis (systemctl --user restart {UNIDAD})")
        self.reiniciar.connect("clicked", self._al_reiniciar)
        cabecera.pack_start(self.reiniciar)

        # El switch de motor: local ⇄ Claude, de un clic. Empieza apagado y sin
        # poder tocarse; lo activa el `hello` del demonio, que es quien sabe en
        # qué motor está de verdad. Así no se puede mandar una orden a ciegas
        # antes de que haya con quién hablar.
        self.motor = Gtk.Label(label=ROTULOS[BACKEND_LOCAL])
        self.motor.add_css_class("mp-motor")
        self.switch = Gtk.Switch()
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.set_tooltip_text("Cambiar entre el modelo local y Claude")
        self.switch.set_sensitive(False)
        self._switch_id = self.switch.connect("notify::active", self._al_cambiar_motor)
        caja_motor = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        caja_motor.append(self.motor)
        caja_motor.append(self.switch)
        cabecera.pack_end(caja_motor)

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

    def documento(self, ruta: str, contenido: str) -> None:
        """Un fichero recién escrito, plegable, con el Markdown ya pintado.

        Va del lado de Maripepis y en su propia tarjeta, no en una burbuja: no es
        algo que haya dicho, es algo que ha dejado en el disco. El título de la
        cabecera es lo que queda cuando se pliega, así que lleva el nombre y el
        tamaño: plegado tiene que seguir diciendo qué hay ahí dentro.
        """
        lineas = contenido.count("\n") + 1
        nombre = ruta.rsplit("/", 1)[-1] or ruta
        titulo = Gtk.Label(
            label=f"📄 {nombre} · {lineas} línea{'s' if lineas != 1 else ''}",
            xalign=0, tooltip_text=ruta,
        )
        titulo.add_css_class("mp-doc-titulo")

        cuerpo = Gtk.Label(wrap=True, selectable=True, xalign=0)
        cuerpo.set_max_width_chars(46)
        if marcado.parece_markdown(ruta):
            # `use_markup` con marcado roto deja la etiqueta EN BLANCO; por eso
            # `a_pango` promete no devolverlo nunca (ver `ui/marcado.py`).
            cuerpo.set_markup(marcado.a_pango(contenido))
            cuerpo.add_css_class("mp-doc-texto")
        else:
            # Un script o un .txt se enseñan tal cual: interpretar sus `#` y sus
            # `*` como Markdown sería inventarse un formato que no tiene.
            cuerpo.set_text(contenido)
            cuerpo.add_css_class("mp-doc-crudo")

        plegable = Gtk.Expander()
        plegable.set_label_widget(titulo)
        plegable.set_child(cuerpo)
        plegable.set_expanded(lineas <= DOC_PLEGADO_LINEAS)

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caja.add_css_class("mp-documento")
        caja.set_halign(Gtk.Align.START)
        caja.append(plegable)
        self._añadir(caja)

    def suelto(self, texto: str, clase: str) -> None:
        """Una línea centrada y discreta: avisos, errores, separadores."""
        etiqueta = Gtk.Label(label=texto, wrap=True, xalign=0.5)
        etiqueta.add_css_class(clase)
        etiqueta.set_halign(Gtk.Align.CENTER)
        self._añadir(etiqueta)

    def _cerrar_respuesta(self) -> None:
        self._respuesta = None
        self._texto_respuesta = ""

    # ── el switch de motor ───────────────────────────────────────────────

    def _pintar_motor(self, backend: str, etiqueta: str = "") -> None:
        """Deja el switch como está el demonio, SIN mandar nada.

        El `notify::active` salta lo mismo si lo mueve una persona que si lo
        mueve el código, así que aquí se desconecta la señal mientras se coloca:
        sin eso, sincronizar la ventana con el demonio mandaría una orden de
        vuelta, y dos ventanas abiertas se pondrían a rebotarse el motor.
        """
        self.switch.handler_block(self._switch_id)
        self.switch.set_active(backend != BACKEND_LOCAL)
        self.switch.handler_unblock(self._switch_id)
        self.switch.set_sensitive(True)
        self.motor.set_text(ROTULOS.get(backend, f"· {backend}"))
        if etiqueta:
            self.motor.set_tooltip_text(etiqueta)

    def _al_cambiar_motor(self, *_a) -> None:
        """Alguien ha tocado el switch: se pide el cambio y se espera respuesta.

        La petición va en un hilo aparte porque escribe en un socket y lee: aquí
        estamos en el hilo de GTK, y bloquearlo congela la ventana entera —justo
        mientras el demonio arranca un proveedor, que es cuando más tarda—.
        """
        destino = BACKEND_NUBE if self.switch.get_active() else BACKEND_LOCAL
        self.switch.set_sensitive(False)
        self.motor.set_text("· cambiando…")
        threading.Thread(target=self._pedir_motor, args=(destino,), daemon=True).start()

    def _pedir_motor(self, destino: str) -> None:
        """El viaje al demonio, fuera del hilo de GTK."""
        resp = mandar(self.socket_path, {"cmd": "backend", "value": destino})
        GLib.idle_add(self._respuesta_motor, destino, resp)

    def _respuesta_motor(self, destino: str, resp: dict) -> bool:
        """Lo que conteste el demonio manda: si dijo que no, el switch vuelve.

        Que es lo importante de todo esto. El backend `claude` necesita
        ANTHROPIC_API_KEY y sin ella no se puede construir; dejar el switch
        encendido «porque lo has pulsado» sería enseñar un motor que no está en
        uso, y a la primera pregunta contestaría el de siempre.
        """
        if resp.get("ok"):
            self._pintar_motor(resp.get("backend", destino))
        else:
            self._pintar_motor(resp.get("backend", BACKEND_LOCAL))
            self.suelto(f"⚠️ {resp.get('error') or 'no he podido cambiar de motor'}",
                        "mp-error")
        return False

    # ── el botón de reiniciar ────────────────────────────────────────────

    def _al_reiniciar(self, *_a) -> None:
        """Un clic y el demonio vuelve a arrancar; la ventana se reengancha sola.

        El viaje va en un hilo aparte por lo mismo que el del switch: `systemctl`
        bloquea hasta que systemd acepta el trabajo, y el hilo de GTK no se puede
        parar sin congelar la ventana. El botón se apaga mientras tanto, que si no
        cuatro clics nerviosos son cuatro reinicios encadenados.
        """
        self.reiniciar.set_sensitive(False)
        self.estado.set_text("🔄 reiniciando…")
        self._cerrar_respuesta()
        # El demonio arranca con la conversación en blanco: la marca deja claro
        # que lo de arriba ya no es contexto de nadie.
        self.suelto("— reiniciando Maripepis —", "mp-separador")
        threading.Thread(target=self._pedir_reinicio, daemon=True).start()

    def _pedir_reinicio(self) -> None:
        """El viaje a systemd, fuera del hilo de GTK."""
        bien, motivo = reiniciar_servicio()
        GLib.idle_add(self._respuesta_reinicio, bien, motivo)

    def _respuesta_reinicio(self, bien: bool, motivo: str) -> bool:
        """Se vuelve a encender el botón pase lo que pase.

        Si salió bien no se pinta nada: el `hello` de la reconexión ya cuenta el
        estado nuevo, y decir «reiniciado» aquí sería cantar victoria antes de que
        el demonio conteste (que es lo único que lo demuestra).
        """
        self.reiniciar.set_sensitive(True)
        if not bien:
            self.suelto(f"⚠️ no he podido reiniciar: {motivo}", "mp-error")
        return False

    # ── eventos del demonio ──────────────────────────────────────────────

    def al_enlace(self, vivo: bool) -> bool:
        if not vivo:
            self.estado.set_text(SIN_CONEXION)
            self.switch.set_sensitive(False)   # sin demonio no hay a quién pedírselo
            self._cerrar_respuesta()
        return False  # GLib.idle_add: no repetir

    def al_evento(self, ev: dict) -> bool:
        tipo = ev.get("event")
        texto = str(ev.get("text") or "")

        if tipo == "hello":
            self._estado(ev)
            self._pintar_motor(str(ev.get("backend") or BACKEND_LOCAL),
                               str(ev.get("backend_label") or ""))
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
        elif tipo == "document":
            self.documento(str(ev.get("path") or ""), texto)
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
        elif tipo == "backend":
            # Puede venir de otra ventana o de `maripepis-hotkey backend claude`.
            self._pintar_motor(str(ev.get("backend") or BACKEND_LOCAL),
                               str(ev.get("backend_label") or ""))
            if texto:
                self.suelto(texto, "mp-aviso" if ev.get("ok") else "mp-error")
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
            ventana = Ventana(app, foco_previo=foco, socket_path=ruta)
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
