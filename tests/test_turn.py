import logging
import re

from maripepis.llm.conversation import Conversation
from maripepis.turn import reply_turn, stream_reply_text

LOG = logging.getLogger("test")


class FakeProvider:
    """Proveedor de mentira: trocea la respuesta en tokens."""

    label = "Fake · llm"

    def __init__(self, reply="Hola. Qué tal.", tools_reply=None, tools_fallan=False):
        self.reply = reply
        self.tools_reply = tools_reply
        self.tools_fallan = tools_fallan
        self.vistos: list[list[dict]] = []

    def stream_reply(self, system_prompt, messages):
        self.vistos.append(list(messages))
        return iter(re.findall(r"\S+\s*", self.reply))  # palabras, sin perder espacios

    def run_tools_turn(self, system_prompt, messages, tools, execute):
        self.vistos.append(list(messages))
        if self.tools_fallan:
            raise RuntimeError("este modelo no sabe llamar herramientas")
        return self.tools_reply


class ProviderRoto:
    label = "Roto"

    def stream_reply(self, system_prompt, messages):
        raise RuntimeError("ollama no responde")


class FakeSpeech:
    def __init__(self) -> None:
        self.dichas: list[str] = []
        self.parada = False

    def say(self, text: str) -> None:
        self.dichas.append(text)

    def stop(self) -> None:
        self.parada = True


def _conv():
    return Conversation(system_prompt="sé breve", max_history=10)


def test_devuelve_la_respuesta_y_actualiza_el_historial():
    conv = _conv()
    reply = reply_turn(FakeProvider("Hola qué tal"), conv, "buenas", LOG)

    assert reply == "Hola qué tal"
    assert conv.messages == [
        {"role": "user", "content": "buenas"},
        {"role": "assistant", "content": "Hola qué tal"},
    ]


def test_on_token_recibe_los_fragmentos():
    trozos: list[str] = []
    reply_turn(FakeProvider("uno dos tres"), _conv(), "va", LOG, on_token=trozos.append)

    assert "".join(trozos) == "uno dos tres"


def test_habla_frase_a_frase():
    # iter_sentences corta en la puntuación (con un mínimo de caracteres).
    speech = FakeSpeech()
    reply_turn(FakeProvider("Buenas tardes a todos. Qué tal estáis."),
               _conv(), "va", LOG, speech=speech)

    assert speech.dichas == ["Buenas tardes a todos. ", "Qué tal estáis."]


def test_fallo_del_proveedor_deshace_el_turno_de_usuario():
    conv = _conv()
    speech = FakeSpeech()
    reply = reply_turn(ProviderRoto(), conv, "buenas", LOG, speech=speech)

    assert reply is None
    assert conv.messages == []       # el turno de usuario no se queda colgado
    assert speech.parada


def test_usa_las_herramientas_si_las_hay():
    provider = FakeProvider(tools_reply="Abriendo Firefox.")
    speech = FakeSpeech()
    reply = reply_turn(
        provider, _conv(), "abre firefox", LOG,
        speech=speech, tools=[object()], execute=lambda n, a: "",
    )

    assert reply == "Abriendo Firefox."
    assert speech.dichas == ["Abriendo Firefox."]


def test_cae_a_streaming_si_las_herramientas_fallan():
    provider = FakeProvider("respuesta normal", tools_fallan=True)
    conv = _conv()
    reply = reply_turn(
        provider, conv, "hola", LOG, tools=[object()], execute=lambda n, a: "",
    )

    assert reply == "respuesta normal"
    assert conv.messages[-1] == {"role": "assistant", "content": "respuesta normal"}


def test_stream_reply_text_sin_voz_ni_callback():
    conv = _conv()
    conv.add_user("hola")
    assert stream_reply_text(FakeProvider("a b c"), conv) == "a b c"


class AccionesFalsas:
    """Como las de verdad: se acuerdan de si lo último salió bien o no."""

    def __init__(self, ultimo_fallo=None):
        self.ultimo_fallo = ultimo_fallo
        self.reseteada = False

    def reset(self):
        self.reseteada = True

    def __call__(self, nombre, args):
        return "Hecho."


def test_desmiente_al_modelo_si_la_accion_fallo():
    # El 7B lee «NO he ejecutado nada» y remata con un «ya la tienes». Quien
    # escucha no ve la pantalla: se queda tan contento sin carpeta.
    provider = FakeProvider(tools_reply="He creado la carpeta fotos en tu escritorio.")
    acciones = AccionesFalsas("«mkdir -p ~/Desktop/fotos» ha fallado con código 1")
    speech = FakeSpeech()

    reply = reply_turn(provider, _conv(), "créame una carpeta fotos", LOG,
                       speech=speech, tools=[object()], execute=acciones)

    assert "no ha funcionado" in reply.lower()
    assert "mkdir" in reply
    assert any("no ha funcionado" in d.lower() for d in speech.dichas)  # y lo dice en voz


def test_no_desmiente_lo_que_ha_ido_bien():
    provider = FakeProvider(tools_reply="He creado la carpeta fotos.")
    reply = reply_turn(provider, _conv(), "créame una carpeta", LOG,
                       tools=[object()], execute=AccionesFalsas())
    assert reply == "He creado la carpeta fotos."


def test_no_repite_el_aviso_si_el_modelo_ya_lo_reconoce():
    provider = FakeProvider(tools_reply="No he podido crearla: esa carpeta no existe.")
    reply = reply_turn(provider, _conv(), "créame una carpeta", LOG,
                       tools=[object()], execute=AccionesFalsas("no existe la carpeta"))
    assert reply.count("no ha funcionado") == 0


def test_cada_turno_empieza_sin_los_fallos_del_anterior():
    acciones = AccionesFalsas()
    reply_turn(FakeProvider(tools_reply="Hecho."), _conv(), "haz algo", LOG,
               tools=[object()], execute=acciones)
    assert acciones.reseteada
