from maripepis.audio.player import AudioPlayer


def test_comando_por_defecto():
    p = AudioPlayer(device=None)
    assert p._build_command() == ["aplay", "-q", "-"]


def test_comando_con_device():
    p = AudioPlayer(device="hw:0,0")
    assert p._build_command() == ["aplay", "-q", "-D", "hw:0,0", "-"]


def test_device_default_se_ignora():
    for d in ("default", "", None):
        p = AudioPlayer(device=d)
        assert p._build_command() == ["aplay", "-q", "-"]
