import io
import wave

import pytest

from maripepis.audio.stream import StreamRecorder
from maripepis.audio.vad import VADRecorder

FRAME = 960  # 30 ms a 16 kHz, 16-bit mono


class FakeStdout:
    """Entrega frames de uno en uno y avisa tras cada uno (para parar a mano)."""

    def __init__(self, n_frames: int, on_frame=None) -> None:
        self.left = n_frames
        self.on_frame = on_frame
        self.leidos = 0

    def read(self, n: int) -> bytes:
        if self.left <= 0:
            return b""  # fin del stream
        self.left -= 1
        self.leidos += 1
        if self.on_frame:
            self.on_frame(self.leidos)
        return b"\x01\x00" * (n // 2)


class FakeProc:
    def __init__(self, stdout) -> None:
        self.stdout = stdout
        self.terminado = False

    def terminate(self) -> None:
        self.terminado = True
        self.stdout.left = 0

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        self.terminado = True


class FakeVad:
    """VAD guionizado: `speech[i]` decide si el frame i es voz."""

    def __init__(self, speech) -> None:
        self.speech = list(speech)
        self.i = -1

    def is_speech(self, frame: bytes, rate: int) -> bool:
        self.i += 1
        return self.speech[self.i] if self.i < len(self.speech) else False


def _grabar(monkeypatch, *, speech, n_frames=None, on_frame=None, **kwargs):
    """Monta un StreamRecorder con `arecord` y webrtcvad falsos, y lo ejecuta."""
    import subprocess

    import webrtcvad

    stdout = FakeStdout(n_frames if n_frames is not None else len(speech), on_frame)
    proc = FakeProc(stdout)

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(webrtcvad, "Vad", lambda aggressiveness: FakeVad(speech))
    monkeypatch.setattr(StreamRecorder, "check", lambda self: None)

    rec = StreamRecorder(sample_rate=16000, min_speech_ms=60, **kwargs)
    if on_frame is not None:
        stdout.on_frame = lambda i: on_frame(i, rec)
    rec.start()
    assert rec.wait_finished(5), "el hilo lector no terminó a tiempo"
    return rec, stdout


def test_hereda_el_comando_de_arecord():
    # El contrato con VADRecorder: mismo `arecord -t raw` a stdout.
    assert StreamRecorder(sample_rate=16000)._build_command() == (
        VADRecorder(sample_rate=16000)._build_command()
    )
    assert StreamRecorder(device="hw:0,0")._build_command()[-3:] == ["-D", "hw:0,0", "-"]


def test_para_por_orden(monkeypatch):
    def parar(i, rec):
        if i == 3:
            rec.request_stop()

    rec, _ = _grabar(monkeypatch, speech=[True] * 10, on_frame=parar)

    assert rec.stop_reason == "orden"
    assert not rec.is_recording
    with wave.open(io.BytesIO(rec.harvest())) as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        assert w.getnframes() == 3 * 480  # 3 frames de 480 muestras


def test_para_por_silencio(monkeypatch):
    # 4 frames de voz + 3 de silencio (silence_ms=90 = 3 frames) y aún queda audio.
    rec, stdout = _grabar(
        monkeypatch,
        speech=[True] * 4 + [False] * 10,
        silence_ms=90,
    )

    assert rec.stop_reason == "silencio"
    assert stdout.leidos == 7  # cortó en cuanto acumuló el silencio


def test_el_silencio_inicial_no_corta(monkeypatch):
    # Pulsas y te lo piensas: 6 frames callado no deben cortar la grabación.
    rec, stdout = _grabar(
        monkeypatch,
        speech=[False] * 6 + [True] * 3 + [False] * 10,
        silence_ms=90,
    )

    assert rec.stop_reason == "silencio"
    assert stdout.leidos == 12  # 6 de espera + 3 de voz + 3 de silencio


def test_para_por_tope(monkeypatch):
    rec, stdout = _grabar(monkeypatch, speech=[True] * 20, max_ms=150)  # 5 frames

    assert rec.stop_reason == "tope"
    assert stdout.leidos == 5


def test_termina_si_se_acaba_el_stream(monkeypatch):
    rec, _ = _grabar(monkeypatch, speech=[True] * 4)

    assert rec.stop_reason == "eof"
    assert rec.harvest() is not None


def test_descarta_si_no_hay_voz_suficiente(monkeypatch):
    # Solo ruido: por debajo de min_speech_ms se tira (si no, Whisper alucina).
    rec, _ = _grabar(monkeypatch, speech=[False] * 8)

    assert rec.harvest() is None


def test_cancel_tira_lo_grabado(monkeypatch):
    rec, _ = _grabar(monkeypatch, speech=[True] * 5)
    assert rec.harvest() is not None

    rec.cancel()

    assert rec.harvest() is None
    assert not rec.is_recording


def test_start_dos_veces_no_duplica(monkeypatch):
    # Un `start` con la grabación en curso no debe abrir un segundo arecord.
    import subprocess

    import webrtcvad

    stdout = FakeStdout(0)
    proc = FakeProc(stdout)
    llamadas = []

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (llamadas.append(1), proc)[1])
    monkeypatch.setattr(webrtcvad, "Vad", lambda aggressiveness: FakeVad([]))
    monkeypatch.setattr(StreamRecorder, "check", lambda self: None)

    rec = StreamRecorder()
    rec._done_evt.clear()  # simula "grabando"
    rec.start()

    assert llamadas == []


def test_stop_devuelve_el_wav(monkeypatch):
    def parar(i, rec):
        if i == 4:
            rec.request_stop()

    import subprocess

    import webrtcvad

    stdout = FakeStdout(20)
    proc = FakeProc(stdout)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(webrtcvad, "Vad", lambda aggressiveness: FakeVad([True] * 20))
    monkeypatch.setattr(StreamRecorder, "check", lambda self: None)

    rec = StreamRecorder(min_speech_ms=60)
    stdout.on_frame = lambda i: parar(i, rec)
    rec.start()
    wav = rec.stop(timeout=5)

    assert wav is not None
    assert wav[:4] == b"RIFF"


@pytest.mark.parametrize("d", ["default", "", None])
def test_device_default_se_ignora(d):
    assert "-D" not in StreamRecorder(device=d)._build_command()
