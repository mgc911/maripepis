"""Reproducción de audio WAV vía ALSA (`aplay`), interrumpible."""

from __future__ import annotations

import shutil
import subprocess
import threading


class AudioPlayer:
    """Reproduce bytes WAV en el altavoz usando `aplay` (paquete alsa-utils)."""

    def __init__(self, device: str | None = None, command: str = "aplay") -> None:
        # "default" o vacío = dispositivo por defecto del sistema (sin -D).
        self.device = None if device in (None, "", "default") else device
        self.command = command
        self._proc: subprocess.Popen | None = None
        self._stopped = False
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return shutil.which(self.command) is not None

    def _build_command(self) -> list[str]:
        cmd = [self.command, "-q"]
        if self.device:
            cmd += ["-D", self.device]
        cmd += ["-"]  # leer el WAV desde stdin (aplay detecta la cabecera RIFF)
        return cmd

    def play_wav_bytes(self, data: bytes) -> None:
        if not self.is_available():
            raise RuntimeError(
                f"No encuentro `{self.command}`. Instala alsa-utils "
                "(Arch: sudo pacman -S alsa-utils)."
            )
        proc = subprocess.Popen(
            self._build_command(), stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
        with self._lock:
            self._proc = proc
            self._stopped = False
        try:
            _out, stderr = proc.communicate(input=data)
        finally:
            with self._lock:
                self._proc = None
                stopped = self._stopped
        # Si lo paramos a propósito (stop/barge-in), no es un error.
        if not stopped and proc.returncode and proc.returncode > 0:
            raise RuntimeError(
                f"{self.command} falló: {stderr.decode('utf-8', 'replace').strip()}"
            )

    def stop(self) -> None:
        """Interrumpe la reproducción en curso, si la hay."""
        with self._lock:
            self._stopped = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
