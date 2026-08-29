"""Captura por pulsación: arranca y para por orden, con red de seguridad.

A diferencia del push-to-talk de la REPL (`recorder.py`, que bloquea en `input()`)
y del manos libres (`vad.py`, que decide él solo cuándo empiezas a hablar), aquí
el disparador es una tecla global: se graba desde `start()` hasta `request_stop()`.

Como el evento de "tecla soltada" puede perderse (ver ARQUITECTURA), la grabación
también se corta sola tras un rato de silencio o al llegar al tope de duración.
"""

from __future__ import annotations

import threading

from .vad import VADRecorder


class StreamRecorder(VADRecorder):
    """Graba un WAV mono a 16 kHz mientras mantienes pulsada la tecla.

    Hereda de :class:`VADRecorder` para reutilizar el trato con `arecord`
    (`_build_command`, `_read_frame`, `_to_wav`, `frame_bytes`, `check`) y el
    propio VAD, que aquí no dispara la grabación: solo la corta si te callas.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        device: str | None = None,
        frame_ms: int = 30,
        aggressiveness: int = 2,
        silence_ms: int = 2500,
        max_ms: int = 60000,
        min_speech_ms: int = 300,
        command: str = "arecord",
    ) -> None:
        super().__init__(
            sample_rate=sample_rate,
            frame_ms=frame_ms,
            aggressiveness=aggressiveness,
            silence_ms=silence_ms,
            max_utterance_ms=max_ms,
            min_speech_ms=min_speech_ms,
            device=device,
            command=command,
        )
        self.max_ms = max_ms
        self._proc = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._done_evt = threading.Event()
        self._done_evt.set()          # en reposo se considera "terminado"
        self._pcm: list[bytes] = []
        self._speech_frames = 0
        self._stop_reason = ""

    @property
    def is_recording(self) -> bool:
        return not self._done_evt.is_set()

    @property
    def stop_reason(self) -> str:
        """Por qué terminó: 'orden' | 'silencio' | 'tope' | 'eof' | 'error'."""
        return self._stop_reason

    def start(self) -> None:
        """Abre el micro y empieza a acumular audio en segundo plano."""
        if self.is_recording:
            return
        self.check()
        import subprocess

        self._pcm = []
        self._speech_frames = 0
        self._stop_reason = ""
        self._stop_evt.clear()
        self._done_evt.clear()

        proc = subprocess.Popen(
            self._build_command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self._proc = proc
        self._thread = threading.Thread(target=self._read_loop, args=(proc,), daemon=True)
        self._thread.start()

    def _read_loop(self, proc) -> None:  # noqa: ANN001 - subprocess.Popen
        import webrtcvad

        vad = webrtcvad.Vad(self.aggressiveness)
        n = self.frame_bytes
        silence_needed = max(1, int(self.silence_ms / self.frame_ms))
        max_frames = max(1, int(self.max_ms / self.frame_ms))
        num_silence = 0
        armed = False  # el corte por silencio solo cuenta tras oír voz de verdad

        try:
            while not self._stop_evt.is_set():
                frame = self._read_frame(proc.stdout, n)
                if frame is None:
                    self._stop_reason = self._stop_reason or "eof"
                    break

                self._pcm.append(frame)
                if vad.is_speech(frame, self.sample_rate):
                    self._speech_frames += 1
                    num_silence = 0
                    armed = True
                elif armed:
                    num_silence += 1
                    if num_silence >= silence_needed:
                        self._stop_reason = "silencio"
                        break

                if len(self._pcm) >= max_frames:
                    self._stop_reason = "tope"
                    break
            else:
                self._stop_reason = "orden"
        except Exception:  # noqa: BLE001 - el hilo nunca debe tumbar al demonio
            self._stop_reason = "error"
        finally:
            self._close_proc()
            self._done_evt.set()

    def _close_proc(self) -> None:
        """Cierra `arecord`. Idempotente: la llaman el hilo lector y `cancel()`."""
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001 - subprocess.TimeoutExpired y similares
                proc.kill()
                proc.wait()
        except Exception:  # noqa: BLE001 - el proceso ya no está
            pass

    def request_stop(self) -> None:
        """Pide el corte sin bloquear. Se nota como mucho un frame después."""
        self._stop_evt.set()

    def wait_finished(self, timeout: float | None = None) -> bool:
        """Espera a que el hilo lector termine. False si se agotó el tiempo."""
        return self._done_evt.wait(timeout)

    def harvest(self) -> bytes | None:
        """Devuelve el WAV grabado, o ``None`` si no hubo voz real suficiente."""
        min_speech_frames = max(1, int(self.min_speech_ms / self.frame_ms))
        if not self._pcm or self._speech_frames < min_speech_frames:
            return None
        return self._to_wav(b"".join(self._pcm))

    def cancel(self) -> None:
        """Corta la grabación y tira lo capturado."""
        self.request_stop()
        self._close_proc()          # desbloquea el read() del hilo lector
        self._done_evt.wait(2)
        self._pcm = []
        self._speech_frames = 0

    def stop(self, timeout: float = 3.0) -> bytes | None:
        """Atajo: pide el corte, espera y devuelve el WAV (o ``None``)."""
        self.request_stop()
        if not self.wait_finished(timeout):
            self.cancel()
            return None
        return self.harvest()
