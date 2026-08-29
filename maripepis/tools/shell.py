"""Ejecutar órdenes de zsh: lo que el usuario escribiría en una terminal.

Es la herramienta más útil y la más peligrosa de todas: el comando lo decide el
LLM a partir de lo que haya entendido el reconocimiento de voz, así que hay tres
redes debajo — un **veto** de órdenes catastróficas, un **tope de tiempo** y un
**tope de salida** (que además viaja al LLM y acaba dicha en voz alta).

Se ejecuta con `zsh -lc`, una shell de *login*: coge el `PATH` de `/etc/profile`
y `~/.zprofile` (mise, `~/.local/bin`...), pero **no** `~/.zshrc`, que es solo
para sesiones interactivas. Es decir: hay comandos, no alias ni prompt.

Sin terminal detrás (`stdin` a /dev/null): lo que pida datos por teclado —`sudo`,
un editor, un `read`— falla o se corta por tiempo, en vez de quedarse colgado
robándole el teclado a la REPL.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

from .base import Tool
from .carpetas import descripcion as descripcion_carpetas
from .carpetas import resolver, traducir_rutas

log = logging.getLogger("maripepis.shell")

SHELL = "zsh"
TIMEOUT_S = 20
MAX_OUTPUT_CHARS = 2000

# Órdenes que no se ejecutan ni pidiéndolas: un fallo del micrófono o una
# alucinación del modelo no pueden costar el disco. Lo que está aquí es
# irreversible; lo demás (borrar un fichero, apagar el equipo) se permite,
# porque también son cosas que uno pide de verdad. Se puede desactivar con
# `[tools.shell] guard = false`, y a partir de ahí es tu problema.
_VETADOS: tuple[tuple[str, str], ...] = (
    (r"\brm\b(?:\s+-\S+)*\s+(?:/|~|\$HOME|\$\{HOME\}|/home|/home/[^/\s]+)/?\*?\s*(?:$|[;&|])",
     "borrar la raíz del sistema o tu carpeta personal entera"),
    (r"\bmkfs(?:\.\w+)?\b", "formatear un disco"),
    (r"\bdd\b[^;&|]*\bof=\s*/dev/(?:sd|nvme|mmcblk|vd)", "escribir en crudo sobre un disco"),
    (r">\s*/dev/(?:sd|nvme|mmcblk|vd)", "escribir en crudo sobre un disco"),
    (r":\s*\(\s*\)\s*\{.*\|.*&.*\}", "reventar el equipo con una fork bomb"),
    (r"\b(?:curl|wget)\b[^;&]*\|\s*(?:sudo\s+)?(?:ba|z|k|d)?sh\b",
     "ejecutar a ciegas un script descargado de internet"),
    (r"\bchmod\b(?:\s+-\S+)*\s+[0-7]{3,4}\s+/\s*(?:$|[;&|])", "cambiar los permisos de la raíz"),
    (r"\bchown\b(?:\s+-\S+)*\s+\S+\s+/\s*(?:$|[;&|])", "cambiar el dueño de la raíz"),
)

_VETOS = tuple((re.compile(patron, re.IGNORECASE), motivo) for patron, motivo in _VETADOS)


def veto(comando: str) -> str | None:
    """Devuelve el motivo por el que `comando` no debe ejecutarse, o ``None``."""
    limpio = " ".join(comando.split())
    for patron, motivo in _VETOS:
        if patron.search(limpio):
            return motivo
    return None


def directorio_de_trabajo(valor: str) -> Path:
    """Dónde ejecutar: el *home* salvo que se pida otro sitio (y exista).

    `directorio` es una comodidad opcional, y el modelo la rellena casi siempre,
    a menudo con algo que aquí no existe: `$HOME/Desktop`, `~/Downloads`... Si
    eso cancelara el comando —que suele traer rutas absolutas y habría
    funcionado igual—, la petición se quedaría sin hacer por un detalle que al
    usuario ni le va ni le viene. Así que un directorio imposible degrada al
    *home*; no veta.
    """
    destino = resolver(valor)
    if destino.is_dir():
        return destino
    if (valor or "").strip():
        log.info("El directorio %s no existe; ejecuto desde %s.", destino, Path.home())
    return Path.home()


def _recortar(texto: str, tope: int) -> str:
    texto = texto.strip()
    if 0 < tope < len(texto):
        return texto[:tope].rstrip() + "\n[...salida recortada...]"
    return texto


def ejecutar_comando(args: dict, cfg: dict | None = None) -> str:
    """Ejecuta un comando de zsh y devuelve un resumen de lo que ha pasado.

    Nunca dice que ha hecho algo sin comprobarlo: el código de salida y la salida
    del comando van en la respuesta para que el asistente no se lo invente.
    """
    cfg = cfg or {}
    comando = (args.get("comando") or args.get("command") or "").strip()
    if not comando:
        return "¿Qué comando quieres que ejecute?"

    comando, cambios = traducir_rutas(comando)
    if cambios:
        log.info("Rutas traducidas (%s): %s", ", ".join(cambios), comando)

    if cfg.get("guard", True):
        motivo = veto(comando)
        if motivo is not None:
            log.warning("Comando vetado (%s): %s", motivo, comando)
            return (
                f"NO he ejecutado nada: «{comando}» sirve para {motivo}, y eso no lo hago. "
                "Si de verdad lo quieres, escríbelo tú en una terminal."
            )

    shell = shutil.which(SHELL)
    if shell is None:
        return f"NO he ejecutado nada: no encuentro `{SHELL}` en este equipo."

    cwd = directorio_de_trabajo(args.get("directorio") or args.get("cwd") or "")

    timeout = float(cfg.get("timeout_s", TIMEOUT_S) or TIMEOUT_S)
    log.info("Ejecuto en %s: %s", cwd, comando)

    # Sesión propia para poder matar también a los hijos si se pasa de tiempo:
    # `communicate(timeout=...)` solo se lleva por delante al proceso directo.
    proc = subprocess.Popen(
        [shell, "-lc", comando],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        cwd=str(cwd),
        start_new_session=True,
    )
    try:
        salida, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)  # start_new_session ⇒ pid == pgid
        proc.communicate()
        log.warning("Cortado por tiempo (%.0fs): %s", timeout, comando)
        return (
            f"He cortado «{comando}» a los {timeout:.0f} segundos porque tardaba demasiado; "
            "puede que se haya quedado a medias."
        )

    salida = _recortar(salida or "", int(cfg.get("max_output_chars", MAX_OUTPUT_CHARS) or 0))

    if proc.returncode != 0:
        log.warning("Código %d: %s", proc.returncode, comando)
        cola = f" Ha dicho: {salida}" if salida else ""
        # La coletilla va dirigida al modelo: un 7B, ante un fallo, tiende a
        # anunciar que lo arreglará y quedarse ahí, sin reintentar nada.
        return (
            f"NO ha salido bien: «{comando}» ha fallado con código {proc.returncode}.{cola}"
            " Corrige el comando y vuelve a llamar a la herramienta; no des por hecho"
            " que ha funcionado."
        )
    if not salida:
        return f"Hecho: «{comando}» ha terminado bien, sin salida."
    return f"Hecho. Salida de «{comando}»:\n{salida}"


def build_shell_tool(cfg: dict | None = None) -> Tool:
    """La herramienta de shell, con su `[tools.shell]` ya dentro."""
    cfg = dict(cfg or {})
    return Tool(
        name="ejecutar_comando",
        description=(
            "Ejecuta una orden de zsh en el equipo del usuario y devuelve su salida. "
            "Úsala para hacer cosas en el sistema: crear, mover o borrar carpetas y "
            "ficheros, mirar el espacio en disco o la batería, usar git, etc. "
            "Si el usuario pide algo que se hace con un comando, EJECÚTALO tú con esta "
            "herramienta en vez de explicarle cómo hacerlo o dictarle el comando. "
            "Fíjate en lo que devuelve: si ha fallado, díselo en vez de dar por hecho "
            "que ha ido bien, y resume la salida en una frase en lugar de leerla entera. "
            "No hay terminal interactiva: nada de `sudo`, editores ni comandos que se "
            "queden esperando a que alguien teclee. "
            "Y no la uses para abrir aplicaciones con ventana (una terminal, el "
            "navegador, un editor gráfico): para eso está abrir_aplicacion, que las "
            "deja abiertas; aquí se cortarían al cabo de unos segundos. "
            "Para escribir texto dentro de un fichero tampoco: nada de `echo ... > x.txt`, "
            "usa escribir_fichero, que no se pelea con las comillas ni con los acentos. "
            "Los nombres con espacios SIEMPRE entre comillas: `mkdir -p \"viaje a Roma\"`, "
            "porque sin ellas creas tres carpetas en vez de una. "
            + descripcion_carpetas()
        ),
        parameters={
            "type": "object",
            "properties": {
                "comando": {
                    "type": "string",
                    "description": (
                        "La orden tal cual se escribiría en la terminal, "
                        "p.ej. 'mkdir -p ~/fotos/2026' o 'df -h /'. "
                        "Los nombres con espacios, entre comillas: "
                        "mv ~/fotos \"imágenes viejas\", no mv ~/fotos imágenes viejas."
                    ),
                },
                "directorio": {
                    "type": "string",
                    "description": (
                        "Directorio donde ejecutarla (opcional). "
                        "Por defecto, la carpeta personal del usuario."
                    ),
                },
            },
            "required": ["comando"],
        },
        handler=lambda args: ejecutar_comando(args, cfg),
    )
