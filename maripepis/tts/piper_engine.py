"""Motor TTS con Piper (invoca el binario `piper`, salida WAV)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .base import TTSEngine


class PiperEngine(TTSEngine):
    def __init__(self, model_path: str | None, speed: float = 1.0) -> None:
        self.model_path = Path(model_path) if model_path else None
        self.speed = speed

    @property
    def label(self) -> str:
        name = self.model_path.name if self.model_path else "sin modelo"
        return f"Piper · {name}"

    def _piper_bin(self) -> str | None:
        """Ruta al binario `piper`, buscando también junto al intérprete.

        Así funciona con `.venv/bin/python -m maripepis` aunque el venv no esté
        activado (su `bin/` no está en PATH, pero `piper` vive ahí).
        """
        found = shutil.which("piper")
        if found:
            return found
        candidate = Path(sys.executable).parent / "piper"
        return str(candidate) if candidate.exists() else None

    def check(self) -> None:
        if self._piper_bin() is None:
            raise RuntimeError(
                'No encuentro el binario `piper`. '
                'Instala el extra de voz:  pip install -e ".[tts]"'
            )
        if self.model_path is None or not self.model_path.exists():
            raise RuntimeError(
                f"No encuentro el modelo de voz: {self.model_path}. "
                "Descárgalo con  scripts/download_models.sh"
            )

    def synthesize(self, text: str) -> bytes:
        self.check()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = tmp.name

        try:
            piper = self._piper_bin()
            data_in = text.encode("utf-8")
            proc = None
            # El flag de salida cambia según la versión de Piper (guion vs. guion bajo).
            for out_flag in ("--output_file", "--output-file"):
                cmd = [piper, "--model", str(self.model_path), out_flag, out_path]
                proc = subprocess.run(cmd, input=data_in, capture_output=True)
                if proc.returncode == 0:
                    break
            else:
                stderr = proc.stderr.decode("utf-8", "replace").strip() if proc else ""
                raise RuntimeError(f"piper falló: {stderr}")

            with open(out_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass
