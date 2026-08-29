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
