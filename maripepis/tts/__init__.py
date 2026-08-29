"""Motores de síntesis de voz (TTS) intercambiables tras un contrato común."""

from .base import TTSEngine
from .factory import build_tts

__all__ = ["TTSEngine", "build_tts"]
