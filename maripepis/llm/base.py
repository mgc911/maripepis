"""Contrato común que cumplen todos los proveedores de LLM."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator


class LLMProvider(ABC):
    """Recibe un historial neutro y devuelve la respuesta token a token.

    Formato neutro de `messages`: lista de ``{"role": "user"|"assistant",
    "content": str}``, empezando por ``user`` y alternando. El ``system`` va
    aparte, que es como lo quiere Claude y como lo aceptaría cualquier otro.
    """

    #: ¿Se le pueden pasar las herramientas de maripepis? Los proveedores que
    #: traen las suyas propias (Claude Code) lo ponen a False: así el turno no
    #: intenta pasárselas y responde en streaming.
    accepts_tools: bool = True

    @property
    def label(self) -> str:
        """Nombre legible del motor activo (para logs y banner)."""
        return self.__class__.__name__

    @abstractmethod
    def stream_reply(self, system: str, messages: list[dict]) -> Iterator[str]:
        """Genera la respuesta en streaming (fragmentos de texto)."""
        raise NotImplementedError

    def run_tools_turn(self, system, messages, tools, execute) -> str:
        """Turno con herramientas (el LLM decide si llamarlas). Devuelve el texto final.

        `execute(nombre, args) -> str` ejecuta una herramienta y devuelve su resultado.
        Fallback por defecto: sin soporte de herramientas, responde en texto normal.
        """
        return "".join(self.stream_reply(system, messages)).strip()
