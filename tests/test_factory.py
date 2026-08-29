import pytest

from maripepis.llm.claude_code_provider import ClaudeCodeProvider
from maripepis.llm.factory import build_provider
from maripepis.llm.ollama_provider import OllamaProvider


def _cfg(backend: str) -> dict:
    return {
        "llm": {
            "backend": backend,
            "ollama": {
                "host": "http://localhost:11434",
                "model": "llama3.1:8b",
                "temperature": 0.7,
            },
            "claude": {"model": "claude-opus-4-8", "max_tokens": 1024},
            "claude_code": {"model": "sonnet"},
        }
    }


def test_construye_ollama():
    p = build_provider(_cfg("ollama"))
    assert isinstance(p, OllamaProvider)
    assert "Ollama" in p.label
    assert p.model == "llama3.1:8b"


def test_construye_claude_code(monkeypatch):
    # No lanzamos el CLI: basta con que el ejecutable "exista".
    monkeypatch.setattr(
        "maripepis.llm.claude_code_provider.shutil.which", lambda _cli: "/usr/bin/claude"
    )
    p = build_provider(_cfg("claude-code"))

    assert isinstance(p, ClaudeCodeProvider)
    assert p.model == "sonnet"
    assert "suscripción" in p.label
    assert p.accepts_tools is False


def test_backend_desconocido():
    with pytest.raises(ValueError):
        build_provider(_cfg("gpt-9"))


def test_claude_sin_clave_no_llega_a_existir(monkeypatch):
    # El SDK se construye sin clave y no protesta hasta la primera petición: con
    # el switch de la ventana eso significa ponerse en «Claude» y enterarte al
    # hablar, que es cuando no estás mirando la pantalla.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    from maripepis.llm.claude_provider import ClaudeProvider

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        ClaudeProvider()


def test_claude_con_clave_se_construye(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-loquesea")

    from maripepis.llm.claude_provider import ClaudeProvider

    assert "claude" in ClaudeProvider().label.lower()
