from maripepis.audio.recorder import AudioRecorder


def test_comando_por_defecto():
    r = AudioRecorder(sample_rate=16000, device=None)
    assert r._build_command("/tmp/x.wav") == [
        "arecord", "-q", "-t", "wav", "-f", "S16_LE", "-c", "1", "-r", "16000", "/tmp/x.wav",
    ]


def test_comando_con_device_y_sample_rate():
    r = AudioRecorder(sample_rate=48000, device="hw:1,0")
    assert r._build_command("/tmp/x.wav") == [
        "arecord", "-q", "-t", "wav", "-f", "S16_LE", "-c", "1", "-r", "48000",
        "-D", "hw:1,0", "/tmp/x.wav",
    ]


def test_device_default_se_ignora():
    for d in ("default", "", None):
        r = AudioRecorder(device=d)
        cmd = r._build_command("/tmp/x.wav")
        assert "-D" not in cmd
