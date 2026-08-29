"""Captura manos libres con detección de actividad de voz (VAD).

Lee audio en streaming desde `arecord` y usa webrtcvad para decidir, frame a
frame, cuándo empieza y termina una intervención: corta tras N ms de silencio.
Devuelve el WAV de la frase, con la misma interfaz que el recorder push-to-talk.
"""

from __future__ import annotations

import collections
import io
import shutil
import subprocess
import wave


class VADRecorder:
    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        aggressiveness: int = 2,
        silence_ms: int = 800,
        max_utterance_ms: int = 15000,
        preroll_ms: int = 300,
        trigger_ms: int = 90,
        min_speech_ms: int = 300,
        device: str | None = None,
        command: str = "arecord",
    ) -> None:
        if frame_ms not in (10, 20, 30):
            raise ValueError("frame_ms debe ser 10, 20 o 30 (requisito de webrtcvad)")
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.aggressiveness = aggressiveness
        self.silence_ms = silence_ms
        self.max_utterance_ms = max_utterance_ms
        self.preroll_ms = preroll_ms
        self.trigger_ms = trigger_ms          # habla sostenida para dar por iniciada la frase
        self.min_speech_ms = min_speech_ms    # voz real mínima; si no, se descarta (ruido)
        self.device = None if device in (None, "", "default") else device
        self.command = command

    @property
    def frame_bytes(self) -> int:
        # muestras por frame * 2 bytes (16-bit)
        return int(self.sample_rate * self.frame_ms / 1000) * 2

    def is_available(self) -> bool:
        if shutil.which(self.command) is None:
            return False
        try:
            import webrtcvad  # noqa: F401
        except ModuleNotFoundError:
            return False
        return True

    def check(self) -> None:
        if shutil.which(self.command) is None:
            raise RuntimeError(
                f"No encuentro `{self.command}`. Instala alsa-utils."
            )
        try:
            import webrtcvad  # noqa: F401
        except ModuleNotFoundError as e:  # pragma: no cover - depende del entorno
            raise RuntimeError(
                "El manos libres necesita `webrtcvad`. "
                'Instálalo con:  pip install -e ".[audio]"'
            ) from e

    def _build_command(self) -> list[str]:
        cmd = [
            self.command,
            "-q",
            "-t", "raw",      # PCM crudo a stdout
            "-f", "S16_LE",
            "-c", "1",
            "-r", str(self.sample_rate),
        ]
        if self.device:
            cmd += ["-D", self.device]
        cmd += ["-"]
        return cmd

    def _to_wav(self, pcm: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(pcm)
        return buf.getvalue()

    @staticmethod
    def _read_frame(stdout, n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            chunk = stdout.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def record_utterance(self) -> bytes | None:
        """Espera a que hables, graba hasta el silencio y devuelve el WAV.

        Devuelve ``None`` si no se capturó nada (p.ej. el stream terminó).
        Un Ctrl-C durante la espera propaga KeyboardInterrupt (para salir).
        """
        self.check()
        import webrtcvad

        vad = webrtcvad.Vad(self.aggressiveness)
        n = self.frame_bytes
        silence_needed = max(1, int(self.silence_ms / self.frame_ms))
        trigger_needed = max(1, int(self.trigger_ms / self.frame_ms))
        min_speech_frames = max(1, int(self.min_speech_ms / self.frame_ms))
        max_frames = int(self.max_utterance_ms / self.frame_ms)
        preroll = collections.deque(maxlen=max(1, int(self.preroll_ms / self.frame_ms)))

        proc = subprocess.Popen(
            self._build_command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        try:
            voiced: list[bytes] = []
            triggered = False
            num_silence = 0
            consec_speech = 0
            speech_frames = 0

            while True:
                frame = self._read_frame(proc.stdout, n)
                if frame is None:
                    break  # el stream terminó

                speech = vad.is_speech(frame, self.sample_rate)

                if not triggered:
                    preroll.append(frame)
                    if speech:
                        consec_speech += 1
                        if consec_speech >= trigger_needed:  # habla sostenida, no un chasquido
                            triggered = True
                            voiced.extend(preroll)  # incluye el arranque de la palabra
                            preroll.clear()
                            num_silence = 0
                            speech_frames = consec_speech
                    else:
                        consec_speech = 0
                else:
                    voiced.append(frame)
                    if speech:
                        speech_frames += 1
                        num_silence = 0
                    else:
                        num_silence += 1
                    if num_silence >= silence_needed:
                        break
                    if len(voiced) >= max_frames:
                        break

            # Descarta lo que no tenga voz real suficiente (ruido → Whisper alucina).
            if not voiced or speech_frames < min_speech_frames:
                return None
            return self._to_wav(b"".join(voiced))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
