"""Proveedores de LLM intercambiables (Claude por API o por CLI) tras un contrato común."""

from .base import LLMProvider
from .factory import build_provider

__all__ = ["LLMProvider", "build_provider"]
