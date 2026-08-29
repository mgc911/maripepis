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


def test_al_agotar_las_vueltas_pide_un_cierre_en_condiciones(monkeypatch):
    # Con leer + consultar + escribir, un turno normal encadena varias llamadas.
    # Si se acaban las vueltas a media faena, lo que queda en `texto` es medio
    # turno («el contenido del fichero es el siguiente:»), no una respuesta.
    llamada = {"function": {"name": "ejecutar_comando", "arguments": {}}}
    a_medias = {"content": "El contenido del archivo es el siguiente:",
                "tool_calls": [llamada]}
    respuestas = Respuestas(*[a_medias] * 8, {"content": "Te lo he actualizado."})
    provider = _provider(monkeypatch, respuestas)

    reply = provider.run_tools_turn("sé breve", [], [_Tool()], lambda n, a: "Hecho.",
                                    max_iters=8)

    assert reply == "Te lo he actualizado."
    # Y el cierre se pide SIN herramientas, para que no siga llamando.
    assert "tools" not in respuestas.peticiones[-1]


def test_si_el_modelo_remata_solo_no_se_pide_ningun_cierre(monkeypatch):
    llamada = {"function": {"name": "ejecutar_comando", "arguments": {}}}
    respuestas = Respuestas({"content": "", "tool_calls": [llamada]},
                            {"content": "Ya está la carpeta."})
    provider = _provider(monkeypatch, respuestas)

    reply = provider.run_tools_turn("sé breve", [], [_Tool()], lambda n, a: "Hecho.")

    assert reply == "Ya está la carpeta."
    assert len(respuestas.peticiones) == 2      # ni una petición de más


def test_insiste_una_vez_si_el_modelo_solo_anuncia_lo_que_hara(monkeypatch):
    # Lo visto con qwen2.5:7b: «he leído el fichero; ahora voy a actualizarlo», y
    # ahí se acababa el turno. El fichero se quedaba igual y sonaba a éxito.
    llamada = {"function": {"name": "ejecutar_comando", "arguments": {}}}
    respuestas = Respuestas(
        {"content": "He leído el archivo. Ahora voy a actualizarlo."},
        {"content": "", "tool_calls": [llamada]},
        {"content": "Ya te lo he actualizado."},
    )
    provider = _provider(monkeypatch, respuestas)

    reply = provider.run_tools_turn("sé breve", [], [_Tool()], lambda n, a: "Hecho.")

    assert reply == "Ya te lo he actualizado."
    assert len(respuestas.peticiones) == 3
    # `messages` es la misma lista en todas las peticiones (se va ampliando), así
    # que el empujón se busca en el conjunto, no en una posición.
    enviados = [m.get("content", "") for m in respuestas.peticiones[-1]["messages"]]
    assert any("hazlo ahora" in m.lower() for m in enviados)


def test_no_insiste_mas_de_dos_veces(monkeypatch):
    # Dos y no una: a la primera el modelo suele reaccionar pero llamando a la
    # herramienta equivocada. A partir de la tercera es dar vueltas.
    respuestas = Respuestas(*[{"content": "Ahora voy a hacerlo."}] * 6)
    provider = _provider(monkeypatch, respuestas)

    provider.run_tools_turn("sé breve", [], [_Tool()], lambda n, a: "Hecho.")

    assert len(respuestas.peticiones) == 3      # el intento y dos empujones


def test_una_respuesta_normal_no_se_confunde_con_un_anuncio(monkeypatch):
    respuestas = Respuestas({"content": "Hoy en Alicante hace 24 grados y sol."})
    provider = _provider(monkeypatch, respuestas)

    reply = provider.run_tools_turn("sé breve", [], [_Tool()], lambda n, a: "Hecho.")

    assert reply == "Hoy en Alicante hace 24 grados y sol."
    assert len(respuestas.peticiones) == 1


class ExecuteQueRecuerda:
    """Como Acciones: sabe qué herramientas salieron bien en el turno."""

    def __init__(self, ok=()):
        self.ok = set(ok)
        # Coherente con las de verdad: si algo salió bien, hubo llamada.
        self.llamadas = len(self.ok)

    def herramientas_ok(self):
        return self.ok

    def __call__(self, nombre, args):
        self.llamadas += 1
        self.ok.add(nombre)
        return "Hecho."


def test_insiste_si_dice_haber_guardado_sin_llamar_a_escribir(monkeypatch):
    # Lo medido con qwen2.5:7b: consulta el tiempo, no escribe, y remata con
    # «te lo he guardado». Desmentirlo después es el último recurso; lo que
    # arregla el turno es insistirle aquí, que casi siempre lo hace a la segunda.
    llamada = {"function": {"name": "escribir_fichero", "arguments": {}}}
    respuestas = Respuestas(
        {"content": "Te lo he guardado en documentos."},          # mentira
        {"content": "", "tool_calls": [llamada]},                  # tras insistir
        {"content": "Ya está guardado de verdad."},
    )
    provider = _provider(monkeypatch, respuestas)
    execute = ExecuteQueRecuerda(ok={"consultar_tiempo"})

    reply = provider.run_tools_turn("sé breve", [], [_Tool()], execute)

    assert reply == "Ya está guardado de verdad."
    assert "escribir_fichero" in execute.ok
    # Y se le nombra la herramienta que falta, que si no vuelve a llamar a la
    # que ya había llamado.
    enviados = [m.get("content", "") for m in respuestas.peticiones[-1]["messages"]]
    assert any("Llama AHORA a escribir_fichero" in m for m in enviados)


def test_no_insiste_si_de_verdad_llamo_a_la_herramienta(monkeypatch):
    respuestas = Respuestas({"content": "Te lo he guardado en documentos."})
    provider = _provider(monkeypatch, respuestas)

    reply = provider.run_tools_turn("sé breve", [], [_Tool()],
                                    ExecuteQueRecuerda(ok={"escribir_fichero"}))

    assert reply == "Te lo he guardado en documentos."
    assert len(respuestas.peticiones) == 1
