"""Definición neutra de una herramienta, convertible al formato de cada proveedor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Contrato de las herramientas: el resultado que se le devuelve al modelo empieza
# por «Hecho» si la acción salió, y por una de estas si no. Suena a apaño, pero
# es lo que permite que el turno sepa lo que ha pasado de verdad sin fiarse de lo
# que el modelo cuente después.
PREFIJOS_FALLO = ("no he ", "no ha ", "no pude ", "he cortado", "error")


def es_fallo(resultado: str) -> bool:
    """¿El resultado de una herramienta dice que la acción NO se hizo?"""
    return (resultado or "").lstrip().lower().startswith(PREFIJOS_FALLO)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema del objeto de argumentos
    handler: Callable[[dict], str]

    def run(self, args: dict) -> str:
        return self.handler(args)

    def to_ollama(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_claude(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }
