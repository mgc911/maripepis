"""Captura de micrófono vía ALSA (`arecord`), en formato apto para Whisper.

Modo push-to-talk (Fase 3): graba hasta que el usuario pulsa Enter. La detección
automática de silencio (VAD) llega en la Fase 4.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile


class AudioRecorder:
    """Graba un WAV mono a 16 kHz (lo que espera Whisper) usando `arecord`."""

    def __init__(
        self,
        sample_rate: int = 16000,
        device: str | None = None,
        command: str = "arecord",
    ) -> None:
        self.sample_rate = sample_rate
        self.device = None if device in (None, "", "default") else device
        self.command = command

    def is_available(self) -> bool:
        return shutil.which(self.command) is not None

    def _build_command(self, out_path: str) -> list[str]:
        cmd = [
            self.command,
            "-q",
            "-t", "wav",
            "-f", "S16_LE",   # PCM 16-bit
            "-c", "1",         # mono
            "-r", str(self.sample_rate),
        ]
        if self.device:
            cmd += ["-D", self.device]
        cmd += [out_path]
        return cmd

    def record_until_enter(self) -> bytes:
        """Graba mientras el usuario no pulse Enter. Devuelve el WAV (bytes)."""
        if not self.is_available():
            raise RuntimeError(
                f"No encuentro `{self.command}`. Instala alsa-utils "
                "(Arch: sudo pacman -S alsa-utils)."
            )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = tmp.name

        try:
            proc = subprocess.Popen(
                self._build_command(out_path), stderr=subprocess.DEVNULL
            )
            try:
                input()  # bloquea hasta que se pulsa Enter
            except (EOFError, KeyboardInterrupt):
                pass

            proc.send_signal(signal.SIGINT)  # arecord cierra el WAV correctamente
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            with open(out_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass
