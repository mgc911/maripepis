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
        raise RuntimeError("el motor no responde")


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
    """Como las de verdad: se acuerdan de si lo último salió bien o no.

    `llamadas` arranca en 1 porque estas son las de un turno en el que el modelo
    SÍ usó una herramienta. El turno en el que no llamó a nada es otro caso, y
    tiene sus propios tests: `llamadas=0`.
    """

    def __init__(self, ultimo_fallo=None, llamadas=1):
        self.ultimo_fallo = ultimo_fallo
        self.llamadas = llamadas
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


# --- El turno en el que no se llamó a nada --------------------------------
# El fallo que se vio en el journal: al pedirle actualizar un fichero ya creado,
# el modelo contestaba «he actualizado el archivo» en 0,4 s, sin pasar por
# ninguna herramienta. El fichero se quedaba igual y no había ni un fallo que
# enseñar, porque no se llegó a intentar nada.

def test_desmiente_al_modelo_si_no_llamo_a_ninguna_herramienta():
    provider = FakeProvider(tools_reply="He actualizado el archivo resumen_tiempo.txt.")
    speech = FakeSpeech()

    reply = reply_turn(provider, _conv(), "actualiza el archivo", LOG,
                       speech=speech, tools=[object()], execute=AccionesFalsas(llamadas=0))

    assert "no he hecho nada" in reply.lower()
    assert any("no he hecho nada" in d.lower() for d in speech.dichas)  # y lo dice en voz


def test_no_desmiente_una_respuesta_que_no_presume_de_nada():
    # Sin herramientas y sin cantar victoria: charla normal, no se toca.
    provider = FakeProvider(tools_reply="Mañana en Alicante suele hacer calor.")
    reply = reply_turn(provider, _conv(), "qué tal el tiempo", LOG,
                       tools=[object()], execute=AccionesFalsas(llamadas=0))
    assert reply == "Mañana en Alicante suele hacer calor."


def test_no_desmiente_si_el_modelo_ya_reconoce_que_no_puede():
    provider = FakeProvider(tools_reply="No he podido mirar el fichero.")
    reply = reply_turn(provider, _conv(), "revísalo", LOG,
                       tools=[object()], execute=AccionesFalsas(llamadas=0))
    assert reply == "No he podido mirar el fichero."


def test_sobre_un_execute_que_no_cuenta_llamadas_no_se_afirma_nada():
    # Un invocable pelado no lleva la cuenta: no se le puede desmentir.
    provider = FakeProvider(tools_reply="He creado la carpeta fotos.")
    reply = reply_turn(provider, _conv(), "crea una carpeta", LOG,
                       tools=[object()], execute=lambda n, a: "")
    assert reply == "He creado la carpeta fotos."


# --- El historial guarda la respuesta y nada más ---------------------------

def test_en_el_historial_solo_va_la_respuesta():
    # Hubo una versión que añadía una nota con lo que habían hecho las
    # herramientas. Medido: con ella, el turno siguiente no llamaba a ninguna
    # (0 de 6) porque leía que ya estaba hecho. Para eso está leer_fichero.
    conv = _conv()
    reply_turn(FakeProvider(tools_reply="Te lo he dejado en Documentos."), conv,
               "hazme un resumen", LOG, tools=[object()],
               execute=AccionesQueRecuerdan(ok={"escribir_fichero"}, llamadas=1))

    assert conv.messages[-1] == {"role": "assistant",
                                 "content": "Te lo he dejado en Documentos."}


def test_el_turno_se_resetea_aunque_no_haya_herramientas():
    # El reseteo vive fuera de la rama de herramientas: lo que se apunta se
    # consulta pase lo que pase, también si se cae al streaming.
    acciones = AccionesFalsas()
    reply_turn(FakeProvider("respuesta normal"), _conv(), "hola", LOG, execute=acciones)
    assert acciones.reseteada


# --- Presumir de una herramienta que no se llamó ---------------------------
# Contar llamadas no basta: el modelo consulta el tiempo, no escribe nada, y
# remata con «te lo he guardado en documentos». Llamó a *algo*, así que el
# contador se queda tan ancho.

class AccionesQueRecuerdan(AccionesFalsas):
    """Como las de verdad: sabe QUÉ herramientas salieron bien en el turno."""

    def __init__(self, ok=(), **kw):
        super().__init__(**kw)
        self.ok = set(ok)

    def herramientas_ok(self):
        return self.ok


def test_desmiente_al_que_dice_haber_guardado_sin_escribir_nada():
    provider = FakeProvider(tools_reply="Te lo he guardado en documentos.")
    acciones = AccionesQueRecuerdan(ok={"consultar_tiempo"}, llamadas=1)

    reply = reply_turn(provider, _conv(), "guárdame el tiempo", LOG,
                       tools=[object()], execute=acciones)

    assert "no he llegado a escribir el fichero" in reply


def test_no_desmiente_si_la_herramienta_que_hacia_falta_si_se_llamo():
    provider = FakeProvider(tools_reply="Te lo he guardado en documentos.")
    acciones = AccionesQueRecuerdan(ok={"consultar_tiempo", "escribir_fichero"},
                                    llamadas=2)

    reply = reply_turn(provider, _conv(), "guárdame el tiempo", LOG,
                       tools=[object()], execute=acciones)

    assert reply == "Te lo he guardado en documentos."


def test_desmiente_al_que_dice_haber_abierto_algo_sin_abrirlo():
    provider = FakeProvider(tools_reply="Ya te he abierto Firefox.")
    acciones = AccionesQueRecuerdan(ok={"ejecutar_comando"}, llamadas=1)

    reply = reply_turn(provider, _conv(), "abre firefox", LOG,
                       tools=[object()], execute=acciones)

    assert "no he abierto nada" in reply


def test_desmiente_al_que_dice_haber_llamado_a_una_herramienta_que_no_llamo():
    # Esta se pilla sola: la respuesta dice el nombre de la herramienta.
    provider = FakeProvider(tools_reply=(
        "He llamado a `escribir_fichero` para crear el archivo tiempo.txt."))
    acciones = AccionesQueRecuerdan(ok={"consultar_tiempo"}, llamadas=1)

    reply = reply_turn(provider, _conv(), "guárdalo", LOG,
                       tools=[object()], execute=acciones)

    assert "no he llamado a escribir_fichero" in reply


def test_desmiente_tambien_la_mentira_en_pasiva():
    # llama3.1:8b no dice «he guardado», dice «el archivo se ha modificado».
    # Toda la detección estaba montada sobre la primera persona y se le escapaba.
    provider = FakeProvider(tools_reply="El archivo tiempo.txt se ha modificado.")
    acciones = AccionesQueRecuerdan(ok={"consultar_tiempo"}, llamadas=1)

    reply = reply_turn(provider, _conv(), "modifícalo", LOG,
                       tools=[object()], execute=acciones)

    assert "no he llegado a escribir el fichero" in reply


# --- El turno que acaba preguntando «¿te lo mando?» -------------------------

class AccionesDeWhatsApp(AccionesQueRecuerdan):
    """Un turno del modo envío: dos herramientas puestas y un registro de llamadas.

    `registro` es lo que distingue «no se ha intentado confirmar» de «se ha
    intentado y se ha dicho que no», que es justo lo que hay que saber para no
    dar por averiado un turno que ha ido como tenía que ir.
    """

    nombres = {"preparar_mensaje_whatsapp", "enviar_mensaje_whatsapp"}

    def __init__(self, ok=("preparar_mensaje_whatsapp",), registro=(), **kw):
        super().__init__(ok=ok, **kw)
        self.registro = [(n, {}, "NO he enviado nada: ...") for n in registro]


def test_un_si_que_el_modelo_se_da_a_si_mismo_no_averia_el_turno():
    """El 7B confirma sin dejar hablar al usuario, la herramienta se niega, y el
    turno acaba bien: con el mensaje preparado y la pregunta hecha.

    Sin esto, quien escucha oye su pregunta y detrás un «en realidad no ha
    funcionado» que le hace pensar que el wasap se ha perdido.
    """
    provider = FakeProvider(tools_reply="Le mando a Edu «llego en diez». ¿Se lo mando?")
    acciones = AccionesDeWhatsApp(
        registro=("preparar_mensaje_whatsapp", "enviar_mensaje_whatsapp"),
        ultimo_fallo="acabo de prepararlo en este mismo turno", llamadas=2)

    reply = reply_turn(provider, _conv(), "mándale un wasap a Edu", LOG,
                       tools=[object()], execute=acciones)

    assert reply == "Le mando a Edu «llego en diez». ¿Se lo mando?"


def test_pero_si_encima_lo_da_por_enviado_se_le_desmiente():
    """Callarse el aviso de avería no es callarse la mentira."""
    provider = FakeProvider(tools_reply="Ya se lo he enviado a Edu.")
    acciones = AccionesDeWhatsApp(
        registro=("preparar_mensaje_whatsapp", "enviar_mensaje_whatsapp"),
        ultimo_fallo="acabo de prepararlo en este mismo turno", llamadas=2)

    reply = reply_turn(provider, _conv(), "mándale un wasap a Edu", LOG,
                       tools=[object()], execute=acciones)

    assert "no lo he enviado todavía" in reply
    assert "dime que sí" in reply


def test_un_fallo_de_verdad_en_ese_turno_se_sigue_diciendo():
    """La excepción es solo para la confirmación que se niega a sí misma."""
    provider = FakeProvider(tools_reply="Le he mandado el wasap y te he abierto la agenda.")
    acciones = AccionesDeWhatsApp(registro=("preparar_mensaje_whatsapp",),
                                  ultimo_fallo="«agenda» no está instalada", llamadas=2)

    reply = reply_turn(provider, _conv(), "mándale un wasap y abre la agenda", LOG,
                       tools=[object()], execute=acciones)

    assert "no ha funcionado" in reply.lower()
    assert "agenda" in reply
