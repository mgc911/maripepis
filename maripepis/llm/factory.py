"""Selecciona e instancia el proveedor de LLM según `config.toml`."""

from __future__ import annotations

from .base import LLMProvider
from .claude_code_provider import ClaudeCodeProvider
from .claude_provider import ClaudeProvider


def build_provider(cfg: dict) -> LLMProvider:
    """Devuelve el proveedor indicado por ``cfg["llm"]["backend"]``."""
    llm = cfg["llm"]
    backend = llm["backend"]

    if backend == "claude":
        c = llm["claude"]
        return ClaudeProvider(
            model=c.get("model", "claude-opus-4-8"),
            max_tokens=c.get("max_tokens", 1024),
        )

    if backend == "claude-code":
        c = llm.get("claude_code", {})
        return ClaudeCodeProvider(
            cli=c.get("cli", "claude"),
            model=c.get("model", ""),
            tools=c.get("tools", ""),
            permission_mode=c.get("permission_mode", ""),
            safe_mode=c.get("safe_mode", True),
            timeout_s=c.get("timeout_s", 120),
            cwd=c.get("cwd", ""),
        )

    raise ValueError(
        f"backend de LLM desconocido: {backend!r} (usa 'claude' o 'claude-code')"
    )
