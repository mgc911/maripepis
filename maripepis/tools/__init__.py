"""Herramientas (acciones) que el LLM puede invocar: abrir apps, buscar, ejecutar comandos."""

from .base import Tool, es_fallo
from .ficheros import build_file_tool
from .runner import Acciones, resumen_de_la_llamada
from .shell import build_shell_tool
from .system import build_default_tools

__all__ = [
    "Acciones", "Tool", "build_default_tools", "build_file_tool",
    "build_shell_tool", "es_fallo", "resumen_de_la_llamada",
]
