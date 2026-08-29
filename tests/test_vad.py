import io
import wave

import pytest

from maripepis.audio.vad import VADRecorder


def test_comando_stream_raw():
    r = VADRecorder(sample_rate=16000, device=None)
    assert r._build_command() == [
        "arecord", "-q", "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", "16000", "-",
    ]


def test_comando_con_device():
    r = VADRecorder(device="hw:0,0")
    assert r._build_command()[-3:] == ["-D", "hw:0,0", "-"]


def test_frame_bytes():
    # 30 ms a 16 kHz, 16-bit mono = 480 muestras * 2 = 960 bytes
    assert VADRecorder(sample_rate=16000, frame_ms=30).frame_bytes == 960
    assert VADRecorder(sample_rate=16000, frame_ms=20).frame_bytes == 640


def test_frame_ms_invalido():
    with pytest.raises(ValueError):
        VADRecorder(frame_ms=25)


def test_to_wav_valido():
    r = VADRecorder(sample_rate=16000)
    pcm = b"\x00\x00" * 1600  # 100 ms de silencio
    wav = r._to_wav(pcm)
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == 1600


def test_check_coherente_con_disponibilidad():
    # check() no debe lanzar si arecord + webrtcvad están; debe lanzar si falta algo.
    r = VADRecorder()
    if r.is_available():
        r.check()
    else:
        with pytest.raises(RuntimeError):
            r.check()
