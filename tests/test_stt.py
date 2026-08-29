import pytest

from maripepis.stt.factory import build_stt
from maripepis.stt.whisper_engine import WhisperEngine


def _cfg(engine: str = "whisper") -> dict:
    return {
        "stt": {
            "engine": engine,
            "model": "small",
            "language": "es",
            "device": "auto",
            "compute_type": "int8",
        }
    }


def test_construye_whisper():
    e = build_stt(_cfg())
    assert isinstance(e, WhisperEngine)
    assert "Whisper" in e.label
    assert e.model_name == "small"


def test_engine_desconocido():
    with pytest.raises(ValueError):
        build_stt(_cfg("vosk"))


def test_check_coherente_con_entorno():
    # check() lanza si falta faster-whisper; no lanza si está instalado.
    e = WhisperEngine()
    try:
        import faster_whisper  # noqa: F401

        disponible = True
    except ModuleNotFoundError:
        disponible = False

    if disponible:
        e.check()
    else:
        with pytest.raises(RuntimeError):
            e.check()
