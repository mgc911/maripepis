import pytest

from maripepis.tts.factory import build_tts
from maripepis.tts.piper_engine import DEFAULT_SPEED, PiperEngine


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


def test_velocidad_por_defecto_unica():
    # El valor por defecto vive en un solo sitio: DEFAULT_SPEED.
    assert PiperEngine("x.onnx").speed == DEFAULT_SPEED
    assert build_tts({"tts": {"voice": "x.onnx"}}).speed == DEFAULT_SPEED


def test_comando_incluye_la_velocidad():
    e = PiperEngine("voz.onnx", speed=2.0)
    cmd = e._build_command("piper", "/tmp/out.wav")
    assert cmd[cmd.index("--length_scale") + 1] == "0.5000"  # inverso de speed
    assert e._build_command("piper", "/tmp/out.wav", "-")[3] == "--length-scale"
