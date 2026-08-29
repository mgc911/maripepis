"""Proveedores de LLM intercambiables (Ollama ↔ Claude) tras un contrato común."""

from .base import LLMProvider
from .factory import build_provider

__all__ = ["LLMProvider", "build_provider"]
