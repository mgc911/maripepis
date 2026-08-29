"""Traerse información de internet en TEXTO, no solo abrir el navegador.

`buscar_en_internet` abría una pestaña y devolvía «he buscado X». Al modelo no le
llegaba ni una palabra de los resultados, así que ante «busca el tiempo de la
semana y apúntamelo en un fichero» no podía hacer nada... y en vez de decirlo,
escribía el fichero con los días en blanco y aseguraba que lo había rellenado.
Aquí la petición se hace de verdad y el texto vuelve al turno.

No hay búsqueda web general, y no por pereza: no la hay gratis. DuckDuckGo (html
y lite), SearXNG y Mojeek contestan con un captcha a todo lo que no sea un
navegador de carne y hueso, y el resto pide clave de API. Sin clave y sin
registro quedan dos fuentes que sí responden, y que entre las dos cubren casi
todo lo que se pregunta hablando: la Wikipedia (quién, qué, cuánto, dónde) y
wttr.in (el tiempo). Para lo demás sigue estando el navegador.
"""

from __future__ import annotations

import html
import logging
import re
import urllib.parse

import httpx

from .base import Tool

log = logging.getLogger("maripepis.busqueda")

# Corto a propósito: detrás hay alguien esperando a que le contesten en voz alta,
# y ocho segundos callada ya se hacen largos.
TIMEOUT = 8.0
MAX_RESULTADOS = 3
MAX_EXTRACTO = 700
UA = "maripepis/0.1 (asistente de voz local)"

_ETIQUETAS = re.compile(r"<[^>]+>")


def _limpiar(texto: str) -> str:
    """Quita el marcado y colapsa los espacios: esto acaba dicho en voz alta."""
    return " ".join(html.unescape(_ETIQUETAS.sub("", texto or "")).split())


def _pedir(url: str, params: dict | None = None) -> dict | None:
    """Un GET que devuelve JSON, o ``None`` si algo ha ido mal.

    Nunca levanta: que internet no conteste no puede tumbar el turno, y el modelo
    necesita distinguir «no he encontrado nada» de «se ha roto», que es
    exactamente lo que devuelve quien llama a esto.
    """
    try:
        r = httpx.get(url, params=params, timeout=TIMEOUT,
                      headers={"User-Agent": UA}, follow_redirects=True)
        if r.status_code != 200:
            log.info("%s respondió %s", url, r.status_code)
            return None
        return r.json()
    except (httpx.HTTPError, ValueError) as e:  # ValueError incluye el JSON roto
        log.info("No pude consultar %s: %s", url, e)
        return None


def _instant_answer(consulta: str) -> str:
    """La respuesta directa de DuckDuckGo, cuando la hay.

    Solo acierta con nombres propios exactos («Alicante», «Teide»); con una
    pregunta entera devuelve vacío. Va primero porque cuando responde, responde
    en una frase, que es justo lo que se puede decir hablando.
    """
    datos = _pedir("https://api.duckduckgo.com/", {
        "q": consulta, "format": "json", "no_html": "1", "skip_disambig": "1",
    })
    if not datos:
        return ""
    for clave in ("Answer", "AbstractText", "Definition"):
        texto = _limpiar(str(datos.get(clave) or ""))
        if texto:
            return texto[:MAX_EXTRACTO]
    return ""


def _wikipedia(consulta: str, idioma: str = "es") -> str:
    """Lo que la Wikipedia sepa: el resumen del mejor resultado y un par más.

    Dos peticiones y no una porque el buscador devuelve recortes cortados por la
    mitad, y el resumen del artículo es un párrafo que se puede leer entero.
    """
    datos = _pedir(f"https://{idioma}.wikipedia.org/w/api.php", {
        "action": "query", "list": "search", "srsearch": consulta,
        "srlimit": MAX_RESULTADOS, "format": "json", "formatversion": "2",
    })
    resultados = ((datos or {}).get("query") or {}).get("search") or []
    if not resultados:
        return ""

    titulo = resultados[0].get("title", "")
    resumen = _pedir(
        f"https://{idioma}.wikipedia.org/api/rest_v1/page/summary/"
        + urllib.parse.quote(titulo.replace(" ", "_"), safe="")
    )
    extracto = _limpiar((resumen or {}).get("extract", ""))

    partes = [f"{titulo}: {extracto[:MAX_EXTRACTO]}"] if extracto else []
    for r in resultados:
        if extracto and r.get("title") == titulo:
            continue                       # ya está, y mejor contado
        frase = _limpiar(r.get("snippet", ""))
        if frase:
            partes.append(f"{r.get('title', '')}: {frase}")
    return "\n".join(partes)


def buscar_texto(consulta: str) -> str:
    """El texto que se haya podido encontrar, o ``""`` si no hay nada que contar."""
    for fuente in (_instant_answer, _wikipedia):
        texto = fuente(consulta)
        if texto:
            log.info("Encontrado en %s (%d caracteres)", fuente.__name__, len(texto))
            return texto
    log.info("Sin resultados en texto para «%s»", consulta)
    return ""


# --- El tiempo ------------------------------------------------------------
# Aparte de la búsqueda porque la Wikipedia no sabe qué tiempo hará el jueves, y
# es de lo que más se pregunta hablando. wttr.in lo da en JSON, en español y sin
# clave; da tres días, ni uno más, y eso hay que decirlo en vez de rellenarlo.

MAX_DIAS = 3


def _descripcion(bloque: dict) -> str:
    """El estado del cielo, en español si wttr.in lo trae traducido."""
    es = (bloque.get("lang_es") or [{}])[0].get("value")
    return (es or (bloque.get("weatherDesc") or [{}])[0].get("value") or "").strip()


def _nombre_del_sitio(datos: dict) -> str:
    area = (datos.get("nearest_area") or [{}])[0]
    return ((area.get("areaName") or [{}])[0].get("value") or "").strip()


def consultar_tiempo(args: dict) -> str:
    """El parte de verdad: lo de ahora y hasta tres días, con horas si se piden."""
    lugar = (args.get("lugar") or args.get("ciudad") or args.get("sitio") or "").strip()
    try:
        dias = max(1, min(int(args.get("dias") or args.get("days") or MAX_DIAS), MAX_DIAS))
    except (TypeError, ValueError):
        dias = MAX_DIAS
    por_horas = bool(args.get("por_horas") or args.get("horas"))

    datos = _pedir("https://wttr.in/" + urllib.parse.quote(lugar), {"format": "j1", "lang": "es"})
    if not datos or not datos.get("weather"):
        return (
            f"NO he podido consultar el tiempo{' de ' + lugar if lugar else ''}: "
            "wttr.in no ha contestado. Díselo al usuario tal cual y NO te inventes "
            "la previsión ni dejes los días en blanco."
        )

    sitio = _nombre_del_sitio(datos) or lugar or "tu zona"
    lineas = []

    ahora = (datos.get("current_condition") or [{}])[0]
    if ahora.get("temp_C"):
        lineas.append(
            f"Ahora en {sitio}: {_descripcion(ahora)}, {ahora['temp_C']} °C "
            f"(sensación {ahora.get('FeelsLikeC', '?')} °C), humedad "
            f"{ahora.get('humidity', '?')} %, viento {ahora.get('windspeedKmph', '?')} km/h."
        )

    for dia in datos["weather"][:dias]:
        horas = dia.get("hourly") or []
        # La franja del mediodía describe el día mejor que la de las tres de la
        # madrugada, que es la que sale si se coge la primera.
        mediodia = horas[4] if len(horas) > 4 else (horas[0] if horas else {})
        lineas.append(
            f"{dia.get('date', '?')}: {_descripcion(mediodia)}, mínima "
            f"{dia.get('mintempC', '?')} °C, máxima {dia.get('maxtempC', '?')} °C."
        )
        if por_horas:
            detalle = [
                f"{int(h.get('time', 0)) // 100:02d}:00 {h.get('tempC', '?')} °C "
                f"({_descripcion(h)}, lluvia {h.get('chanceofrain', '0')} %)"
                for h in horas
            ]
            if detalle:
                lineas.append("   " + " · ".join(detalle))

    # Si se han pedido los tres días es que querían «la semana»: más no hay, y
    # decirlo es mejor que dejar que el modelo rellene el resto de su cosecha.
    tope = "\nNo hay más allá de 3 días: si te han pedido la semana entera, dilo." \
        if dias >= MAX_DIAS else ""
    return f"Parte de wttr.in para {sitio}:\n" + "\n".join(lineas) + tope


def build_weather_tool() -> Tool:
    """La herramienta del tiempo, que es la mitad de lo que se le pregunta hablando."""
    return Tool(
        name="consultar_tiempo",
        description=(
            "Da el tiempo que hace ahora y la previsión de hasta 3 días en una ciudad, "
            "con temperaturas, cielo, lluvia y viento de verdad. "
            "Úsala SIEMPRE que el usuario pregunte por el tiempo, y también cuando te "
            "pida apuntar o escribir una previsión en un fichero: primero llama aquí "
            "para tener los datos, y luego escribe el fichero con ellos. "
            "NUNCA escribas una previsión sin haber llamado antes a esta herramienta, "
            "ni dejes los días puestos y vacíos. "
            "Para el tiempo no uses buscar_en_internet, que no lo sabe."
        ),
        parameters={
            "type": "object",
            "properties": {
                "lugar": {
                    "type": "string",
                    "description": (
                        "Ciudad o sitio ('Alicante', 'Madrid'). Si se omite, usa la "
                        "ubicación aproximada del equipo."
                    ),
                },
                "dias": {
                    "type": "integer",
                    "description": "Días de previsión, de 1 a 3 (por defecto 3).",
                },
                "por_horas": {
                    "type": "boolean",
                    "description": (
                        "true para el detalle cada tres horas de cada día. Úsalo cuando "
                        "pidan el tiempo «hora por hora»."
                    ),
                },
            },
            "required": [],
        },
        handler=consultar_tiempo,
    )
