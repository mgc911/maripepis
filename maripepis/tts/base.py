"""Contrato común de los motores de síntesis de voz."""

from __future__ import annotations

from abc import ABC, abstractmethod


class TTSEngine(ABC):
    """Convierte texto en audio. `synthesize` devuelve un WAV completo (bytes)."""

    @property
    def label(self) -> str:
        return self.__class__.__name__

    def check(self) -> None:
        """Valida los prerequisitos (binarios, modelos). Lanza si algo falta.

        Por defecto no comprueba nada; los motores concretos lo sobrescriben.
        """
        return None

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Devuelve el audio WAV (bytes) correspondiente a `text`."""
        raise NotImplementedError
