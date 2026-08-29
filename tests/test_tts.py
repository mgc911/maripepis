import pytest

from maripepis.tts.factory import build_tts
from maripepis.tts.piper_engine import PiperEngine


def _cfg(engine: str = "piper") -> dict:
    return {"tts": {"engine": engine, "voice": "models/piper/x.onnx", "speed": 1.0}}


def test_construye_piper():
    e = build_tts(_cfg())
    assert isinstance(e, PiperEngine)
    assert "Piper" in e.label


def test_engine_desconocido():
    with pytest.raises(ValueError):
        build_tts(_cfg("robovoz"))


def test_synthesize_falla_sin_binario_o_modelo():
    # Sin `piper` instalado (o sin el modelo) debe lanzar un RuntimeError claro.
    e = PiperEngine("no/existe/voz.onnx")
    with pytest.raises(RuntimeError):
        e.synthesize("hola")
