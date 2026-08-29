import json

from maripepis.hotkey.protocol import (
    COMMANDS,
    SUBSCRIBE,
    decode,
    encode,
    event,
    parse_argv,
    socket_path,
)


def test_encode_termina_en_salto_de_linea():
    raw = encode({"cmd": "stop"})
    assert raw.endswith(b"\n")
    assert json.loads(raw) == {"cmd": "stop"}


def test_encode_no_escapa_los_acentos():
    assert "adiós".encode("utf-8") in encode({"texto": "adiós"})


def test_encode_decode_ida_y_vuelta():
    req = {"cmd": "start", "mode": "dictation"}
    assert decode(encode(req)) == req


def test_decode_json_invalido_no_revienta():
    assert decode(b"{esto no es json") == {"cmd": "?"}
    assert decode(b"") == {"cmd": "?"}
    assert decode(b"[1, 2, 3]") == {"cmd": "?"}  # JSON válido pero no es un objeto


def test_socket_path_usa_xdg_runtime_dir(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.delenv("MARIPEPIS_SOCKET", raising=False)
    assert socket_path() == "/run/user/1000/maripepis.sock"


def test_socket_path_respeta_la_variable(monkeypatch):
    monkeypatch.setenv("MARIPEPIS_SOCKET", "/tmp/otro.sock")
    assert socket_path() == "/tmp/otro.sock"


def test_socket_path_de_config_manda(monkeypatch):
    monkeypatch.setenv("MARIPEPIS_SOCKET", "/tmp/otro.sock")
    assert socket_path("/tmp/config.sock") == "/tmp/config.sock"


def test_parse_argv_start_sin_modo_es_asistente():
    assert parse_argv(["start"]) == {"cmd": "start", "mode": "assistant"}


def test_parse_argv_start_con_modo():
    assert parse_argv(["start", "dictation"]) == {"cmd": "start", "mode": "dictation"}


def test_parse_argv_stop_no_lleva_modo():
    # El demonio recuerda el modo del start; mandarlo aquí solo daría problemas.
    assert parse_argv(["stop", "dictation"]) == {"cmd": "stop"}


def test_parse_argv_verbos_sueltos():
    for cmd in ("stop", "cancel", "status", "ping"):
        assert parse_argv([cmd]) == {"cmd": cmd}


def test_parse_argv_rechaza_lo_desconocido():
    assert parse_argv([]) == {"cmd": "?"}
    assert parse_argv(["bailar"]) == {"cmd": "?"}
    assert parse_argv(["start", "cantar"]) == {"cmd": "?"}


# ── eventos para la ventana de chat ──────────────────────────────────────

def test_event_pone_el_tipo_en_la_clave_event():
    assert event("user", text="hola") == {"event": "user", "text": "hola"}


def test_event_viaja_por_el_mismo_encode():
    assert decode(encode(event("reply", text="qué tal"))) == {
        "event": "reply", "text": "qué tal"
    }


def test_subscribe_no_es_una_orden_del_cliente():
    # La manda la ventana por el socket, no `maripepis-hotkey`: si colara aquí,
    # una pulsación dejaría al compositor esperando eventos para siempre.
    assert SUBSCRIBE not in COMMANDS
    assert parse_argv([SUBSCRIBE]) == {"cmd": "?"}
