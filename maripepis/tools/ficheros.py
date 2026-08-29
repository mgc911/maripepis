"""Escribir un fichero de texto: «guárdame una nota con la lista de la compra».

Esto ya se podía hacer con `ejecutar_comando` y un `echo ... > fichero`, y por
eso mismo salía mal. Un `echo` con el texto dentro es un campo de minas de
comillas, acentos y saltos de línea, y el modelo, cuando lo ve venir, se escaquea:
en vez de escribir el fichero abre un editor y te cuenta lo que tienes que teclear
tú. Que es justo lo contrario de lo que se le ha pedido.

Con una herramienta propia el contenido viaja como un argumento más —sin shell
de por medio— y el modelo la encuentra a la primera.

Y aquí vive también la mitad que faltaba, `leer_fichero`: sin ella, «revisa el
documento que me hiciste» no tiene respuesta posible, porque del turno anterior
al modelo solo le queda su propia frase. Y antes que reconocerlo, se inventa lo
que pone y te asegura que lo ha corregido.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .base import MARCA_MODELO, Tool
from .carpetas import descripcion as descripcion_carpetas
from .carpetas import resolver_ruta

log = logging.getLogger("maripepis.ficheros")

MAX_CHARS = 100_000
# Lo que se le pasa al modelo al leer. Va bastante por debajo de MAX_CHARS: aquí
# el texto entra en el contexto de cada petición siguiente, y con num_ctx en 8192
# un documento largo se come el turno entero.
MAX_LECTURA = 4_000
# Tope de lo que se saca del disco antes de mirar si es texto: un binario o un
# log de un giga no se leen «por si acaso».
MAX_BYTES = 1_000_000


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
        # Una sola orden, y la primera. Con la versión larga («si el usuario te ha
        # pedido cambiarlo, léelo antes con leer_fichero y vuelve en modo...») el
        # 7B repetía la llamada idéntica, sin `modo`, una y otra vez: el usuario
        # decía «sobrescríbelo» tres veces y el fichero seguía igual.
        return (
            f"NO he escrito nada: {destino} ya existe."
            + MARCA_MODELO
            + ' Repite AHORA esta misma llamada añadiendo modo="sobrescribir" (pisa lo '
            'que hubiera) o modo="añadir" (escribe al final). Sin el argumento modo no '
            "toco un fichero que ya existe, por muchas veces que lo intentes."
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
        f"{len(contenido)} caracteres)." + MARCA_MODELO
        + " Dile al usuario dónde ha quedado, en una frase."
    )


def leer_fichero(args: dict) -> str:
    """Devuelve lo que hay dentro de un fichero de texto.

    Sin esto, «revisa el documento que me hiciste» no tiene respuesta posible: el
    modelo no ve el disco, y lo único que le queda del turno anterior es su propia
    frase. Antes que decir que no puede, se inventa el contenido y te asegura que
    lo ha corregido.
    """
    nombre = (args.get("ruta") or args.get("fichero") or args.get("archivo") or "").strip()
    if not nombre:
        return "¿Qué fichero quieres que lea?"

    carpeta = str(args.get("carpeta") or args.get("directorio") or "")
    origen = resolver_ruta(nombre, carpeta)

    if origen.is_dir():
        return f"NO he leído nada: {origen} es una carpeta, no un fichero."
    if not origen.exists():
        return (
            f"NO he leído nada: {origen} no existe."
            + MARCA_MODELO
            + " Díselo al usuario tal cual, sin inventarte lo que pondría; si acaso, "
            "pregúntale dónde está."
        )

    try:
        tamano = origen.stat().st_size
        with open(origen, "rb") as f:
            crudo = f.read(MAX_BYTES)
    except OSError as e:
        log.warning("No pude leer %s: %s", origen, e)
        return f"NO he leído nada: {origen} ha dado un error ({e.strerror})."

    # Un `\0` en el primer megabyte y no hay más que hablar: leer un binario en
    # voz alta no le sirve a nadie, y de paso ensucia el contexto para el resto
    # de la conversación.
    if b"\0" in crudo:
        return f"NO he leído nada: {origen} no es un fichero de texto."
    if not tamano:
        return f"{origen} existe pero está vacío: no tiene nada dentro."

    texto = crudo.decode("utf-8", "replace")
    recorte = ""
    if len(texto) > MAX_LECTURA:
        texto = texto[:MAX_LECTURA]
        recorte = (
            f" [Solo los primeros {MAX_LECTURA} caracteres de {tamano}: si vas a "
            "reescribirlo entero, avisa al usuario de que no lo has visto completo.]"
        )

    lineas = texto.count("\n") + (0 if texto.endswith("\n") else 1)
    log.info("Leído %s (%d líneas, %d caracteres)", origen, lineas, len(texto))
    return (
        f"Contenido de {origen} ({lineas} línea{'s' if lineas != 1 else ''}, "
        f"{tamano} caracteres):{recorte}\n{texto}"
    )


# Lo que se le manda a la ventana de chat cuando se escribe un fichero. Es más
# generoso que MAX_LECTURA porque aquí el texto NO entra en el contexto del
# modelo: solo viaja por el socket y se pinta. Y menos que MAX_CHARS porque un
# documento de cien mil caracteres en una burbuja no lo lee nadie.
MAX_VENTANA = 20_000


def para_la_ventana(destino: Path) -> tuple[str, str] | None:
    """El fichero recién escrito, para enseñarlo en el chat: ``(ruta, contenido)``.

    Se vuelve a leer del disco en vez de reutilizar el argumento `contenido` a
    propósito: en modo «añadir» ese argumento son solo las líneas nuevas, y
    enseñarlas bajo el nombre del fichero sería enseñar otra cosa. Lo que se
    pinta es el fichero, no lo que se le acaba de meter.

    Devuelve None si no hay nada que enseñar (se borró, es binario, no se puede
    leer). Nunca lanza: esto cuelga del camino de un turno de voz.
    """
    try:
        crudo = destino.read_bytes()[:MAX_BYTES]
    except OSError as e:
        log.debug("No pude releer %s para la ventana: %s", destino, e)
        return None
    if not crudo or b"\0" in crudo:
        return None

    texto = crudo.decode("utf-8", "replace")
    if len(texto) > MAX_VENTANA:
        texto = texto[:MAX_VENTANA] + "\n\n[…recortado: el fichero sigue en el disco]"
    return str(destino), texto


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
            "Para CAMBIAR un fichero que ya existe: léelo antes con leer_fichero y "
            "vuelve aquí en modo 'sobrescribir' con el texto entero ya corregido. "
            "Nunca digas que lo has actualizado sin haber pasado por aquí. "
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
                        "OBLIGATORIO si el fichero ya existe. 'crear' (por defecto) "
                        "solo sirve para uno nuevo: si ya existe no escribe nada. "
                        "'sobrescribir' lo reemplaza entero, y es el que quiere el "
                        "usuario cuando dice «cámbialo», «actualízalo», «corrígelo» o "
                        "«sobrescríbelo». 'añadir' escribe al final."
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


def build_read_tool() -> Tool:
    """La herramienta de leer ficheros: la mitad que faltaba de la de escribir."""
    return Tool(
        name="leer_fichero",
        description=(
            "Lee un fichero de texto del equipo y te devuelve lo que hay dentro. "
            "Es LA herramienta para «revisa el documento», «qué pone en...», «léeme "
            "la nota», «mira el fichero que hiciste». "
            "Y es OBLIGATORIA antes de modificar, corregir o ampliar algo que ya "
            "existe: lee primero para saber qué hay, y luego escribe con "
            "escribir_fichero en modo 'sobrescribir'. "
            "Nunca des por sabido lo que pone un fichero porque lo escribieras en un "
            "turno anterior: léelo. "
            + descripcion_carpetas()
        ),
        parameters={
            "type": "object",
            "properties": {
                "ruta": {
                    "type": "string",
                    "description": (
                        "Nombre del fichero, con carpeta si hace falta: 'notas.txt', "
                        "'documentos/lista.txt' o una ruta completa."
                    ),
                },
                "carpeta": {
                    "type": "string",
                    "description": "Carpeta en la que está (opcional), p.ej. 'documentos'.",
                },
            },
            "required": ["ruta"],
        },
        handler=leer_fichero,
    )
