import json
import logging

import httpx
import pytest

from maripepis.llm.ollama_provider import OllamaProvider, rescatar_llamadas

LOG = logging.getLogger("test")
NOMBRES = {"ejecutar_comando", "escribir_fichero"}


class Respuestas:
    """Contesta lo que se le diga, y apunta lo que le han pedido."""

    def __init__(self, *mensajes):
        self.mensajes = list(mensajes)
        self.peticiones: list[dict] = []

    def __call__(self, url, json=None, timeout=None):  # noqa: A002
        self.peticiones.append(json)
        msg = self.mensajes.pop(0) if self.mensajes else {"content": "y ya está."}
        return httpx.Response(200, json={"message": msg})


def _provider(monkeypatch, respuestas):
    monkeypatch.setattr("maripepis.llm.ollama_provider.httpx.post", respuestas)
    return OllamaProvider(model="qwen2.5:7b")


class _Tool:
    name = "ejecutar_comando"

    def to_ollama(self):
        return {"type": "function", "function": {"name": self.name}}


def test_pide_contexto_de_sobra(monkeypatch):
    # Sin num_ctx, Ollama da 4096 y aquí no llegan: el prompt con memoria y
    # herramientas ya ronda los 2500, y al pasarse el modelo desbarra.
    r = Respuestas({"content": "hola"})
    _provider(monkeypatch, r).run_tools_turn("sé breve", [], [_Tool()], lambda n, a: "")
    assert r.peticiones[0]["options"]["num_ctx"] == 8192


def test_el_contexto_se_puede_quitar(monkeypatch):
    r = Respuestas({"content": "hola"})
    p = OllamaProvider(model="x", context=0)
    monkeypatch.setattr("maripepis.llm.ollama_provider.httpx.post", r)
    p.run_tools_turn("s", [], [_Tool()], lambda n, a: "")
    assert "num_ctx" not in r.peticiones[0]["options"]


def test_ejecuta_la_llamada_aunque_venga_escrita_en_el_texto(monkeypatch):
    # Ollama no siempre la reconoce; si se le hace caso al texto, el comando
    # acaba dicho en voz alta en vez de ejecutado, que es la queja de siempre.
    crudo = 'Ronaldo {"name": "ejecutar_comando", "arguments": {"comando": "mkdir -p ~/fotos"}}'
    r = Respuestas({"content": crudo}, {"content": "Ya tienes la carpeta fotos."})
    hechas = []

    reply = _provider(monkeypatch, r).run_tools_turn(
        "s", [], [_Tool()], lambda n, a: hechas.append((n, a)) or "Hecho.",
    )

    assert hechas == [("ejecutar_comando", {"comando": "mkdir -p ~/fotos"})]
    assert reply == "Ya tienes la carpeta fotos."


def test_reintenta_si_ollama_devuelve_un_turno_mudo(monkeypatch):
    # Pasa de verdad, y sin reintento maripepis se queda callada.
    r = Respuestas({"content": ""}, {"content": "Perdona, ya está hecho."})
    reply = _provider(monkeypatch, r).run_tools_turn("s", [], [_Tool()], lambda n, a: "")
    assert reply == "Perdona, ya está hecho."
    assert len(r.peticiones) == 2


def test_un_turno_mudo_que_no_se_arregla_no_devuelve_el_vacio(monkeypatch):
    r = Respuestas(*[{"content": ""}] * 5)
    reply = _provider(monkeypatch, r).run_tools_turn("s", [], [_Tool()], lambda n, a: "")
    assert reply.strip()


def test_rescata_con_etiquetas_y_sin_ellas():
    llamadas, resto = rescatar_llamadas(
        '<tool_call>{"name": "escribir_fichero", "arguments": {"ruta": "x.txt"}}</tool_call>',
        NOMBRES,
    )
    assert llamadas[0]["function"]["name"] == "escribir_fichero"
    assert resto == ""


def test_no_toca_el_texto_normal():
    texto = "He creado la carpeta fotos en {tu} escritorio."
    assert rescatar_llamadas(texto, NOMBRES) == ([], texto)


def test_ignora_un_json_que_no_es_una_herramienta_nuestra():
    texto = 'Mira: {"name": "otra_cosa", "arguments": {}}'
    llamadas, resto = rescatar_llamadas(texto, NOMBRES)
    assert llamadas == []
    assert "otra_cosa" in resto


def test_error_de_ollama_se_cuenta(monkeypatch):
    monkeypatch.setattr(
        "maripepis.llm.ollama_provider.httpx.post",
        lambda url, json=None, timeout=None: httpx.Response(500, text="petó"),
    )
    with pytest.raises(RuntimeError, match="500"):
        OllamaProvider(model="x").run_tools_turn("s", [], [_Tool()], lambda n, a: "")
