"""Motor TTS con Piper (invoca el binario `piper`, salida WAV)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .base import TTSEngine

#: Velocidad de la voz: única fuente de verdad para el valor por defecto.
#: Es un multiplicador sobre el ritmo natural del modelo (1.0 = normal,
#: 2.0 = el doble de rápido). Se ajusta desde `config.toml`, en [tts].speed.
DEFAULT_SPEED = 2.1


class PiperEngine(TTSEngine):
    def __init__(self, model_path: str | None, speed: float = DEFAULT_SPEED) -> None:
        self.model_path = Path(model_path) if model_path else None
        self.speed = speed

    @property
    def label(self) -> str:
        name = self.model_path.name if self.model_path else "sin modelo"
        return f"Piper · {name}"

    @property
    def length_scale(self) -> float:
        """Piper razona en «longitud»: cuanto menor, más rápido (inverso de speed)."""
        return 1.0 / self.speed if self.speed > 0 else 1.0

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

    def _build_command(self, piper: str, out_path: str, sep: str = "_") -> list[str]:
        """Comando de Piper. `sep` es el separador de los flags largos, que
        cambia según la versión (guion bajo vs. guion)."""
        return [
            piper,
            "--model", str(self.model_path),
            f"--length{sep}scale", f"{self.length_scale:.4f}",
            f"--output{sep}file", out_path,
        ]

    def synthesize(self, text: str) -> bytes:
        self.check()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = tmp.name

        try:
            piper = self._piper_bin()
            data_in = text.encode("utf-8")
            proc = None
            # Los flags cambian según la versión de Piper (guion vs. guion bajo).
            for sep in ("_", "-"):
                cmd = self._build_command(piper, out_path, sep)
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
