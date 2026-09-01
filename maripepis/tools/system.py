"""Acciones del sistema: abrir el navegador, buscar en internet, abrir apps."""

from __future__ import annotations

import logging
import os
import shutil
import urllib.parse
from pathlib import Path

from .base import Tool
from .busqueda import build_weather_tool, buscar_texto
from .carpetas import resolver
from .ficheros import build_file_tool, build_read_tool
from .hogar import build_home_tools
from .lanzador import lanzar
from .shell import build_shell_tool
from .whatsapp import build_whatsapp_tools

log = logging.getLogger("maripepis.tools")

# Cómo se llaman las cosas hablando. Nadie dice «abre xdg-terminal-exec»: dice
# «abre una terminal». Se resuelve a lo que tenga configurado el sistema, no a un
# emulador concreto, para no ir a rastras del que tenga instalado cada uno.
_ALIAS: dict[str, tuple[str, ...]] = {
    "terminal": ("$TERMINAL", "xdg-terminal-exec"),
    "consola": ("$TERMINAL", "xdg-terminal-exec"),
    "navegador": ("$BROWSER", "xdg-open"),
    "archivos": ("$FILEMANAGER", "nautilus", "thunar", "dolphin"),
    "explorador de archivos": ("$FILEMANAGER", "nautilus", "thunar", "dolphin"),
    "gestor de archivos": ("$FILEMANAGER", "nautilus", "thunar", "dolphin"),
    "editor": ("$EDITOR", "code", "nvim"),
}


def _candidatos(nombre: str) -> list[str]:
    """Nombres de comando a probar para lo que ha pedido el usuario, en orden."""
    clave = " ".join(nombre.strip().lower().split())
    for prefijo in ("una ", "un ", "el ", "la "):  # «abre una terminal»
        clave = clave.removeprefix(prefijo)
    alias = _ALIAS.get(clave)
    if not alias:
        return [nombre.strip()]
    nombres = [os.environ.get(a[1:], "") if a.startswith("$") else a for a in alias]
    return [n for n in nombres if n]


def _data_dirs() -> list[Path]:
    """Directorios XDG donde viven los `.desktop`."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(d) / "applications" for d in [home, *dirs.split(os.pathsep)] if d]


def _desktop_entry(nombre: str) -> str | None:
    """Busca un `.desktop` que corresponda a `nombre`. Devuelve su ID o None."""
    clave = nombre.strip().lower().replace(" ", "-")
    candidatos: list[str] = []
    for d in _data_dirs():
        try:
            entradas = list(d.glob("*.desktop"))
        except OSError:
            continue
        for f in entradas:
            tallo = f.stem.lower()
            if tallo == clave:
                return f.stem              # coincidencia exacta: la mejor
            if clave in tallo.split(".") or tallo.endswith("." + clave):
                candidatos.append(f.stem)  # p.ej. "org.gnome.Nautilus" ← "nautilus"
    return candidatos[0] if candidatos else None


def abrir_navegador(args: dict) -> str:
    url = (args.get("url") or "").strip() or "https://duckduckgo.com"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if shutil.which("xdg-open") is None:
        return "NO he abierto nada: no encuentro `xdg-open` en este equipo."
    lanzar(["xdg-open", url])
    return f"He abierto el navegador en {url}."


def buscar_en_internet(args: dict) -> str:
    """Busca y **devuelve el texto**; solo abre el navegador si no encuentra nada.

    El orden importa. Antes esto abría una pestaña y devolvía «he buscado X»: al
    modelo no le llegaba ningún dato, así que no podía contestar a lo que se le
    había preguntado, y se lo inventaba. Ahora la pestaña es el último recurso, y
    cuando toca abrirla se dice claramente que no se traen los datos, para que no
    se ponga a responder como si los tuviera.
    """
    consulta = (args.get("consulta") or args.get("query") or "").strip()
    if not consulta:
        return "¿Qué quieres que busque?"

    texto = buscar_texto(consulta)
    if texto:
        return (
            f"Esto he encontrado sobre «{consulta}»:\n{texto}\n"
            "Contéstale con esto, resumido en una o dos frases."
        )

    if shutil.which("xdg-open") is None:
        return "NO he buscado nada: no encuentro `xdg-open` en este equipo."
    url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(consulta)
    lanzar(["xdg-open", url])
    return (
        f"NO he podido traerme los resultados de «{consulta}»: te he abierto la "
        "búsqueda en el navegador para que la mire el usuario. Díselo así, y NO "
        "contestes a lo que preguntaba como si lo supieras."
    )


def abrir_aplicacion(args: dict) -> str:
    """Abre una aplicación, **comprobando antes que existe**.

    Nunca dice que ha abierto algo sin haberlo encontrado: si no está instalada,
    lo dice claro para que el asistente no se invente que lo ha hecho.

    Acepta `directorio` porque «abre una terminal en Documentos» es una petición
    de lo más normal, y sin él la ventana sale en el directorio del demonio. Si
    esa carpeta no existe se abre igual, en el *home*: no abrir nada por una
    carpeta equivocada es peor servicio que abrirlo un poco más arriba, siempre
    que se diga.
    """
    nombre = (args.get("nombre") or args.get("app") or "").strip()
    if not nombre:
        return "¿Qué aplicación quieres abrir?"

    pedido = str(args.get("directorio") or args.get("carpeta") or args.get("cwd") or "")
    cwd = resolver(pedido)
    aviso = ""
    if pedido and not cwd.is_dir():
        aviso = f" Ojo: {cwd} no existe, así que la he abierto en tu carpeta personal."
        cwd = Path.home()
    donde = f" en {cwd}" if pedido and not aviso else ""

    for candidato in _candidatos(nombre):
        cmd = shutil.which(candidato)
        if cmd:
            lanzar([cmd], cwd)
            return f"He abierto {nombre}{donde}.{aviso}"

        entrada = _desktop_entry(candidato)
        if entrada and shutil.which("gtk-launch"):
            lanzar(["gtk-launch", entrada], cwd)
            return f"He abierto {nombre}{donde}.{aviso}"

    return (
        f"NO he abierto nada: «{nombre}» no está instalada en este equipo. "
        "Díselo al usuario y ofrécele una alternativa que sí tenga."
    )


def build_default_tools(tools_cfg: dict | None = None) -> list[Tool]:
    """Las acciones disponibles, según la sección `[tools]` de la configuración."""
    tools = [
        Tool(
            name="abrir_navegador",
            description="Abre el navegador web, opcionalmente en una URL concreta.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL a abrir (opcional). Si se omite, abre la página de inicio.",
                    }
                },
                "required": [],
            },
            handler=abrir_navegador,
        ),
        Tool(
            name="buscar_en_internet",
            description=(
                "Busca información en internet y te DEVUELVE el texto de lo que "
                "encuentre, para que puedas contestar con datos de verdad. Úsala "
                "cuando el usuario quiera consultar algo: quién es alguien, qué es "
                "una cosa, cuánto mide, datos de un sitio, etc. "
                "Fíjate en lo que devuelve: si dice que NO ha podido traerse los "
                "resultados, dilo, no contestes como si los supieras. "
                "Para el tiempo no la uses: para eso está consultar_tiempo."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "consulta": {
                        "type": "string",
                        "description": "Lo que hay que buscar.",
                    }
                },
                "required": ["consulta"],
            },
            handler=buscar_en_internet,
        ),
        Tool(
            name="abrir_aplicacion",
            description=(
                "Abre una aplicación con ventana propia, por el nombre de su comando "
                "(p.ej. 'firefox', 'nautilus', 'alacritty') o genérico ('terminal', "
                "'gestor de archivos', 'editor'). Úsala también para abrir una terminal: "
                "no la lances con ejecutar_comando, que se quedaría esperando a que la "
                "cierres. No inventes que la has abierto: fíjate en lo que devuelve, "
                "porque la aplicación puede no estar instalada."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "nombre": {
                        "type": "string",
                        "description": "Nombre del comando o de la aplicación a abrir.",
                    },
                    "directorio": {
                        "type": "string",
                        "description": (
                            "Carpeta en la que abrirla (opcional), p.ej. '~/Documentos'. "
                            "Es lo que hace que una terminal salga en la carpeta pedida."
                        ),
                    },
                },
                "required": ["nombre"],
            },
            handler=abrir_aplicacion,
        ),
        build_weather_tool(),
    ]

    # WhatsApp va aparte de todo lo demás: es la única acción que le llega a otra
    # persona. Ni siquiera esta la envía —deja el mensaje escrito y el enviar lo da
    # el usuario—, pero abrirle el chat a alguien ya es meterse en su conversación,
    # así que tiene su propio interruptor.
    whatsapp_cfg = (tools_cfg or {}).get("whatsapp", {})
    if whatsapp_cfg.get("enabled", True):
        # Una en borrador y dos en envío: ahí redactar y enviar son dos pasos, con
        # el usuario diciendo que sí por en medio.
        tools.extend(build_whatsapp_tools(whatsapp_cfg))

    # Las luces de casa. Interruptor propio porque no todo el mundo tiene un
    # puente Hue, y sin él estas dos herramientas solo sirven para que el modelo
    # las intente y falle: cada una que sobra es contexto gastado en cada frase.
    hogar_cfg = (tools_cfg or {}).get("hogar", {})
    if hogar_cfg.get("enabled", True):
        tools.extend(build_home_tools(hogar_cfg))

    # Leer y escribir ficheros van con la shell: las tres tocan tus cosas, así que
    # las tres se quitan de en medio con `[tools.shell] enabled = false`.
    shell_cfg = (tools_cfg or {}).get("shell", {})
    if shell_cfg.get("enabled", True):
        tools.append(build_file_tool())
        tools.append(build_read_tool())
        tools.append(build_shell_tool(shell_cfg))

    return tools
