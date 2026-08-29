import socket
import threading

import pytest

from maripepis.hotkey import client
from maripepis.hotkey.protocol import decode, encode


class DemonioFalso:
    """Servidor de un solo uso: guarda lo que recibe y responde lo que le digan."""

    def __init__(self, path: str, respuesta: dict) -> None:
        self.path = path
        self.respuesta = respuesta
        self.recibido = b""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(1)
        self.hilo = threading.Thread(target=self._atender, daemon=True)
        self.hilo.start()

    def _atender(self) -> None:
        conn, _ = self.sock.accept()
        with conn:
            while b"\n" not in self.recibido:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                self.recibido += chunk
            conn.sendall(encode(self.respuesta))

    def cerrar(self) -> None:
        self.hilo.join(timeout=2)
        self.sock.close()


@pytest.fixture
def demonio(tmp_path, monkeypatch):
    creados = []

    def _crear(respuesta):
        d = DemonioFalso(str(tmp_path / "m.sock"), respuesta)
        creados.append(d)
        monkeypatch.setenv("MARIPEPIS_SOCKET", d.path)
        return d

    yield _crear
    for d in creados:
        d.cerrar()


@pytest.fixture(autouse=True)
def sin_notificaciones(monkeypatch):
    """Ningún test debe lanzar notify-send de verdad."""
    lanzados = []
    monkeypatch.setattr(client.subprocess, "run", lambda *a, **k: lanzados.append(a))
    return lanzados


def test_manda_los_bytes_esperados(demonio):
    d = demonio({"ok": True, "state": "recording"})

    assert client.main(["start", "dictation"]) == 0

    assert decode(d.recibido) == {"cmd": "start", "mode": "dictation"}


def test_devuelve_0_si_el_demonio_responde_ok(demonio):
    demonio({"ok": True, "state": "idle"})
    assert client.main(["stop"]) == 0


def test_devuelve_1_si_el_demonio_responde_error(demonio, capsys):
    demonio({"ok": False, "error": "ocupado", "state": "processing"})

    assert client.main(["start"]) == 1
    assert "ocupado" in capsys.readouterr().err


def test_status_imprime_el_estado(demonio, capsys):
    demonio({"ok": True, "state": "recording"})

    assert client.main(["status"]) == 0
    assert capsys.readouterr().out.strip() == "recording"


def test_sin_demonio_no_revienta(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MARIPEPIS_SOCKET", str(tmp_path / "no-existe.sock"))

    assert client.main(["stop"]) == 1
    assert "no responde" in capsys.readouterr().err


def test_start_sin_demonio_avisa(tmp_path, monkeypatch, sin_notificaciones):
    monkeypatch.setenv("MARIPEPIS_SOCKET", str(tmp_path / "no-existe.sock"))

    assert client.main(["start"]) == 1
    assert sin_notificaciones, "un start sin demonio debe avisar por notificación"


def test_stop_sin_demonio_es_silencioso(tmp_path, monkeypatch, sin_notificaciones):
    # Soltar la tecla tras un corte automático es normal: no debe molestar.
    monkeypatch.setenv("MARIPEPIS_SOCKET", str(tmp_path / "no-existe.sock"))

    client.main(["stop"])

    assert sin_notificaciones == []


def test_argumentos_invalidos(capsys):
    assert client.main(["bailar"]) == 2
    assert "uso:" in capsys.readouterr().err
