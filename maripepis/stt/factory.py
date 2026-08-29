"""Selecciona e instancia el motor STT según `config.toml`."""

from __future__ import annotations

from .base import STTEngine
from .whisper_engine import WhisperEngine


def build_stt(cfg: dict) -> STTEngine:
    stt = cfg.get("stt", {})
    engine = stt.get("engine", "whisper")

    if engine == "whisper":
        return WhisperEngine(
            model=stt.get("model", "small"),
            language=stt.get("language", "es"),
            device=stt.get("device", "auto"),
            compute_type=stt.get("compute_type", "int8"),
            initial_prompt=stt.get("initial_prompt", ""),
            beam_size=stt.get("beam_size", 5),
        )

    raise ValueError(f"motor STT desconocido: {engine!r} (de momento solo 'whisper')")
