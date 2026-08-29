"""Contrato común de los motores de transcripción (voz → texto)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class STTEngine(ABC):
    """Convierte audio WAV (bytes) en texto."""

    @property
    def label(self) -> str:
        return self.__class__.__name__

    def check(self) -> None:
        """Valida los prerequisitos (paquetes, modelos). Lanza si algo falta."""
        return None

    @abstractmethod
    def transcribe(self, wav_bytes: bytes) -> str:
        """Devuelve el texto reconocido en el audio WAV `wav_bytes`."""
        raise NotImplementedError
