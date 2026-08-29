import logging

from maripepis.audio.speech import SpeechWorker


class FakeTTS:
    label = "Fake · voz"

    def synthesize(self, text: str) -> bytes:
        return text.encode("utf-8")


class FakePlayer:
    def __init__(self) -> None:
        self.played: list[str] = []

    def play_wav_bytes(self, data: bytes) -> None:
        self.played.append(data.decode("utf-8"))

    def stop(self) -> None:
        pass


def _worker():
    return SpeechWorker(FakeTTS(), FakePlayer(), logging.getLogger("test"))


def test_reproduce_en_orden():
    w = _worker()
    for t in ("uno", "dos", "tres"):
        w.say(t)
    w.wait()
    assert w.player.played == ["uno", "dos", "tres"]
    w.close()


def test_label_delega_en_tts():
    w = _worker()
    assert w.label == "Fake · voz"
    w.close()


def test_say_ignora_vacios():
    w = _worker()
    w.say("")
    w.say("   ")
    w.wait()
    assert w.player.played == []
    w.close()


def test_close_termina_el_hilo():
    w = _worker()
    w.close()
    assert not w._thread.is_alive()
