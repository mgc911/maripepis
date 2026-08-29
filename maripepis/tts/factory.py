"""Selecciona e instancia el motor TTS según `config.toml`."""

from __future__ import annotations

from .base import TTSEngine
from .piper_engine import PiperEngine


def build_tts(cfg: dict) -> TTSEngine:
    tts = cfg.get("tts", {})
    engine = tts.get("engine", "piper")

    if engine == "piper":
        return PiperEngine(model_path=tts.get("voice"), speed=tts.get("speed", 1.0))

    raise ValueError(f"motor TTS desconocido: {engine!r} (de momento solo 'piper')")
