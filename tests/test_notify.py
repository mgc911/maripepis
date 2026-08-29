import logging

import pytest

from maripepis.hotkey import clipboard, notify
from maripepis.hotkey.notify import Notifier, escape

LOG = logging.getLogger("test")


@pytest.fixture
def notificador(monkeypatch):
    """Notifier con `notify-send` presente pero falso: guarda los argv."""
    lanzados: list[list[str]] = []
    monkeypatch.setattr(notify.shutil, "which", lambda c: "/usr/bin/" + c)
    monkeypatch.setattr(notify.subprocess, "run", lambda cmd, **k: lanzados.append(cmd))
    n = Notifier(LOG)
    n.lanzados = lanzados
    return n


def test_escape_markup():
    assert escape("Tom & Jerry <b>") == "Tom &amp; Jerry &lt;b&gt;"


def test_escapa_el_texto_del_usuario(notificador):
    notificador.show("🗣️ Has dicho", "busca gatos & perros en <google>")

    assert "busca gatos &amp; perros en &lt;google&gt;" in notificador.lanzados[0]


def test_recorta_texto_largo(notificador):
    notificador.max_chars = 20
    notificador.show("🗣️ Has dicho", "a" * 100)

    cuerpo = notificador.lanzados[0][-1]
    assert len(cuerpo) == 20
    assert cuerpo.endswith("…")


def test_colapsa_los_saltos_de_linea(notificador):
    notificador.show("🐙 Maripepis", "una\nlínea\n\ny  otra")

    assert notificador.lanzados[0][-1] == "una línea y otra"


def test_incluye_la_pista_de_sincronia(notificador):
    notificador.show("hola")

    assert "string:x-canonical-private-synchronous:maripepis" in notificador.lanzados[0]


def test_pasa_urgencia_y_timeout(notificador):
    notificador.show("hola", urgency="critical", timeout_ms=6000)

    cmd = notificador.lanzados[0]
    assert cmd[cmd.index("-u") + 1] == "critical"
    assert cmd[cmd.index("-t") + 1] == "6000"


def test_sin_cuerpo_no_manda_argumento_vacio(notificador):
    notificador.show("🎙️ Grabando…")

    assert notificador.lanzados[0][-1] == "🎙️ Grabando…"


def test_error_usa_urgencia_critica(notificador):
    notificador.error("no te he entendido")

    assert "critical" in notificador.lanzados[0]


def test_no_revienta_sin_notify_send(monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda c: None)
    n = Notifier(LOG)

    assert not n.is_available()
    n.show("hola", "mundo")  # no debe lanzar


def test_desactivado_no_busca_el_binario(monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda c: "/usr/bin/notify-send")
    n = Notifier(LOG, enabled=False)

    assert not n.is_available()


def test_copiar_al_portapapeles(monkeypatch):
    llamadas = []
    monkeypatch.setattr(clipboard.shutil, "which", lambda c: "/usr/bin/wl-copy")
    monkeypatch.setattr(clipboard.subprocess, "run",
                        lambda cmd, **k: llamadas.append((cmd, k.get("input"))))

    assert clipboard.copy("hola mundo", logger=LOG)
    assert llamadas == [(["wl-copy"], b"hola mundo")]


def test_copiar_sin_wl_copy_avisa(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda c: None)

    assert not clipboard.copy("hola", logger=LOG)
