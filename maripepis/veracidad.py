"""¿La respuesta dice haber hecho algo que no ha hecho?

Un modelo de 7B narra el éxito con una seguridad que no se corresponde con nada:
consulta el tiempo, no escribe el fichero y remata con «te lo he guardado en
documentos». Por escrito cantaría; dicho en voz alta, y sin ver la pantalla, no
hay forma humana de distinguirlo de que funcione.

Esto vive aparte porque hacen falta dos sitios distintos, y por motivos opuestos:

- el bucle de herramientas (`ollama_provider`) lo usa **antes** de dar el turno
  por terminado, para insistirle una vez: casi siempre lo hace a la segunda;
- el turno (`turn`) lo usa **después**, cuando ya no hay más remedio, para
  desmentirlo en voz alta.
"""

from __future__ import annotations

from .utils.phrases import normalize

# Pistas de que la respuesta afirma haber HECHO algo, no de haberlo contado.
# «No sé qué tiempo hará» es una respuesta honrada y no se toca; «he actualizado
# el archivo» sin haber llamado a nada, no.
CANTA_VICTORIA = (
    "he creado", "he escrito", "he anadido", "he agregado", "he actualizado",
    "he guardado", "he apuntado", "he modificado", "he cambiado", "he corregido",
    "he rellenado", "he completado", "he borrado", "he movido", "he copiado",
    "he abierto", "he ejecutado", "he buscado", "he puesto", "he dejado",
    "he sobrescrito", "ya lo tienes", "ya esta hecho", "ya esta listo",
    "queda hecho", "lo tienes en", "te lo he dejado", "te lo he guardado",
    "esta actualizado", "ha quedado actualizado", "he asegurado", "he incluido",
    "queda escrito", "queda guardado", "ahora contiene", "ya contiene",
    "he modificado",
    # En pasiva, que es como lo dice llama3.1:8b: «el archivo se ha modificado».
    "se ha modificado", "se ha guardado", "se ha escrito", "se ha actualizado",
    "se ha creado", "ha sido modificado", "ha sido guardado", "ha sido creado",
    "ha sido actualizado", "queda modificado",
)

# Lo que dice haber hecho → la herramienta que tendría que haberlo hecho, y cómo
# se desmiente. Contar llamadas a secas no basta: el modelo mira el tiempo y
# remata con «te lo he guardado». Llamó a *algo*, así que un contador se queda
# tan ancho; lo que importa es si llamó a **la que hacía falta**.
EXIGEN_HERRAMIENTA = (
    (("he guardado", "he escrito", "he actualizado", "he sobrescrito", "he anadido",
      "he agregado", "he creado el archivo", "he creado un archivo", "he rellenado",
      "he creado el fichero", "he creado un fichero", "he completado", "he corregido",
      "te lo he dejado", "te lo he guardado", "esta guardado", "queda guardado",
      "ha quedado actualizado", "esta actualizado", "he asegurado", "he incluido",
      "ahora contiene", "ya contiene", "he modificado",
      "se ha modificado", "se ha guardado", "se ha escrito", "se ha actualizado",
      "se ha creado", "ha sido modificado", "ha sido guardado", "ha sido creado",
      "ha sido actualizado", "queda modificado"),
     {"escribir_fichero"},
     "no he llegado a escribir el fichero"),
    (("he abierto", "te he abierto"),
     {"abrir_aplicacion", "abrir_navegador", "buscar_en_internet"},
     "no he abierto nada"),
    (("he creado la carpeta", "he ejecutado", "he borrado", "he movido", "he copiado"),
     {"ejecutar_comando"},
     "no he ejecutado ningún comando"),
)

# El otro final de turno que deja el trabajo a medias: anunciar en vez de hacer.
ANUNCIA_INTENCION = (
    "voy a", "vamos a", "procedo a", "procedere a", "a continuacion",
    "lo hare", "ahora lo", "ahora mismo lo", "actualizare", "escribire",
    "creare", "anadire", "guardare", "permiteme", "dejame que",
    "escribiendo el", "escribiendo en", "guardando el", "guardando en",
    "actualizando el", "creando el", "anadiendo al", "anadiendo el",
    "actualizo el", "escribo el", "guardo el", "ahora actualizo",
)


def canta_victoria(texto: str) -> bool:
    """¿La respuesta afirma haber hecho algo (y no solo haberlo explicado)?"""
    limpio = f" {normalize(texto)} "
    return any(pista in limpio for pista in CANTA_VICTORIA)


def anuncia_sin_hacer(texto: str) -> bool:
    """¿Cuenta lo que va a hacer en vez de haberlo hecho («ahora voy a...»)?"""
    limpio = f" {normalize(texto)} "
    return any(f" {pista} " in limpio for pista in ANUNCIA_INTENCION)


def falta_llamar(reply: str, execute) -> set[str]:  # noqa: ANN001
    """Las herramientas que la respuesta da por usadas y no se han usado.

    Sirve para nombrárselas: insistirle con «hazlo con la herramienta que haga
    falta» no basta —medido, vuelve a llamar a la que ya había llamado—; hay que
    decirle «llama a escribir_fichero» y que no llame a ninguna otra.
    """
    usadas = getattr(execute, "herramientas_ok", None)
    if not callable(usadas):
        return set()
    hechas = usadas()
    limpio = f" {normalize(reply)} "
    for pistas, hacen_falta, _motivo in EXIGEN_HERRAMIENTA:
        if any(p in limpio for p in pistas) and not (hechas & hacen_falta):
            return set(hacen_falta)
    return set()


def _se_delata_nombrando(limpio: str, hechas: set[str]) -> str:
    """«He llamado a escribir_fichero para crear el archivo...» y no la llamó.

    Esta se pilla sola, sin listas de frases: dice el nombre de la herramienta.
    `normalize` convierte el guion bajo en espacio, de ahí la traducción.
    """
    for nombre in {n for _p, hacen_falta, _m in EXIGEN_HERRAMIENTA for n in hacen_falta}:
        if nombre in hechas:
            continue
        dicho = nombre.replace("_", " ")
        if f" he llamado a {dicho} " in limpio or f" llame a {dicho} " in limpio:
            return f"no he llamado a {nombre}"
    return ""


def lo_que_no_ha_hecho(reply: str, execute) -> str:  # noqa: ANN001
    """El motivo del desmentido, o ``""`` si no hay nada que desmentir.

    `execute` solo tiene que saber contestar a `herramientas_ok()` (cuáles
    salieron bien) o a `llamadas` (cuántas se hicieron). Si no sabe ni eso, no se
    afirma nada: sobre un invocable pelado no hay forma de saberlo.

    Esto es una heurística, y se nota: la lista de frases se ha ensanchado dos
    veces con ejemplos medidos y nunca estará completa. Lo que sí es firme es el
    lado seguro — si no hay pista, no se desmiente—, así que lo que se escapa es
    una mentira que pasa, nunca un acierto que se desmiente por error.
    """
    limpio = f" {normalize(reply)} "

    usadas = getattr(execute, "herramientas_ok", None)
    if callable(usadas):
        hechas = usadas()
        if (delatado := _se_delata_nombrando(limpio, hechas)):
            return delatado
        for pistas, hacen_falta, motivo in EXIGEN_HERRAMIENTA:
            if any(p in limpio for p in pistas) and not (hechas & hacen_falta):
                return motivo

    # Y el caso general: presume de algo y no llamó absolutamente a nada.
    if getattr(execute, "llamadas", None) == 0 and canta_victoria(reply):
        return "no he hecho nada, no he llegado a usar ninguna herramienta"
    return ""
