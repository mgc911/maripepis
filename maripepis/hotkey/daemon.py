"""Demonio de la tecla de hablar: mantiene Whisper caliente y sirve el socket.

Arranca con la sesión (servicio systemd de usuario) y se queda esperando. Cuando
mantienes pulsado ALT+Z, Hyprland lanza el cliente, que manda un ``start`` por el
socket; al soltar, un ``stop``. El demonio graba, transcribe y responde.

El bucle que atiende el socket **nunca trabaja**: solo cambia de estado y
contesta. Todo lo lento (grabar → transcribir → LLM → hablar) va en un único hilo
de turno, así una pulsación siempre obtiene respuesta inmediata.

Por ese mismo socket va la ventana de chat (`ui/chat.py`): manda un ``subscribe``,
se queda enganchada y el demonio le empuja el turno según pasa. Es un espectador,
no un mando: nada de lo que hace la ventana cambia lo que hace el demonio, y si no
hay ninguna abierta, todo funciona igual.
"""

from __future__ import annotations

import os
import select
import signal
import socket
import threading
import time
from pathlib import Path

from ..llm.factory import build_provider
from ..tools import es_fallo, fichero_de_la_llamada, resumen_de_la_llamada
from ..tools.ficheros import para_la_ventana
from ..turn import reply_turn
from . import clipboard, window
from .notify import Notifier
from .protocol import BACKENDS, MODES, SUBSCRIBE, decode, encode, event, socket_path

LOADING = "loading"
IDLE = "idle"
RECORDING = "recording"
PROCESSING = "processing"
# Entre transcribir y hablar hay un hueco que antes no se contaba: el LLM
# trabajando. Claude Code piensa y se va a internet antes de abrir la boca, así
# que son diez segundos largos en los que la ventana juraba estar hablando y no
# se oía nada.
THINKING = "thinking"
SPEAKING = "speaking"

_ASISTENTE, _DICTADO = MODES

# Un visor atascado no puede frenar un turno de voz: si no traga un evento en
# este rato, se le da por muerto y se le quita de la lista.
_VISOR_TIMEOUT = 1.0
# Margen para que GTK arranque y se suscriba, sin abrir dos ventanas por el camino.
_VISOR_ARRANQUE = 8.0


class HotkeyDaemon:
    """Máquina de estados de la tecla de hablar.

    ``LOADING → IDLE ⇄ RECORDING → PROCESSING → SPEAKING → IDLE``

    Separar `PROCESSING` de `SPEAKING` da dos cosas: barge-in por teclado (pulsar
    mientras responde la corta y empieza a escuchar) y que la voz de Piper no se
    cuele en la grabación, porque no hay cancelación de eco.
    """

    def __init__(self, cfg: dict, provider, conversation, logger, *,
                 stt, recorder, speech=None, tools=None, execute=None,
                 notifier=None) -> None:
        self.cfg = cfg
        self.provider = provider
        self.conversation = conversation
        self.logger = logger
        self.stt = stt
        self.recorder = recorder
        self.speech = speech
        self.tools = tools
        self.execute = execute
        # Los comandos que ejecuta el LLM se ven en la ventana de chat: quien
        # escucha la respuesta no tiene por qué creerse el «ya está hecho».
        if execute is not None and hasattr(execute, "on_call"):
            execute.on_call = self._al_ejecutar
        self._engancharse_al_proveedor(provider)

        hk = cfg.get("hotkey", {})
        self.socket_path = socket_path(hk.get("socket") or None)
        self.max_ms = int(hk.get("max_ms", 60000))
        self.context_timeout = float(hk.get("context_timeout_s", 300))
        self.auto_paste = bool(hk.get("auto_paste", False))
        self.paste_delay_ms = int(hk.get("paste_delay_ms", 250))
        self.window_enabled = bool(hk.get("window", True))
        self.window_python = str(hk.get("window_python", "") or "")
        self.notifier = notifier or Notifier(
            logger,
            enabled=bool(hk.get("notify", True)),
            max_chars=int(hk.get("notify_chars", 240)),
        )

        self._lock = threading.RLock()
        self._state = LOADING
        self._mode = _ASISTENTE
        self._turn = 0                 # generación: descarta turnos ya superados
        self._last_turn = 0.0
        self._sock: socket.socket | None = None
        self._running = False
        self._subscribers: list[socket.socket] = []   # ventanas de chat mirando
        self._window_launch = 0.0
        #: Motor en uso. Se puede cambiar en caliente (orden `backend`); de ahí
        #: que se guarde aparte y no se lea de `cfg` cada vez.
        self._backend = str(cfg.get("llm", {}).get("backend", BACKENDS[0]))

    # ── estado ───────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _marcar_hablando(self, turno: int) -> None:
        """Pasa a SPEAKING la primera vez que hay algo que decir; luego, no hace nada.

        Lo llama el hilo del turno en cada token, así que tiene que ser barato e
        idempotente: en cuanto ya está en SPEAKING se sale sin tocar el socket.
        """
        with self._lock:
            if turno != self._turn or self._state == SPEAKING:
                return
            self._state = SPEAKING
        self.broadcast("state", state=SPEAKING)  # fuera del lock: escribe en sockets

    def _finish(self, turn: int) -> None:
        """Vuelve a reposo, salvo que otra pulsación ya haya tomado el relevo."""
        with self._lock:
            if turn != self._turn:
                return
            self._state = IDLE
        self.broadcast("state", state=IDLE)  # fuera del lock: escribe en sockets

    # ── lo que se cuenta fuera: avisos y ventana de chat ─────────────────

    def _aviso(self, texto: str, *, timeout_ms: int | None = None) -> None:
        """Algo sin importancia, por los dos canales: notificación y ventana."""
        self.notifier.show(texto, timeout_ms=timeout_ms)
        self.broadcast("notice", text=texto)

    def _fallo(self, texto: str) -> None:
        """Un fallo, por los dos canales. La ventana lo deja escrito; mako no."""
        self.notifier.error(texto)
        self.broadcast("error", text=texto)

    def _al_ejecutar(self, nombre: str, args: dict, resultado: str) -> None:
        """Una herramienta acaba de correr: que se vea en la ventana de chat."""
        salio = not es_fallo(resultado)
        self.broadcast("tool", name=nombre,
                       text=resumen_de_la_llamada(nombre, args),
                       ok=salio)
        if salio and (destino := fichero_de_la_llamada(nombre, args)):
            self._mostrar_documento(str(destino))

    def _mostrar_documento(self, ruta: str) -> None:
        """Manda a la ventana el fichero que se acaba de escribir, para verlo.

        Que te diga «ya lo tienes en Documentos» y tengas que ir a abrirlo para
        saber qué ha puesto es media respuesta. El texto **no** vuelve al modelo
        (para eso está `leer_fichero`): solo viaja por el socket y se pinta.
        """
        documento = para_la_ventana(Path(ruta))
        if documento is None:
            return
        destino, contenido = documento
        self.broadcast("document", path=destino, text=contenido)

    def _engancharse_al_proveedor(self, provider) -> None:  # noqa: ANN001
        """Pide al proveedor que avise de SUS herramientas, si sabe hacerlo.

        Claude Code trae las suyas y no pasan por `Acciones`: sin esto, el turno
        se va diez segundos a buscar en internet y en la ventana no aparece nada
        —ni el comando, ni un aviso—, que es exactamente lo que se ve cuando algo
        se ha colgado. Se vuelve a llamar al cambiar de motor en caliente.
        """
        if hasattr(provider, "on_tool"):
            provider.on_tool = self._al_ejecutar_el_proveedor
        if hasattr(provider, "on_file"):
            provider.on_file = self._mostrar_documento

    def _al_ejecutar_el_proveedor(self, nombre: str, resumen: str, ok: bool) -> None:
        """Una herramienta del proveedor (no nuestra): misma línea en la ventana.

        El resumen lo trae hecho quien la lanzó: `resumen_de_la_llamada` sabe de
        las herramientas de maripepis, no de las de Claude Code. El fichero que
        deje, si deja alguno, llega aparte y más tarde por `on_file`.
        """
        self.broadcast("tool", name=nombre, text=resumen, ok=ok)

    def broadcast(self, kind: str, **fields) -> None:
        """Empuja un evento a las ventanas de chat conectadas.

        Nunca lanza y nunca se queda colgado: al visor que no traga en
        ``_VISOR_TIMEOUT`` se le da por muerto. Esto lo llama el hilo del turno
        (incluso token a token), así que no puede permitirse frenarlo.
        """
        with self._lock:
            if not self._subscribers:
                return
            visores = list(self._subscribers)
        raw = encode(event(kind, **fields))
        for conn in visores:
            try:
                conn.sendall(raw)
            except OSError as e:
                self.logger.debug("Ventana de chat caída (%s); la quito.", e)
                self._drop_subscriber(conn)

    def _hello(self) -> dict:
        """La bienvenida: estado y conversación en curso, para no abrir en blanco."""
        return event("hello", state=self.state,
                     history=list(self.conversation.messages),
                     backend=self._backend, backend_label=self.provider.label)

    def _add_subscriber(self, conn: socket.socket) -> bool:
        """Apunta un visor nuevo. Devuelve False si se fue antes de la bienvenida."""
        conn.settimeout(_VISOR_TIMEOUT)
        try:
            conn.sendall(encode(self._hello()))
        except OSError as e:
            self.logger.debug("Ventana de chat que no llegó a entrar: %s", e)
            return False
        with self._lock:
            self._subscribers.append(conn)
            cuantas = len(self._subscribers)
        self.logger.info("Ventana de chat conectada (%d).", cuantas)
        return True

    def _drop_subscriber(self, conn: socket.socket) -> None:
        with self._lock:
            if conn in self._subscribers:
                self._subscribers.remove(conn)
        try:
            conn.close()
        except OSError:
            pass

    def _prune_subscribers(self) -> None:
        """Quita los visores que ya no están al otro lado.

        Hace falta mirarlo a propósito: cuando cierras la ventana, la **primera**
        escritura del demonio todavía cuela (se queda en el buffer) y solo falla
        la segunda. Sin esto, la pulsación siguiente creería que hay alguien
        mirando y no abriría ninguna ventana.

        Del socket de un visor no se lee nunca, así que tener algo pendiente solo
        puede ser el EOF de una ventana cerrada.
        """
        with self._lock:
            visores = list(self._subscribers)
        if not visores:
            return
        try:
            cerrados, _, _ = select.select(visores, [], [], 0)
        except (OSError, ValueError, TypeError):
            return  # si no se puede comprobar, mejor no echar a nadie
        for conn in cerrados:
            self.logger.debug("Ventana de chat cerrada; la quito.")
            self._drop_subscriber(conn)

    def _ensure_window(self) -> None:
        """Abre la ventana de chat si no hay ninguna mirando.

        La lista de suscriptores es la única fuente de verdad: si la ventana
        sobrevivió a un reinicio del demonio, se reconecta sola y aquí ya consta.
        El margen de arranque evita abrir cuatro ventanas mientras GTK carga.
        """
        if not self.window_enabled:
            return
        self._prune_subscribers()
        with self._lock:
            if self._subscribers:
                return
            ahora = time.monotonic()
            if ahora - self._window_launch < _VISOR_ARRANQUE:
                return
            self._window_launch = ahora
        window.launch(self.socket_path, self.logger, python=self.window_python)

    # ── órdenes ──────────────────────────────────────────────────────────

    def handle(self, req: dict) -> dict:
        """Atiende una orden. Rápido y sin bloquear: solo mueve el estado."""
        cmd = req.get("cmd", "?")
        if cmd == "start":
            return self._start(req.get("mode", _ASISTENTE))
        if cmd == "stop":
            return self._stop()
        if cmd == "cancel":
            return self._cancel()
        if cmd == "backend":
            return self._cambiar_backend(str(req.get("value", "")))
        if cmd in ("status", "ping"):
            return {"ok": True, "state": self.state, "backend": self._backend}
        return {"ok": False, "error": "orden desconocida", "state": self.state}

    def _cambiar_backend(self, valor: str) -> dict:
        """Cambia de motor en caliente, sin reiniciar ni perder la conversación.

        Tres cosas de las que hay que acordarse aquí:

        * **A mitad de turno no.** Si está grabando o pensando, el proveedor lo
          está usando otro hilo; se dice que no y se queda como estaba.
        * **Si no se puede construir, no se cambia.** El backend `claude` quiere
          `ANTHROPIC_API_KEY`, y sin ella revienta al instanciarse. Antes de tocar
          nada se monta el proveedor nuevo: si falla, el viejo sigue en su sitio y
          el fallo se cuenta. Cambiar y descubrirlo en el turno siguiente sería
          dejar a maripepis muda a mitad de conversación.
        * **El historial se queda.** Es neutro (`Conversation`), así que la
          conversación continúa con el motor nuevo desde donde iba.

        Las herramientas no hace falta apagarlas a mano: `reply_turn` mira
        `provider.accepts_tools`, y con `claude-code` (que trae las suyas) se va
        por la vía sin herramientas él solo.
        """
        if valor not in BACKENDS:
            return {"ok": False, "error": f"motor desconocido: {valor}",
                    "state": self.state, "backend": self._backend}

        with self._lock:
            if self._state not in (IDLE, LOADING):
                return {"ok": False, "error": "ocupado", "state": self._state,
                        "backend": self._backend}
            if valor == self._backend:
                return {"ok": True, "state": self._state, "backend": self._backend}

        cfg = dict(self.cfg)
        cfg["llm"] = {**self.cfg.get("llm", {}), "backend": valor}
        try:
            provider = build_provider(cfg)
        except Exception as e:  # noqa: BLE001 - falta la clave, el CLI, lo que sea
            self.logger.warning("No pude cambiar a %s: %s", valor, e)
            motivo = f"No he podido cambiar a {valor}: {e}"
            self.broadcast("backend", backend=self._backend,
                           backend_label=self.provider.label, ok=False, text=motivo)
            self._fallo(motivo)
            return {"ok": False, "error": str(e), "state": self.state,
                    "backend": self._backend}

        with self._lock:
            self.provider = provider
            self._backend = valor
        self._engancharse_al_proveedor(provider)

        self.logger.info("Motor cambiado a %s (%s).", valor, provider.label)
        self.broadcast("backend", backend=valor, backend_label=provider.label, ok=True,
                       text=f"Ahora hablas con {provider.label}.")
        return {"ok": True, "state": self.state, "backend": valor}

    def _start(self, mode: str) -> dict:
        if mode not in MODES:
            return {"ok": False, "error": f"modo desconocido: {mode}", "state": self.state}

        with self._lock:
            if self._state == LOADING:
                self.notifier.show("⏳ Maripepis está arrancando…", timeout_ms=3000)
                return {"ok": False, "error": "cargando", "state": LOADING}

            if self._state in (RECORDING, PROCESSING):
                # Al soltar SHIFT antes que Z se cuela un `start` de más: ignorarlo.
                self.logger.debug("start durante %s: lo ignoro", self._state)
                if self._state == PROCESSING:
                    self.notifier.show("🧠 Estoy pensando…", timeout_ms=2000)
                return {"ok": False, "error": "ocupado", "state": self._state}

            if self._state in (THINKING, SPEAKING) and self.speech:
                # Barge-in: te callo y te escucho. También mientras piensa, que
                # con Claude Code buscando en internet es medio minuto en el que
                # antes se podía interrumpir y ahora también.
                self.speech.stop()

            self._turn += 1
            turno = self._turn
            self._mode = mode
            self._state = RECORDING

        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            self.logger.error("No pude abrir el micrófono: %s", e)
            self._fallo(f"No pude abrir el micrófono: {e}")
            self._finish(turno)
            return {"ok": False, "error": str(e), "state": self.state}

        titulo = "🎙️ Grabando…" if mode == _ASISTENTE else "🎙️ Dictando…"
        self.notifier.show(titulo, "Suelta la tecla para enviar",
                           timeout_ms=self.max_ms + 5000)

        # La ventana de chat acompaña a la conversación, no al dictado (que va al
        # portapapeles y ni siquiera pasa por el LLM).
        if mode == _ASISTENTE:
            self._ensure_window()
        self.broadcast("state", state=RECORDING, mode=mode)

        threading.Thread(target=self._turn_worker, args=(mode, turno), daemon=True).start()
        return {"ok": True, "state": RECORDING}

    def _stop(self) -> dict:
        """Soltar la tecla. En reposo es un no-op **silencioso**: pasa a menudo,
        porque la grabación puede haberse cortado sola por silencio."""
        with self._lock:
            if self._state == RECORDING:
                self.recorder.request_stop()
            return {"ok": True, "state": self._state}

    def _cancel(self) -> dict:
        with self._lock:
            self._turn += 1  # invalida el turno en curso
            estado, self._state = self._state, IDLE
        if estado == RECORDING:
            self.recorder.cancel()
        elif estado in (THINKING, SPEAKING) and self.speech:
            self.speech.stop()
        self.broadcast("state", state=IDLE)
        return {"ok": True, "state": IDLE}

    # ── el turno ─────────────────────────────────────────────────────────

    def _turn_worker(self, mode: str, turno: int) -> None:
        try:
            self._run_turn(mode, turno)
        except Exception as e:  # noqa: BLE001 - un turno roto no tumba el demonio
            self.logger.error("Fallo en el turno: %s", e)
            self._fallo(str(e))
        finally:
            self._finish(turno)

    def _vigente(self, turno: int) -> bool:
        with self._lock:
            return turno == self._turn

    def _run_turn(self, mode: str, turno: int) -> None:
        # 1. Esperar a que termine la grabación (por orden, silencio o tope).
        if not self.recorder.wait_finished(self.max_ms / 1000 + 5):
            self.logger.error("La grabación no terminó a tiempo; la corto.")
            self.recorder.cancel()
            self._fallo("La grabación se quedó colgada")
            return
        if not self._vigente(turno):
            return
        self.logger.debug("Grabación terminada (%s)", self.recorder.stop_reason)

        wav = self.recorder.harvest()
        with self._lock:
            if turno != self._turn:
                return
            self._state = PROCESSING
        self.broadcast("state", state=PROCESSING, mode=mode)

        if wav is None:
            self._aviso("🤫 No te he oído", timeout_ms=2000)
            return

        # 2. Transcribir.
        self.notifier.show("🧠 Transcribiendo…", timeout_ms=10000)
        try:
            text = self.stt.transcribe(wav)
        except Exception as e:  # noqa: BLE001
            self.logger.error("Fallo transcribiendo: %s", e)
            self._fallo(f"No pude transcribir: {e}")
            return
        if not self._vigente(turno):
            return
        if not text:
            self._aviso("🤫 No te he entendido", timeout_ms=2000)
            return

        # 3. Responder (o copiar, si es dictado).
        if mode == _DICTADO:
            self._dictation_turn(text)
        else:
            self._assistant_turn(text, turno)

    def _dictation_turn(self, text: str) -> None:
        """Dictado: al portapapeles y punto. Ni LLM, ni voz, ni historial."""
        self.logger.info("Dictado: %s", text)
        if clipboard.copy(text, logger=self.logger):
            self.notifier.show("📋 Copiado al portapapeles", text, timeout_ms=8000)
            self.broadcast("notice", text="📋 Copiado al portapapeles")
            if self.auto_paste:
                clipboard.paste(delay_ms=self.paste_delay_ms, logger=self.logger)
        else:
            self._fallo("No pude copiar al portapapeles (falta wl-clipboard)")

    def _assistant_turn(self, text: str, turno: int) -> None:
        """Turno normal: LLM y respuesta hablada, igual que en la REPL."""
        ahora = time.monotonic()
        if self.context_timeout > 0 and self._last_turn:
            if ahora - self._last_turn > self.context_timeout:
                self.logger.info("Contexto caducado; empiezo conversación nueva.")
                self.conversation.reset()
                self.broadcast("reset")
        self._last_turn = ahora

        self.logger.info("Has dicho: %s", text)
        self.notifier.show("🗣️ Has dicho", text, timeout_ms=20000)
        self.broadcast("user", text=text)

        with self._lock:
            if turno != self._turn:
                return
            self._state = THINKING
        self.broadcast("state", state=THINKING)

        def al_token(tok: str) -> None:
            # El primer trozo es la frontera: hasta aquí pensaba, de aquí en
            # adelante habla (Piper empieza con la primera frase completa).
            self._marcar_hablando(turno)
            # La ventana escribe a la vez que Piper habla; sin esto la respuesta
            # aparecería de golpe cuando ya se ha terminado de oír.
            self.broadcast("delta", text=tok)

        reply = reply_turn(
            self.provider, self.conversation, text, self.logger,
            speech=self.speech, tools=self.tools, execute=self.execute,
            on_token=al_token,
        )
        if reply is None:
            self._fallo("El motor LLM no responde")
            return

        # La vía de herramientas (`run_tools_turn`) no da tokens: no ha pasado
        # por `al_token` y el estado seguiría en «pensando» mientras Piper lee la
        # respuesta entera.
        self._marcar_hablando(turno)

        self.logger.info("Maripepis: %s", reply)
        self.notifier.show("🐙 Maripepis", reply, urgency="normal", timeout_ms=8000)
        self.broadcast("reply", text=reply)

        if self.speech and self._vigente(turno):
            self.speech.wait()

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
            return False  # socket huérfano de un cierre brusco

    def serve(self) -> int:
        # Aquí ya se ha ejecutado ensure_cuda_libs(): si el socket se enlazara
        # antes del os.execv, la imagen nueva heredaría el fd y creería que ya
        # hay otro demonio. Por eso el bind vive aquí y no en __init__.
        self.logger.debug("Reejecución CUDA: %s", os.environ.get("MARIPEPIS_CUDA_REEXEC"))
        self.logger.info("Config en uso: %s (cwd %s)",
                         self.cfg.get("_path", "config.toml"), os.getcwd())

        if self._ya_hay_demonio():
            self.logger.error(
                "Ya hay un Maripepis escuchando en %s. "
                "Párala con `systemctl --user stop maripepis`.", self.socket_path
            )
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

        with self._lock:
            self._state = IDLE

        self.logger.info("🐙 Maripepis lista · escucho en %s · %s",
                         self.socket_path, self.stt.label)
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
                break  # el socket se cerró (señal de parada)
            self._serve_conn(conn)

    def _serve_conn(self, conn: socket.socket) -> None:
        """Atiende una conexión: contesta y cierra… salvo que sea un visor.

        La ventana de chat manda ``subscribe`` y se queda: su conexión no se
        cierra aquí, se guarda para irle empujando eventos.
        """
        cerrar = True
        try:
            conn.settimeout(2)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            req = decode(buf)
            if req.get("cmd") == SUBSCRIBE:
                cerrar = not self._add_subscriber(conn)
            else:
                conn.sendall(encode(self.handle(req)))
        except OSError as e:
            self.logger.debug("Conexión perdida: %s", e)
        finally:
            if cerrar:
                conn.close()

    def _on_signal(self, signum, frame) -> None:  # noqa: ANN001
        self.logger.info("Señal %s: cerrando.", signum)
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()  # desbloquea el accept()
            except OSError:
                pass

    def _shutdown(self) -> None:
        # Cerrar los sockets de los visores es lo que les dice que nos vamos: se
        # quedan reintentando y se reenganchan solos cuando el demonio vuelve.
        for conn in list(self._subscribers):
            self._drop_subscriber(conn)
        try:
            self.recorder.cancel()
        except Exception:  # noqa: BLE001
            pass
        if self.speech:
            try:
                self.speech.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
