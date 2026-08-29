"""Escribir un fichero de texto: «guárdame una nota con la lista de la compra».

Esto ya se podía hacer con `ejecutar_comando` y un `echo ... > fichero`, y por
eso mismo salía mal. Un `echo` con el texto dentro es un campo de minas de
comillas, acentos y saltos de línea, y el modelo, cuando lo ve venir, se escaquea:
en vez de escribir el fichero abre un editor y te cuenta lo que tienes que teclear
tú. Que es justo lo contrario de lo que se le ha pedido.

Con una herramienta propia el contenido viaja como un argumento más —sin shell
de por medio— y el modelo la encuentra a la primera.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .base import Tool
from .carpetas import descripcion as descripcion_carpetas
from .carpetas import resolver_ruta

log = logging.getLogger("maripepis.ficheros")

MAX_CHARS = 100_000


def escribir_fichero(args: dict) -> str:
    """Crea (o amplía) un fichero de texto y dice qué ha pasado exactamente."""
    nombre = (args.get("ruta") or args.get("fichero") or args.get("archivo") or "").strip()
    if not nombre:
        return "¿Cómo quieres que se llame el fichero?"

    contenido = args.get("contenido")
    if contenido is None:
        contenido = args.get("texto")
    if contenido is None:
        return "¿Qué quieres que escriba dentro?"
    contenido = str(contenido)
    if len(contenido) > MAX_CHARS:
        return f"NO he escrito nada: el texto pasa de {MAX_CHARS} caracteres."

    modo = (args.get("modo") or "crear").strip().lower()
    carpeta = str(args.get("carpeta") or args.get("directorio") or "")
    destino = resolver_ruta(nombre, carpeta)

    if destino.is_dir():
        return f"NO he escrito nada: {destino} es una carpeta, no un fichero."

    # Sobrescribir sin avisar es la forma más tonta de perder algo: basta con que
    # el micrófono entienda «notas» donde has dicho «notitas». Si ya existe, se
    # pregunta; para pisarlo hay que pedirlo por su nombre.
    if destino.exists() and modo == "crear":
        return (
            f"NO he escrito nada: {destino} ya existe. Pregúntale al usuario si quiere "
            "que le AÑADAS el texto al final (modo «añadir») o que lo SOBRESCRIBAS "
            "entero (modo «sobrescribir»), y vuelve a llamarme con el modo que te diga."
        )

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        if modo in ("añadir", "anadir", "append", "ampliar"):
            # Un salto delante solo si el fichero no acababa en línea nueva: si no,
            # la primera línea añadida se pegaría al final de la última. Se mira
            # el último byte y no el fichero entero, que puede ser un diario.
            sangria = ""
            if destino.exists() and destino.stat().st_size:
                with open(destino, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    sangria = "" if f.read(1) == b"\n" else "\n"
            with open(destino, "a", encoding="utf-8") as f:
                f.write(sangria + contenido.strip("\n") + "\n")
            accion = "he añadido el texto a"
        else:
            destino.write_text(contenido.rstrip("\n") + "\n", encoding="utf-8")
            accion = "he escrito"
    except OSError as e:
        log.warning("No pude escribir %s: %s", destino, e)
        return f"NO he escrito nada: {destino} ha dado un error ({e.strerror})."

    lineas = contenido.strip().count("\n") + 1
    log.info("Escrito %s (%d líneas, %d caracteres)", destino, lineas, len(contenido))
    return (
        f"Hecho: {accion} {destino} ({lineas} línea{'s' if lineas != 1 else ''}, "
        f"{len(contenido)} caracteres). Dile al usuario dónde ha quedado, en una frase."
    )


def build_file_tool() -> Tool:
    """La herramienta de escribir ficheros, con las carpetas del equipo dentro."""
    return Tool(
        name="escribir_fichero",
        description=(
            "Crea un fichero de texto con el contenido que le des, o le añade texto "
            "al final. Es LA herramienta para «crea un documento con...», «guárdame "
            "una nota», «apunta esto en un fichero», «hazme una lista». "
            "Escríbelo tú aquí: no abras un editor para que lo teclee el usuario, ni "
            "montes un `echo` con `ejecutar_comando`. "
            + descripcion_carpetas()
        ),
        parameters={
            "type": "object",
            "properties": {
                "ruta": {
                    "type": "string",
                    "description": (
                        "Nombre del fichero, con carpeta si hace falta: 'notas.txt', "
                        "'descargas/lista.txt' o una ruta completa. Ponle extensión "
                        "(.txt o .md) si el usuario no dice otra cosa."
                    ),
                },
                "contenido": {
                    "type": "string",
                    "description": (
                        "El texto que va dentro, ya redactado y con sus saltos de línea. "
                        "Nada de comillas ni comandos alrededor: solo el contenido."
                    ),
                },
                "modo": {
                    "type": "string",
                    "enum": ["crear", "sobrescribir", "añadir"],
                    "description": (
                        "'crear' (por defecto) avisa si el fichero ya existe; "
                        "'sobrescribir' lo reemplaza entero; 'añadir' escribe al final."
                    ),
                },
                "carpeta": {
                    "type": "string",
                    "description": "Carpeta donde dejarlo (opcional), p.ej. 'documentos'.",
                },
            },
            "required": ["ruta", "contenido"],
        },
        handler=escribir_fichero,
    )
