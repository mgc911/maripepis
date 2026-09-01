"""Herramientas (acciones) que el LLM puede invocar: abrir apps, buscar, ejecutar comandos."""

from .base import Tool, es_fallo
from .busqueda import build_weather_tool, buscar_texto
from .ficheros import build_file_tool, build_read_tool
from .hogar import build_home_tools
from .lanzador import lanzar
from .runner import Acciones, fichero_de_la_llamada, resumen_de_la_llamada
from .shell import build_shell_tool
from .system import build_default_tools
from .whatsapp import build_whatsapp_tools

__all__ = [
    "Acciones", "Tool", "buscar_texto", "build_default_tools", "build_file_tool",
    "build_home_tools", "build_read_tool", "build_shell_tool", "build_weather_tool",
    "build_whatsapp_tools",
    "es_fallo",
    "fichero_de_la_llamada",
    "lanzar",
    "resumen_de_la_llamada",
]
