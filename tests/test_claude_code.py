import json

import pytest

from maripepis.llm.claude_code_provider import (
    NOTA_HISTORIAL,
    ClaudeCodeProvider,
    _delta_de_texto,
)


@pytest.fixture
def provider(monkeypatch):
    """Proveedor con el CLI ya 'encontrado' (los tests no lanzan `claude`)."""
    monkeypatch.setattr(
        "maripepis.llm.claude_code_provider.shutil.which",
        lambda _cli: "/usr/bin/claude",
    )
    return ClaudeCodeProvider(model="sonnet")


def test_sin_cli_avisa_de_como_instalarlo(monkeypatch):
    monkeypatch.setattr(
        "maripepis.llm.claude_code_provider.shutil.which", lambda _cli: None
    )
    with pytest.raises(RuntimeError, match="Claude Code"):
        ClaudeCodeProvider()


def test_un_solo_turno_va_tal_cual(provider):
    prompt, hay_historial = provider.build_prompt([{"role": "user", "content": "hola"}])

    assert prompt == "hola"
    assert hay_historial is False


def test_el_historial_se_aplana_en_el_prompt(provider):
    prompt, hay_historial = provider.build_prompt(
        [
            {"role": "user", "content": "me llamo Manu"},
            {"role": "assistant", "content": "encantado"},
            {"role": "user", "content": "¿cómo me llamo?"},
        ]
    )

    assert hay_historial is True
    assert "Usuario: me llamo Manu" in prompt
    assert "Tú: encantado" in prompt
    # El último mensaje es el que toca contestar, no parte del contexto.
    assert prompt.endswith("Mensaje actual del usuario:\n¿cómo me llamo?")


def test_argumentos_del_cli(provider):
    args = provider.build_args("sé breve")

    assert args[0] == "/usr/bin/claude"
    assert "--print" in args
    # El CLI exige --verbose junto a stream-json; sin él, no arranca.
    assert "--verbose" in args
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert args[args.index("--system-prompt") + 1] == "sé breve"
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--tools") + 1] == ""      # sin herramientas por defecto
    assert "--safe-mode" in args
    assert "--append-system-prompt" not in args       # no hay historial que aclarar


def test_con_historial_anade_la_nota(provider):
    args = provider.build_args("sé breve", con_historial=True)

    assert args[args.index("--append-system-prompt") + 1] == NOTA_HISTORIAL


def test_opciones_vacias_no_ensucian_la_linea(monkeypatch):
    monkeypatch.setattr(
        "maripepis.llm.claude_code_provider.shutil.which", lambda _cli: "/usr/bin/claude"
    )
    args = ClaudeCodeProvider(safe_mode=False).build_args("x")

    assert "--model" not in args
    assert "--permission-mode" not in args
    assert "--safe-mode" not in args


def test_herramientas_propias_de_claude_code(monkeypatch):
    monkeypatch.setattr(
        "maripepis.llm.claude_code_provider.shutil.which", lambda _cli: "/usr/bin/claude"
    )
    args = ClaudeCodeProvider(
        tools="Bash,WebSearch", permission_mode="bypassPermissions"
    ).build_args("x")

    assert args[args.index("--tools") + 1] == "Bash,WebSearch"
    assert args[args.index("--permission-mode") + 1] == "bypassPermissions"


def test_no_acepta_las_herramientas_de_maripepis(provider):
    # El turno mira esta bandera para no intentar pasárselas.
    assert provider.accepts_tools is False


# --------------------------------------------------- lectura del stream-json


def _delta(texto: str) -> dict:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": texto},
        },
    }


def _pensamiento(texto: str) -> dict:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": texto},
        },
    }


def test_solo_saca_el_texto_de_la_respuesta():
    assert _delta_de_texto(_delta("hola")["event"]) == "hola"
    assert _delta_de_texto(_pensamiento("mmm")["event"]) == ""
    assert _delta_de_texto({"type": "message_stop"}) == ""


def _fake_popen(monkeypatch, eventos, codigo=0):
    """Sustituye al CLI por un proceso de mentira que escupe esas líneas JSON."""

    class FakeStdin:
        closed = False

        def write(self, _texto):
            return None

        def close(self):
            self.closed = True

    class FakeStdout:
        def __init__(self, lineas):
            self._lineas = iter(lineas)
            self.closed = False

        def __iter__(self):
            return self._lineas

        def close(self):
            self.closed = True

    class FakeProc:
        def __init__(self, *_a, **_kw):
            self.stdin = FakeStdin()
            self.stdout = FakeStdout([json.dumps(e) + "\n" for e in eventos])
            self.matado = False

        def wait(self, timeout=None):  # noqa: ARG002
            return codigo

        def poll(self):
            return codigo

        def kill(self):
            self.matado = True

    monkeypatch.setattr(
        "maripepis.llm.claude_code_provider.subprocess.Popen", FakeProc
    )


def test_stream_reply_devuelve_los_trozos(monkeypatch, provider):
    _fake_popen(
        monkeypatch,
        [
            {"type": "system", "subtype": "init"},
            _delta("Hola"),
            _pensamiento("ignórame"),
            _delta(", Manu."),
            {"type": "result", "subtype": "success", "is_error": False, "result": "Hola, Manu."},
        ],
    )

    trozos = list(provider.stream_reply("sé breve", [{"role": "user", "content": "hola"}]))

    assert trozos == ["Hola", ", Manu."]


def test_sin_deltas_se_queda_con_el_resultado_final(monkeypatch, provider):
    _fake_popen(
        monkeypatch,
        [{"type": "result", "subtype": "success", "is_error": False, "result": "Listo."}],
    )

    trozos = list(provider.stream_reply("x", [{"role": "user", "content": "hola"}]))

    assert trozos == ["Listo."]


def test_un_resultado_con_error_revienta(monkeypatch, provider):
    _fake_popen(
        monkeypatch,
        [
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "límite de uso alcanzado",
            }
        ],
    )

    with pytest.raises(RuntimeError, match="límite de uso alcanzado"):
        list(provider.stream_reply("x", [{"role": "user", "content": "hola"}]))


def test_si_el_cli_se_va_sin_decir_nada_tambien_revienta(monkeypatch, provider):
    _fake_popen(monkeypatch, [], codigo=1)

    with pytest.raises(RuntimeError, match="código 1"):
        list(provider.stream_reply("x", [{"role": "user", "content": "hola"}]))


def test_la_clave_de_api_no_viaja_al_cli(monkeypatch, provider):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-loquesea")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")

    env = provider._env()

    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
