"""Las luces de casa, dichas como se dicen: «apaga el salón», «pon la cocina al 20».

Todo el trabajo de este módulo es traducir. El puente Hue habla de recursos con
identificadores uuid, brillos en coma flotante y colores en coordenadas CIE; el
usuario habla de «la lamparita» y de «rojo». Lo de en medio está aquí.

Dos decisiones que parecen pequeñas y no lo son:

Los **grupos van antes que las bombillas**. Si hay una habitación llamada Salón y
además una bombilla llamada Salón, «apaga el salón» tiene que apagar la
habitación entera: quien lo dice está mirando el salón, no una lámpara. Acertar
en la que se ve es más importante que ser coherente.

Y **nada de esto pide confirmación**, al revés que WhatsApp. Aquí no hace falta:
encender una luz se deshace apagándola, se ve desde donde estás, y no le llega a
nadie más. La única acción que necesitaba una red de seguridad ya la tiene.
"""

from __future__ import annotations

import logging
import unicodedata

import httpx

from ..hogar import Luz, SinPuente, SinVincular, conectar
from .base import MARCA_MODELO, Tool

log = logging.getLogger("maripepis.hogar")

#: Lo que se cuela delante de un nombre al hablar y no dice nada: «apaga **la**
#: luz **del** salón». Se quitan por delante, en orden, hasta que no quede
#: ninguno, porque encadenan («las luces de la cocina»).
_RELLENO = (
    "todas las ", "todos los ", "las ", "los ", "la ", "el ",
    "luces de ", "luces del ", "luz de ", "luz del ", "luces ", "luz ",
    "de la ", "del ", "de ",
)

#: Cómo se pide «todo». La casa entera es un caso aparte: no es un sitio que
#: exista en el puente, es todos los sitios a la vez.
#:
#: «luz» y «luces» están aquí porque «apaga la luz», a secas, es de lo más normal
#: y no nombra ningún sitio. Sin ellas se iba a buscar una habitación llamada
#: «luz», no la encontraba, y contestaba con la lista de habitaciones a alguien
#: que solo quería quedarse a oscuras.
_TODO = {
    "todo", "toda la casa", "casa", "todas", "todas las luces", "la casa entera",
    "luz", "luces",
}

#: Colores por su nombre, en RGB porque en RGB se pueden leer y corregir. La
#: conversión a lo que entiende el puente va debajo.
_COLORES: dict[str, tuple[int, int, int]] = {
    "rojo": (255, 0, 0),
    "naranja": (255, 110, 0),
    "amarillo": (255, 210, 0),
    "verde": (0, 255, 0),
    "turquesa": (0, 255, 200),
    "cian": (0, 255, 255),
    "azul": (0, 60, 255),
    "morado": (140, 0, 255),
    "violeta": (140, 0, 255),
    "lila": (190, 130, 255),
    "rosa": (255, 80, 180),
    "magenta": (255, 0, 255),
    "melocoton": (255, 170, 120),
}

#: Los blancos no se piden en RGB sino en temperatura, que es como se ven. El
#: número es *mirek* (un millón partido por los kelvin), que es la unidad del
#: puente: cuanto más alto, más cálido.
_BLANCOS: dict[str, int] = {
    "blanco": 300,
    "neutro": 300,
    "calido": 450,      # ~2200 K, la luz de bombilla de toda la vida
    "muy calido": 500,
    "frio": 200,        # ~5000 K
    "muy frio": 153,    # el tope del puente
    "luz de dia": 200,
    "concentracion": 230,
    "relax": 450,
}

_ENCENDER = {"encender", "enciende", "on", "abrir", "poner", "encendida", "encendido"}
_APAGAR = {"apagar", "apaga", "off", "cerrar", "quitar", "apagada", "apagado"}
_ALTERNAR = {"alternar", "cambiar", "toggle"}


def _sin_tildes(texto: str) -> str:
    """«Salón» y «salon» tienen que ser lo mismo: el dictado no siempre acentúa."""
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def normalizar(nombre: str) -> str:
    """El nombre reducido a lo que de verdad distingue: sin tildes, ni relleno."""
    clave = " ".join(_sin_tildes(nombre or "").lower().split())
    cambiado = True
    while cambiado:
        cambiado = False
        for relleno in _RELLENO:
            if clave.startswith(relleno):
                clave, cambiado = clave[len(relleno):], True
                break
    return clave.strip()


def buscar(sitio: str, candidatos: list[Luz]) -> Luz | None:
    """La luz o el grupo que mejor case con lo que se ha dicho.

    Tres intentos, de más estricto a menos: igual, uno contiene al otro, y una
    palabra en común. El último es el que salva «pon el dormitorio» cuando en la
    app se llama «Dormitorio principal», que es como acaban llamándose las cosas.
    """
    clave = normalizar(sitio)
    if not clave:
        return None

    normalizados = [(normalizar(c.nombre), c) for c in candidatos]

    for nombre, luz in normalizados:
        if nombre == clave:
            return luz
    for nombre, luz in normalizados:
        if clave in nombre or nombre in clave:
            return luz

    palabras = set(clave.split())
    for nombre, luz in normalizados:
        if palabras & set(nombre.split()):
            return luz
    return None


def _xy(rgb: tuple[int, int, int]) -> dict[str, float]:
    """RGB a las coordenadas CIE que quiere el puente (la conversión de Philips)."""
    def gamma(c: int) -> float:
        v = c / 255
        return ((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92

    r, g, b = (gamma(c) for c in rgb)
    x = r * 0.664511 + g * 0.154324 + b * 0.162028
    y = r * 0.283881 + g * 0.668433 + b * 0.047685
    z = r * 0.000088 + g * 0.072310 + b * 0.986039
    total = x + y + z
    if not total:
        return {"x": 0.0, "y": 0.0}
    return {"x": round(x / total, 4), "y": round(y / total, 4)}


def cambios_de_color(color: str) -> dict | None:
    """Lo que hay que mandarle al puente para ese color, o None si no lo conozco."""
    clave = normalizar(color)
    if clave in _BLANCOS:
        return {"color_temperature": {"mirek": _BLANCOS[clave]}}
    if clave in _COLORES:
        return {"color": {"xy": _xy(_COLORES[clave])}}
    # «azul claro», «rojo intenso»: quedarse con el color y perder el matiz es
    # mejor servicio que no hacer nada y contestar que no se ha entendido.
    for palabra in clave.split():
        if palabra in _BLANCOS:
            return {"color_temperature": {"mirek": _BLANCOS[palabra]}}
        if palabra in _COLORES:
            return {"color": {"xy": _xy(_COLORES[palabra])}}
    return None


def _colores_conocidos() -> str:
    return ", ".join(sorted(set(_COLORES) | set(_BLANCOS)))


def _objetivos(puente, sitio: str) -> tuple[list[Luz], str]:
    """A qué luces va la orden y cómo llamarlas al contestar.

    Lista vacía = no se ha encontrado nada con ese nombre.
    """
    grupos = puente.grupos()
    if normalizar(sitio) in {normalizar(t) for t in _TODO}:
        return grupos, "toda la casa"

    # Los grupos primero, a propósito (ver la cabecera del módulo).
    if luz := buscar(sitio, grupos):
        return [luz], luz.nombre
    if luz := buscar(sitio, puente.bombillas()):
        return [luz], luz.nombre
    return [], sitio


def _nombres_disponibles(puente) -> str:
    nombres = [g.nombre for g in puente.grupos()] + [b.nombre for b in puente.bombillas()]
    return ", ".join(nombres) if nombres else "ninguna"


def controlar_luces(args: dict) -> str:
    """Encender, apagar, atenuar, colorear o poner una escena."""
    sitio = str(args.get("sitio") or args.get("lugar") or "").strip()
    accion = normalizar(str(args.get("accion") or ""))
    escena = str(args.get("escena") or "").strip()
    color = str(args.get("color") or "").strip()
    brillo = args.get("brillo")

    if not sitio and not escena:
        return "¿Qué luces quieres, las del salón, las de la cocina...?"

    try:
        with conectar() as puente:
            return _hacer(puente, sitio, accion, escena, color, brillo)
    except SinVincular as e:
        return (
            f"NO he tocado las luces: {e}."
            f"{MARCA_MODELO} Dile al usuario que lo arregla en un minuto: que abra "
            "una terminal, ejecute `maripepis-hue vincular` y pulse el botón "
            "redondo del puente. No vuelvas a llamar a esta herramienta hasta que "
            "diga que lo ha hecho."
        )
    except SinPuente as e:
        return (
            f"NO he tocado las luces: {e}."
            f"{MARCA_MODELO} Puede ser que el puente esté desenchufado o que este "
            "equipo esté en otra red. Díselo y no lo reintentes."
        )
    except httpx.HTTPError as e:
        return f"NO he tocado las luces: el puente ha fallado ({e})."


def _hacer(puente, sitio: str, accion: str, escena: str, color: str, brillo) -> str:  # noqa: ANN001
    """La orden en sí, ya con el puente abierto."""
    objetivos, como_se_llama = _objetivos(puente, sitio) if sitio else ([], "")

    if escena:
        return _poner_escena(puente, escena, objetivos, como_se_llama)

    if not objetivos:
        return (
            f"NO he tocado las luces: aquí no hay nada que se llame «{sitio}». "
            f"Lo que hay es: {_nombres_disponibles(puente)}."
            f"{MARCA_MODELO} Pregúntale a cuál se refería, con esos nombres."
        )

    cambios: dict = {}
    dicho: list[str] = []

    if accion in _APAGAR:
        cambios["on"] = {"on": False}
    elif accion in _ENCENDER:
        cambios["on"] = {"on": True}
    elif accion in _ALTERNAR:
        cambios["on"] = {"on": not any(o.encendida for o in objetivos)}
        dicho.append("encendida" if cambios["on"]["on"] else "apagada")

    if brillo is not None:
        try:
            nivel = max(0, min(100, round(float(brillo))))
        except (TypeError, ValueError):
            return (
                f"NO he tocado las luces: «{brillo}» no es un brillo."
                f"{MARCA_MODELO} Tiene que ser un número del 0 al 100."
            )
        if nivel == 0:
            # Brillo cero en el puente no apaga: deja la luz al mínimo, encendida.
            # Quien dice «pon el salón a cero» quiere el salón a oscuras.
            cambios["on"] = {"on": False}
            dicho.append("apagada")
        else:
            cambios["dimming"] = {"brightness": float(nivel)}
            # Atenuar una luz apagada no se ve. Si no se ha dicho lo contrario,
            # se enciende: es lo que quería quien lo ha pedido.
            cambios.setdefault("on", {"on": True})
            dicho.append(f"al {nivel}%")

    if color:
        if (cambio := cambios_de_color(color)) is None:
            return (
                f"NO he tocado las luces: no sé qué color es «{color}». "
                f"Conozco estos: {_colores_conocidos()}."
                f"{MARCA_MODELO} Ofrécele el más parecido de esa lista."
            )
        cambios |= cambio
        cambios.setdefault("on", {"on": True})
        dicho.append(f"en {normalizar(color)}")

    if not cambios:
        return (
            f"NO he tocado {como_se_llama}: no me has dicho qué hacer con esas luces."
            f"{MARCA_MODELO} Falta la acción (encender, apagar), el brillo o el color."
        )

    fallos = _aplicar(puente, objetivos, cambios)
    if len(fallos) == len(objetivos):
        return f"NO he podido cambiar {como_se_llama}: el puente ha dicho «{fallos[0]}»."

    if not dicho:
        dicho.append("encendida" if cambios.get("on", {}).get("on") else "apagada")
    resumen = f"He dejado {como_se_llama} {' y '.join(dicho)}."
    if fallos:
        resumen += f" Menos {len(fallos)}, que no han respondido."
    return resumen


def _aplicar(puente, objetivos: list[Luz], cambios: dict) -> list[str]:  # noqa: ANN001
    """Manda los cambios a cada objetivo. Devuelve los motivos de los que fallaron.

    Se sigue con los demás cuando uno falla: en «apaga toda la casa», que una
    bombilla esté sin corriente no es motivo para dejar el resto encendidas.
    """
    fallos: list[str] = []
    for objetivo in objetivos:
        try:
            puente.aplicar(objetivo, cambios)
        except httpx.HTTPError as e:
            log.info("No pude cambiar %s: %s", objetivo.nombre, e)
            fallos.append(str(e))
    return fallos


def _poner_escena(puente, escena: str, objetivos: list[Luz], sitio: str) -> str:  # noqa: ANN001
    """Activa una escena guardada en el puente, mejor si es la del sitio pedido."""
    todas = puente.escenas()
    if not todas:
        return (
            "NO he puesto ninguna escena: en este puente no hay escenas guardadas."
            f"{MARCA_MODELO} Se crean desde la app de Hue; aquí solo se activan."
        )

    # Si se ha dicho el sitio, las de ese sitio primero: «pon relax en el salón»
    # con una escena Relax en cada habitación tiene que acertar la del salón.
    ids = {o.id for o in objetivos}
    ordenadas = sorted(todas, key=lambda e: e[2] not in ids) if ids else todas

    clave = normalizar(escena)
    for id_escena, nombre, _ in ordenadas:
        if clave and clave in normalizar(nombre):
            try:
                puente.activar_escena(id_escena)
            except httpx.HTTPError as e:
                return f"NO he puesto la escena «{nombre}»: el puente ha fallado ({e})."
            donde = f" en {sitio}" if sitio else ""
            return f"He puesto la escena {nombre}{donde}."

    nombres = ", ".join(sorted({n for _, n, _ in todas}))
    return (
        f"NO he puesto ninguna escena: no hay ninguna que se llame «{escena}». "
        f"Las que hay son: {nombres}."
        f"{MARCA_MODELO} Pregúntale cuál de esas quería."
    )


def estado_de_las_luces(args: dict) -> str:
    """Qué luces hay encendidas, para poder contestar «¿me he dejado algo dado?»."""
    sitio = str(args.get("sitio") or args.get("lugar") or "").strip()
    try:
        with conectar() as puente:
            if sitio and normalizar(sitio) not in {normalizar(t) for t in _TODO}:
                objetivos, como_se_llama = _objetivos(puente, sitio)
                if not objetivos:
                    return (
                        f"NO he podido mirarlo: aquí no hay nada que se llame "
                        f"«{sitio}». Lo que hay es: {_nombres_disponibles(puente)}."
                    )
                return _resumen(objetivos, como_se_llama)
            return _resumen(puente.grupos(), "la casa")
    except SinVincular as e:
        return (
            f"NO he podido mirarlo: {e}."
            f"{MARCA_MODELO} Que ejecute `maripepis-hue vincular` y pulse el botón."
        )
    except SinPuente as e:
        return f"NO he podido mirarlo: {e}."
    except httpx.HTTPError as e:
        return f"NO he podido mirarlo: el puente ha fallado ({e})."


def _resumen(luces: list[Luz], donde: str) -> str:
    """Una o dos frases sobre lo que está dado. Esto acaba dicho en voz alta."""
    encendidas = [luz for luz in luces if luz.encendida]
    if not luces:
        return f"NO he encontrado luces en {donde}."
    if not encendidas:
        return f"En {donde} está todo apagado."
    if len(encendidas) == 1:
        luz = encendidas[0]
        return f"En {donde} solo está {luz.nombre}, al {luz.brillo}%."
    detalle = ", ".join(f"{luz.nombre} al {luz.brillo}%" for luz in encendidas)
    return f"En {donde} hay {len(encendidas)} encendidas: {detalle}."


def build_home_tools(cfg: dict | None = None) -> list[Tool]:
    """Las herramientas de la casa, para la sección `[tools.hogar]`."""
    return [
        Tool(
            name="controlar_luces",
            description=(
                "Enciende, apaga, atenúa o colorea las luces de casa (Philips Hue), "
                "y activa escenas guardadas. Úsala para 'apaga el salón', 'pon la "
                "cocina al 20', 'las luces en rojo', 'enciende todo', 'pon la escena "
                "relax'. Si el usuario nombra un sitio que no existe, te devuelve la "
                "lista de los que sí: pregúntale con esa lista, no te inventes otro."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sitio": {
                        "type": "string",
                        "description": (
                            "Habitación, zona o lámpara, tal como lo haya dicho el "
                            "usuario ('el salón', 'la cocina', 'la lamparita'). "
                            "'todo' o 'toda la casa' para todas."
                        ),
                    },
                    "accion": {
                        "type": "string",
                        "enum": ["encender", "apagar", "alternar"],
                        "description": "Qué hacer. Se puede omitir si solo cambias brillo o color.",
                    },
                    "brillo": {
                        "type": "integer",
                        "description": (
                            "Brillo del 0 al 100 (opcional). Enciende la luz si estaba "
                            "apagada; 0 la apaga."
                        ),
                    },
                    "color": {
                        "type": "string",
                        "description": (
                            "Color por su nombre en español (opcional): rojo, azul, "
                            "verde, naranja, rosa, morado, cálido, frío, blanco..."
                        ),
                    },
                    "escena": {
                        "type": "string",
                        "description": (
                            "Escena guardada en el puente a activar (opcional), p.ej. "
                            "'relax'. Si la pones, manda ella: se ignoran brillo y color."
                        ),
                    },
                },
                "required": [],
            },
            handler=controlar_luces,
        ),
        Tool(
            name="estado_de_las_luces",
            description=(
                "Dice qué luces están encendidas y a qué brillo. Úsala para '¿me he "
                "dejado alguna luz dada?', '¿está encendido el salón?'. No contestes "
                "de memoria: las luces las cambia también quien esté en casa."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sitio": {
                        "type": "string",
                        "description": "Sitio concreto (opcional). Sin él, mira toda la casa.",
                    }
                },
                "required": [],
            },
            handler=estado_de_las_luces,
        ),
    ]
