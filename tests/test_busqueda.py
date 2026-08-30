"""Que lo que se busca vuelva como texto, y que cuando no vuelve se diga.

Todo con la red simulada (ver conftest): estos tests prueban lo que se hace con
la respuesta, no que la Wikipedia esté hoy de buenas.
"""

import json

import pytest

from maripepis.tools.base import es_fallo
from maripepis.tools.busqueda import (
    build_weather_tool,
    buscar_texto,
    consultar_tiempo,
)


class RespuestaFalsa:
    def __init__(self, datos, status_code=200):
        self._datos = datos
        self.status_code = status_code

    def json(self):
        if isinstance(self._datos, str):
            return json.loads(self._datos)      # para simular un JSON roto
        return self._datos


@pytest.fixture
def responder(monkeypatch):
    """Contesta según la URL pedida. Devuelve la lista de URLs que se han pedido."""
    pedidas = []

    def _montar(por_url):
        def _get(url, params=None, **kwargs):
            pedidas.append(url)
            for trozo, datos in por_url.items():
                if trozo in url:
                    return RespuestaFalsa(datos)
            return RespuestaFalsa({}, status_code=404)

        monkeypatch.setattr("maripepis.tools.busqueda.httpx.get", _get)
        return pedidas

    return _montar


# --- Buscar ---------------------------------------------------------------

def test_la_respuesta_directa_de_duckduckgo_gana(responder):
    pedidas = responder({"api.duckduckgo.com": {"AbstractText": "El Teide mide 3715 m."}})

    assert "3715" in buscar_texto("Teide")
    assert not any("wikipedia" in u for u in pedidas)   # ni se molesta en seguir


def test_cae_a_la_wikipedia_si_no_hay_respuesta_directa(responder):
    responder({
        "api.duckduckgo.com": {"AbstractText": ""},
        "/w/api.php": {"query": {"search": [
            {"title": "Antonio Meucci", "snippet": "creador del <b>teléfono</b>"},
            {"title": "Teléfono", "snippet": "aparato de <i>telecomunicación</i>"},
        ]}},
        "/api/rest_v1/": {"extract": "Antonio Meucci fue un inventor italiano."},
    })

    texto = buscar_texto("quién inventó el teléfono")

    assert "inventor italiano" in texto        # el resumen largo del primero
    assert "Teléfono: aparato" in texto        # y los demás, en corto
    assert "<b>" not in texto                  # sin marcado: esto acaba dicho en voz


def test_sin_nada_en_ninguna_fuente_devuelve_vacio(responder):
    responder({"api.duckduckgo.com": {}, "/w/api.php": {"query": {"search": []}}})
    assert buscar_texto("asdfghjkl") == ""


def test_un_servidor_caido_no_tumba_el_turno(monkeypatch):
    import httpx

    def _revienta(*a, **k):
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr("maripepis.tools.busqueda.httpx.get", _revienta)
    assert buscar_texto("lo que sea") == ""    # calla, pero no explota


# --- El tiempo ------------------------------------------------------------

PARTE = {
    "nearest_area": [{"areaName": [{"value": "Alicante"}]}],
    "current_condition": [{
        "temp_C": "24", "FeelsLikeC": "24", "humidity": "57", "windspeedKmph": "11",
        "lang_es": [{"value": "Soleado"}], "weatherDesc": [{"value": "Sunny"}],
    }],
    "weather": [
        {
            "date": "2026-08-29", "mintempC": "23", "maxtempC": "26",
            "hourly": [
                {"time": str(h * 300), "tempC": str(20 + h), "chanceofrain": "0",
                 "lang_es": [{"value": "Soleado"}], "weatherDesc": [{"value": "Sunny"}]}
                for h in range(8)
            ],
        },
        {"date": "2026-08-30", "mintempC": "23", "maxtempC": "27", "hourly": []},
        {"date": "2026-08-31", "mintempC": "22", "maxtempC": "28", "hourly": []},
    ],
}


def test_trae_el_parte_de_verdad(responder):
    responder({"wttr.in": PARTE})

    msg = consultar_tiempo({"lugar": "Alicante"})

    assert not es_fallo(msg)
    assert "Alicante" in msg
    assert "24 °C" in msg                      # lo de ahora
    assert "2026-08-31" in msg                 # y los tres días
    assert "Soleado" in msg                    # en español


def test_los_dias_se_pueden_recortar(responder):
    responder({"wttr.in": PARTE})
    msg = consultar_tiempo({"lugar": "Alicante", "dias": 1})
    assert "2026-08-29" in msg
    assert "2026-08-30" not in msg


def test_el_detalle_por_horas_solo_si_se_pide(responder):
    responder({"wttr.in": PARTE})
    assert "15:00" not in consultar_tiempo({"lugar": "Alicante"})
    assert "15:00" in consultar_tiempo({"lugar": "Alicante", "por_horas": True})


def test_si_no_contesta_el_servidor_lo_dice_en_vez_de_inventarse_el_tiempo(responder):
    # Lo importante de todo esto: una previsión inventada suena igual que una real.
    responder({"otra-cosa": {}})

    msg = consultar_tiempo({"lugar": "Alicante"})

    assert es_fallo(msg)
    assert "no te inventes" in msg.lower()


def test_un_dias_que_no_es_un_numero_no_revienta(responder):
    responder({"wttr.in": PARTE})
    assert not es_fallo(consultar_tiempo({"lugar": "Alicante", "dias": "muchos"}))


def test_la_herramienta_esta_bien_formada():
    t = build_weather_tool()
    assert t.name == "consultar_tiempo"
    assert t.to_claude()["name"] == "consultar_tiempo"
    assert t.parameters["required"] == []       # sin lugar, wttr.in geolocaliza
