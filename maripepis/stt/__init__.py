"""Motores de transcripción de voz (STT) intercambiables tras un contrato común."""

from .base import STTEngine
from .factory import build_stt

__all__ = ["STTEngine", "build_stt"]
