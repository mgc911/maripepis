"""El lanzador de la ventana de chat: qué comando arma y cuándo se rinde."""

import logging

from maripepis.hotkey import window as mod

LOG = logging.getLogger("test")
SOCK = "/run/user/1000/maripepis.sock"


def sin_uwsm(monkeypatch, *, python="/usr/bin/python3"):
    """Deja `which` diciendo que hay python3 pero no uwsm-app."""
    monkeypatch.setattr(mod.shutil, "which",
                        lambda c: python if c in ("python3", python) else None)


def test_command_lanza_el_visor_con_el_socket(monkeypatch):
    sin_uwsm(monkeypatch)
    cmd = mod.command(SOCK)

    assert cmd[0] == "/usr/bin/python3"
    assert cmd[1].endswith("ui/chat.py")
    assert cmd[2:] == ["--socket", SOCK]


def test_command_antepone_uwsm_app(monkeypatch):
    # Sin esto la ventana hereda el cgroup del servicio y se cierra con
    # `systemctl --user restart maripepis`.
    monkeypatch.setattr(mod.shutil, "which",
                        lambda c: f"/usr/bin/{c}" if c in ("python3", "uwsm-app") else None)
    cmd = mod.command(SOCK)

    assert cmd[:2] == ["uwsm-app", "--"]
    assert "--socket" in cmd


def test_command_respeta_el_interprete_configurado(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda c: "/otro/python" if c == "/otro/python" else None)
    assert mod.command(SOCK, "/otro/python")[0] == "/otro/python"


def test_command_sin_interprete_es_none(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda c: None)
    monkeypatch.setattr(mod.os.path, "isfile", lambda p: False)
    assert mod.command(SOCK) is None


def test_command_sin_visor_es_none(monkeypatch, tmp_path):
    sin_uwsm(monkeypatch)
    monkeypatch.setattr(mod, "VISOR", tmp_path / "no-existe.py")
    assert mod.command(SOCK) is None


def test_launch_sin_comando_avisa_y_no_revienta(monkeypatch, caplog):
    monkeypatch.setattr(mod, "command", lambda *a, **k: None)

    with caplog.at_level(logging.WARNING):
        assert mod.launch(SOCK, LOG) is False
    assert "ventana de chat" in caplog.text


def test_launch_arranca_el_proceso(monkeypatch):
    lanzados = []
    monkeypatch.setattr(mod, "command", lambda *a, **k: ["python3", "chat.py"])
    monkeypatch.setattr(mod.subprocess, "Popen", lambda cmd, **k: lanzados.append((cmd, k)))

    assert mod.launch(SOCK, LOG) is True
    cmd, kwargs = lanzados[0]
    assert cmd == ["python3", "chat.py"]
    # Desligada del demonio: si no, se iría con él al recibir la señal de parada.
    assert kwargs["start_new_session"] is True


def test_launch_si_no_arranca_devuelve_false(monkeypatch):
    monkeypatch.setattr(mod, "command", lambda *a, **k: ["python3", "chat.py"])

    def revienta(cmd, **k):
        raise OSError("no hay memoria")

    monkeypatch.setattr(mod.subprocess, "Popen", revienta)
    assert mod.launch(SOCK, LOG) is False
