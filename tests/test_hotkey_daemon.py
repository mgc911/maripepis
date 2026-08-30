import json
import logging
import re
import socket

import pytest

from maripepis.hotkey import daemon as mod
from maripepis.hotkey.daemon import (
    IDLE,
    LOADING,
    PROCESSING,
    RECORDING,
    SPEAKING,
    THINKING,
    HotkeyDaemon,
)
from maripepis.llm.conversation import Conversation
from maripepis.tools.base import Tool
from maripepis.tools.runner import Acciones

LOG = logging.getLogger("test")
WAV = b"RIFF....WAVE"


class FakeRecorder:
    """Grabadora de mentira: `start()` deja el turno listo para cosechar."""

    def __init__(self, wav=WAV) -> None:
        self.wav = wav
        self.stop_reason = "orden"
        self.arrancada = 0
        self.parada = 0
        self.cancelada = 0
        self.a_tiempo = True

    def start(self) -> None:
        self.arrancada += 1

    def request_stop(self) -> None:
        self.parada += 1

    def wait_finished(self, timeout=None) -> bool:
        return self.a_tiempo

    def harvest(self):
        return self.wav

    def cancel(self) -> None:
        self.cancelada += 1
        self.wav = None


class FakeSTT:
    label = "Fake · whisper"

    def __init__(self, text="hola maripepis", falla=False) -> None:
        self.text = text
        self.falla = falla
        self.llamadas = 0

    def transcribe(self, wav: bytes) -> str:
        self.llamadas += 1
        if self.falla:
            raise RuntimeError("cuda se ha caído")
        return self.text


class FakeProvider:
    label = "Fake · llm"

    def __init__(self, reply="Muy buenas."):
        self.reply = reply
        self.turnos = 0

    def stream_reply(self, system_prompt, messages):
        self.turnos += 1
        return iter(re.findall(r"\S+\s*", self.reply))


class ProviderConAcciones(FakeProvider):
    """Provider que llama a una herramienta antes de contestar."""

    def __init__(self, llamadas=(("ejecutar_comando", {"comando": "mkdir -p ~/fotos"}),),
                 reply="Ya está.") -> None:
        super().__init__(reply)
        self.llamadas = llamadas

    def run_tools_turn(self, system_prompt, messages, tools, execute):
        self.turnos += 1
        for nombre, args in self.llamadas:
            execute(nombre, args)
        return self.reply


class FakeSpeech:
    label = "Fake · voz"

    def __init__(self) -> None:
        self.dichas: list[str] = []
        self.paradas = 0
        self.esperas = 0

    def say(self, text: str) -> None:
        self.dichas.append(text)

    def stop(self) -> None:
        self.paradas += 1

    def wait(self) -> None:
        self.esperas += 1


class FakeNotifier:
    def __init__(self) -> None:
        self.avisos: list[tuple[str, str]] = []

    def show(self, summary, body="", **k) -> None:
        self.avisos.append((summary, body))

    def error(self, body) -> None:
        self.avisos.append(("⚠️", body))

    def resumenes(self) -> str:
        return " | ".join(s for s, _ in self.avisos)


class HiloInerte:
    """Hilo que no arranca: los tests ejecutan el turno a mano, en orden."""

    def __init__(self, target=None, args=(), **kwargs) -> None:
        self.target = target
        self.args = args

    def start(self) -> None:
        pass


@pytest.fixture(autouse=True)
def sin_hilos(monkeypatch):
    monkeypatch.setattr(mod.threading, "Thread", HiloInerte)


@pytest.fixture(autouse=True)
def sin_ventana(monkeypatch):
    """Ningún test abre una ventana de verdad: se apunta a quién se intentó abrir."""
    lanzadas: list[str] = []
    monkeypatch.setattr(mod.window, "launch",
                        lambda ruta, logger, **k: (lanzadas.append(ruta), True)[1])
    return lanzadas


@pytest.fixture(autouse=True)
def portapapeles(monkeypatch):
    """Nada de wl-copy real: guarda lo copiado."""
    copiado: list[str] = []
    monkeypatch.setattr(mod.clipboard, "copy",
                        lambda text, **k: (copiado.append(text), True)[1])
    monkeypatch.setattr(mod.clipboard, "paste", lambda **k: copiado.append("<pegado>"))
    return copiado


def build(cfg=None, *, stt=None, recorder=None, provider=None, speech=None,
          tools=None, execute=None):
    """Demonio listo para `handle()`, sin socket ni hilos de fondo."""
    d = HotkeyDaemon(
        {"hotkey": cfg or {}},
        provider or FakeProvider(),
        Conversation("sé breve"),
        LOG,
        stt=stt or FakeSTT(),
        recorder=recorder or FakeRecorder(),
        speech=speech,
        tools=tools,
        execute=execute,
        notifier=FakeNotifier(),
    )
    d._state = IDLE  # en producción lo hace serve() tras cargar Whisper
    return d


def turno(d, mode="assistant"):
    """Ejecuta un turno completo en el hilo del test (determinista)."""
    resp = d.handle({"cmd": "start", "mode": mode})
    if resp["ok"]:
        d._turn_worker(mode, d._turn)
    return resp


# ── máquina de estados ───────────────────────────────────────────────────

def test_start_pasa_a_grabando():
    d = build()
    resp = d.handle({"cmd": "start", "mode": "assistant"})

    assert resp == {"ok": True, "state": RECORDING}
    assert d.recorder.arrancada == 1
    assert "🎙️ Grabando…" in d.notifier.resumenes()


def test_start_en_dictado_avisa_distinto():
    d = build()
    d.handle({"cmd": "start", "mode": "dictation"})

    assert "🎙️ Dictando…" in d.notifier.resumenes()


def test_stop_en_idle_es_noop_silencioso():
    # Pasa siempre que la grabación se cortó sola por silencio antes de soltar.
    d = build()
    resp = d.handle({"cmd": "stop"})

    assert resp == {"ok": True, "state": IDLE}
    assert d.recorder.parada == 0
    assert d.notifier.avisos == []


def test_stop_grabando_pide_el_corte():
    d = build()
    d.handle({"cmd": "start", "mode": "assistant"})
    d.handle({"cmd": "stop"})

    assert d.recorder.parada == 1


def test_start_mientras_graba_devuelve_ocupado():
    # Soltar SHIFT antes que Z cuela un `start` de más: hay que ignorarlo.
    d = build()
    d.handle({"cmd": "start", "mode": "assistant"})
    resp = d.handle({"cmd": "start", "mode": "assistant"})

    assert resp["ok"] is False
    assert resp["error"] == "ocupado"
    assert d.recorder.arrancada == 1


def test_start_mientras_piensa_devuelve_ocupado():
    d = build()
    d._state = PROCESSING
    resp = d.handle({"cmd": "start", "mode": "assistant"})

    assert resp["error"] == "ocupado"


def test_start_mientras_carga_avisa():
    d = build()
    d._state = LOADING
    resp = d.handle({"cmd": "start", "mode": "assistant"})

    assert resp == {"ok": False, "error": "cargando", "state": LOADING}
    assert "arrancando" in d.notifier.resumenes()


def test_start_mientras_habla_corta_la_voz():
    speech = FakeSpeech()
    d = build(speech=speech)
    d._state = SPEAKING

    resp = d.handle({"cmd": "start", "mode": "assistant"})

    assert speech.paradas == 1          # barge-in
    assert resp == {"ok": True, "state": RECORDING}


def test_cancel_vuelve_a_reposo():
    d = build()
    d.handle({"cmd": "start", "mode": "assistant"})
    resp = d.handle({"cmd": "cancel"})

    assert resp == {"ok": True, "state": IDLE}
    assert d.recorder.cancelada == 1


def test_status_y_ping():
    d = build()
    # Lleva el motor en uso: es lo que deja a la ventana abrir con el nombre
    # correcto en la cabecera en vez de con uno supuesto.
    assert d.handle({"cmd": "status"}) == {"ok": True, "state": IDLE,
                                           "backend": "claude-code"}
    assert d.handle({"cmd": "ping"})["state"] == IDLE


def test_orden_desconocida():
    d = build()
    assert d.handle({"cmd": "bailar"})["ok"] is False


def test_modo_desconocido():
    d = build()
    assert d.handle({"cmd": "start", "mode": "cantar"})["ok"] is False


def test_micro_que_no_abre_vuelve_a_reposo():
    class RecRoto(FakeRecorder):
        def start(self):
            raise RuntimeError("dispositivo ocupado")

    d = build(recorder=RecRoto())
    resp = d.handle({"cmd": "start", "mode": "assistant"})

    assert resp["ok"] is False
    assert d.state == IDLE
    assert "dispositivo ocupado" in d.notifier.resumenes() + d.notifier.avisos[-1][1]


# ── el turno completo ────────────────────────────────────────────────────

def test_modo_asistente_llama_al_llm_y_habla():
    provider = FakeProvider("Muy buenas, aquí estoy.")
    speech = FakeSpeech()
    d = build(provider=provider, speech=speech)

    turno(d)

    assert provider.turnos == 1
    assert speech.dichas == ["Muy buenas, aquí estoy."]
    assert speech.esperas == 1
    assert d.conversation.messages == [
        {"role": "user", "content": "hola maripepis"},
        {"role": "assistant", "content": "Muy buenas, aquí estoy."},
    ]
    assert d.state == IDLE
    assert "🗣️ Has dicho" in d.notifier.resumenes()
    assert "🐙 Maripepis" in d.notifier.resumenes()


def test_modo_dictado_copia_y_no_toca_el_historial(portapapeles):
    provider = FakeProvider()
    d = build(provider=provider, stt=FakeSTT("dos cervezas por favor"))

    turno(d, "dictation")

    assert portapapeles == ["dos cervezas por favor"]
    assert provider.turnos == 0
    assert d.conversation.messages == []
    assert "📋 Copiado al portapapeles" in d.notifier.resumenes()


def test_dictado_no_pega_por_defecto(portapapeles):
    d = build(stt=FakeSTT("hola"))
    turno(d, "dictation")

    assert "<pegado>" not in portapapeles


def test_dictado_pega_si_se_activa(portapapeles):
    d = build({"auto_paste": True}, stt=FakeSTT("hola"))
    turno(d, "dictation")

    assert "<pegado>" in portapapeles


def test_sin_voz_no_llama_al_llm():
    provider = FakeProvider()
    d = build(provider=provider, recorder=FakeRecorder(wav=None))

    turno(d)

    assert provider.turnos == 0
    assert "🤫 No te he oído" in d.notifier.resumenes()
    assert d.state == IDLE


def test_transcripcion_vacia_no_llama_al_llm():
    provider = FakeProvider()
    d = build(provider=provider, stt=FakeSTT(""))

    turno(d)

    assert provider.turnos == 0
    assert "🤫 No te he entendido" in d.notifier.resumenes()


def test_fallo_de_transcripcion_vuelve_a_idle():
    d = build(stt=FakeSTT(falla=True))

    turno(d)

    assert d.state == IDLE
    assert "cuda se ha caído" in d.notifier.avisos[-1][1]


def test_grabacion_colgada_se_corta():
    rec = FakeRecorder()
    rec.a_tiempo = False
    d = build(recorder=rec)

    turno(d)

    assert rec.cancelada == 1
    assert d.state == IDLE


def test_no_aplica_wake_word_ni_frases_de_salida():
    # Pulsar la tecla ya es hablarle a ella: ni palabra de activación, ni "salir".
    provider = FakeProvider()
    d = build(stt=FakeSTT("salir"))
    d.cfg["app"] = {"wake_word": "oye maripepis"}
    d.provider = provider

    turno(d)

    assert provider.turnos == 1
    assert d.conversation.messages[0] == {"role": "user", "content": "salir"}


def test_mantiene_el_contexto_entre_pulsaciones():
    d = build(stt=FakeSTT("y mañana?"))

    turno(d)
    turno(d)

    assert len(d.conversation.messages) == 4


def test_reinicia_el_contexto_tras_la_inactividad(monkeypatch):
    d = build({"context_timeout_s": 300})

    turno(d)
    # «Dos horas después» tiene que serlo del turno anterior, no de un 10.000
    # absoluto: `time.monotonic()` cuenta desde el arranque de la máquina, así que
    # ese número dejaba de estar en el futuro en cuanto el equipo llevaba encendido
    # 2 h 47 min, y el test fallaba solo por eso, sin que nadie hubiera tocado nada.
    monkeypatch.setattr(mod.time, "monotonic", lambda: d._last_turn + 7_200)  # +2 h
    turno(d)

    assert len(d.conversation.messages) == 2  # solo el turno nuevo


def test_contexto_sin_caducidad():
    d = build({"context_timeout_s": 0})

    turno(d)
    turno(d)

    assert len(d.conversation.messages) == 4


def test_turno_superado_no_pisa_el_estado():
    # Barge-in: llega una pulsación nueva mientras el turno viejo va terminando.
    d = build(speech=FakeSpeech())
    d.handle({"cmd": "start", "mode": "assistant"})
    viejo = d._turn
    d._turn += 1                       # simula la pulsación nueva
    d._state = RECORDING

    d._turn_worker("assistant", viejo)

    assert d.state == RECORDING        # el turno viejo no lo mandó a reposo

# ── la ventana de chat ───────────────────────────────────────────────────


class FakeVisor:
    """Ventana de mentira: guarda las líneas que le empujan."""

    def __init__(self, rompe=False) -> None:
        self.lineas: list[dict] = []
        self.rompe = rompe
        self.cerrado = 0

    def settimeout(self, t) -> None:
        pass

    def sendall(self, raw: bytes) -> None:
        if self.rompe:
            raise OSError("tubería rota")
        for linea in raw.decode().splitlines():
            self.lineas.append(json.loads(linea))

    def close(self) -> None:
        self.cerrado += 1

    def tipos(self) -> list[str]:
        return [e.get("event") for e in self.lineas]

    def de(self, tipo: str) -> list[dict]:
        return [e for e in self.lineas if e.get("event") == tipo]


def con_visor(d, visor=None) -> FakeVisor:
    """Engancha un visor al demonio como si se hubiera suscrito."""
    visor = visor or FakeVisor()
    assert d._add_subscriber(visor) is True
    return visor


def test_suscribirse_recibe_estado_e_historial():
    d = build()
    d.conversation.add_user("¿qué hora es?")
    d.conversation.add_assistant("Las cinco.")

    visor = con_visor(d)

    bienvenida = visor.lineas[0]
    assert bienvenida["event"] == "hello"
    assert bienvenida["state"] == IDLE
    assert bienvenida["history"] == [
        {"role": "user", "content": "¿qué hora es?"},
        {"role": "assistant", "content": "Las cinco."},
    ]


def test_visor_que_se_va_antes_de_la_bienvenida_no_se_apunta():
    d = build()
    assert d._add_subscriber(FakeVisor(rompe=True)) is False
    assert d._subscribers == []


def test_un_turno_se_cuenta_entero_por_el_socket():
    d = build(provider=FakeProvider("Muy buenas."))
    visor = con_visor(d)

    turno(d)

    assert visor.tipos()[:4] == ["hello", "state", "state", "user"]
    assert visor.de("user")[0]["text"] == "hola maripepis"
    assert visor.de("reply")[0]["text"] == "Muy buenas."
    # La respuesta se ve escribirse, no aparece de golpe cuando ya ha sonado.
    assert "".join(e["text"] for e in visor.de("delta")) == "Muy buenas."
    assert [e["state"] for e in visor.de("state")][-1] == IDLE


def test_lo_que_no_se_entiende_tambien_se_cuenta():
    d = build(stt=FakeSTT(""))
    visor = con_visor(d)

    turno(d)

    assert "🤫 No te he entendido" in [e["text"] for e in visor.de("notice")]


def test_los_fallos_se_cuentan_al_visor():
    d = build(stt=FakeSTT(falla=True))
    visor = con_visor(d)

    turno(d)

    assert "cuda se ha caído" in visor.de("error")[0]["text"]


def test_el_contexto_caducado_se_ve_en_la_ventana(monkeypatch):
    d = build({"context_timeout_s": 1})
    d._last_turn = 1.0
    monkeypatch.setattr(mod.time, "monotonic", lambda: 500.0)
    visor = con_visor(d)

    turno(d)

    assert visor.de("reset")


def test_un_visor_muerto_se_cae_solo_y_no_tumba_el_turno():
    d = build()
    visor = con_visor(d)
    visor.rompe = True

    turno(d)                       # nadie debería enterarse de que se ha ido

    assert d._subscribers == []
    assert visor.cerrado == 1
    assert d.conversation.messages[-1]["content"] == "Muy buenas."


def test_al_cerrar_el_demonio_se_sueltan_los_visores():
    d = build()
    visor = con_visor(d)

    d._shutdown()

    assert d._subscribers == []
    assert visor.cerrado == 1


# ── abrir la ventana ─────────────────────────────────────────────────────

def test_la_tecla_abre_la_ventana(sin_ventana):
    d = build()
    d.handle({"cmd": "start", "mode": "assistant"})

    assert sin_ventana == [d.socket_path]


def test_no_se_abre_otra_si_ya_hay_una_mirando(sin_ventana):
    d = build()
    con_visor(d)

    d.handle({"cmd": "start", "mode": "assistant"})

    assert sin_ventana == []


def test_dos_pulsaciones_seguidas_no_abren_dos_ventanas(sin_ventana):
    # Entre el `start` y la ventana suscrita pasan un par de segundos de GTK.
    d = build()
    d.handle({"cmd": "start", "mode": "assistant"})
    d.handle({"cmd": "cancel"})
    d.handle({"cmd": "start", "mode": "assistant"})

    assert len(sin_ventana) == 1


def test_una_ventana_cerrada_no_impide_abrir_la_siguiente(sin_ventana):
    # Cerrar la ventana no se nota en la primera escritura (así son los sockets):
    # sin comprobarlo a propósito, la pulsación siguiente se quedaría sin ventana.
    d = build()
    lado_demonio, lado_ventana = socket.socketpair()
    con_visor(d, lado_demonio)
    lado_ventana.close()

    d.handle({"cmd": "start", "mode": "assistant"})

    assert sin_ventana == [d.socket_path]
    assert d._subscribers == []


def test_el_dictado_no_abre_ventana(sin_ventana):
    d = build()
    d.handle({"cmd": "start", "mode": "dictation"})

    assert sin_ventana == []


def test_se_puede_apagar_la_ventana(sin_ventana):
    d = build({"window": False})
    d.handle({"cmd": "start", "mode": "assistant"})

    assert sin_ventana == []


# ── las acciones se ven ──────────────────────────────────────────────────

def con_acciones(resultado="Hecho: «mkdir -p ~/fotos» ha terminado bien, sin salida.",
                 provider=None):
    """Demonio con una herramienta de mentira que el provider llama en el turno."""
    tool = Tool(name="ejecutar_comando", description="", parameters={},
                handler=lambda args: resultado)
    return build(provider=provider or ProviderConAcciones(),
                 tools=[tool], execute=Acciones([tool], LOG))


def test_los_comandos_se_ven_en_la_ventana():
    d = con_acciones()
    visor = con_visor(d)

    turno(d)

    orden = visor.de("tool")[0]
    assert orden["text"] == "ejecutar_comando · mkdir -p ~/fotos"
    assert orden["ok"] is True
    # Primero lo que ha hecho y luego lo que cuenta: al revés no se entiende.
    assert visor.tipos().index("tool") < visor.tipos().index("reply")


def test_un_comando_que_falla_se_ve_como_tal():
    d = con_acciones("NO ha salido bien: «mkdir -p ~/fotos» ha fallado con código 1.")
    visor = con_visor(d)

    turno(d)

    assert visor.de("tool")[0]["ok"] is False


def test_sin_ventana_las_acciones_no_estorban():
    # El espectador escribe en sockets: sin ninguno mirando, el turno va igual.
    d = con_acciones()

    turno(d)

    assert d.conversation.messages[-1]["content"] == "Ya está."
    assert d.execute.llamadas == 1


def test_un_execute_que_no_es_de_maripepis_no_revienta():
    # `execute` es solo un invocable: si no trae `on_call`, no se le engancha nada.
    d = build(execute=lambda nombre, args: "Hecho: ya está.")
    assert d.execute is not None


# ── el switch de motor ───────────────────────────────────────────────────

class ProviderNube(FakeProvider):
    label = "Claude · nube"


def _con_backend(monkeypatch, provider=None, revienta=None):
    """Demonio con `[llm]` y una fábrica de proveedores controlada."""
    def _fabrica(cfg):
        if revienta:
            raise RuntimeError(revienta)
        return provider or ProviderNube()

    monkeypatch.setattr(mod, "build_provider", _fabrica)
    d = build()
    d.cfg = {"llm": {"backend": "claude-code", "claude_code": {"model": "sonnet"}}}
    d._backend = "claude-code"
    return d


def test_cambia_de_motor_en_caliente(monkeypatch):
    d = _con_backend(monkeypatch)

    resp = d.handle({"cmd": "backend", "value": "claude"})

    assert resp == {"ok": True, "state": IDLE, "backend": "claude"}
    assert d.provider.label == "Claude · nube"
    assert d.handle({"cmd": "status"})["backend"] == "claude"


def test_el_historial_sobrevive_al_cambio(monkeypatch):
    # Es lo que hace que cambiar sirva a mitad de conversación: `Conversation` es
    # neutra, así que se sigue por donde se iba pero con el otro motor.
    d = _con_backend(monkeypatch)
    d.conversation.add_user("hola")
    d.conversation.add_assistant("buenas")

    d.handle({"cmd": "backend", "value": "claude"})

    assert [m["content"] for m in d.conversation.messages] == ["hola", "buenas"]


def test_si_el_motor_nuevo_no_se_puede_construir_no_se_cambia(monkeypatch):
    # El caso de verdad: el backend `claude` sin ANTHROPIC_API_KEY revienta al
    # instanciarse. Cambiar y enterarse en el turno siguiente dejaría a maripepis
    # muda a mitad de conversación.
    d = _con_backend(monkeypatch, revienta="falta ANTHROPIC_API_KEY")
    antes = d.provider

    resp = d.handle({"cmd": "backend", "value": "claude"})

    assert resp["ok"] is False
    assert "ANTHROPIC_API_KEY" in resp["error"]
    assert d.provider is antes            # el de siempre sigue en su sitio
    assert d._backend == "claude-code"


def test_un_motor_que_no_existe_se_rechaza(monkeypatch):
    d = _con_backend(monkeypatch)
    resp = d.handle({"cmd": "backend", "value": "gemini"})

    assert resp["ok"] is False
    assert d._backend == "claude-code"


def test_no_se_cambia_de_motor_a_mitad_de_turno(monkeypatch):
    # El proveedor lo está usando otro hilo: cambiarlo debajo es pedir un lío.
    d = _con_backend(monkeypatch)
    d._state = PROCESSING

    resp = d.handle({"cmd": "backend", "value": "claude"})

    assert resp == {"ok": False, "error": "ocupado", "state": PROCESSING,
                    "backend": "claude-code"}


def test_cambiar_al_que_ya_esta_no_hace_nada(monkeypatch):
    d = _con_backend(monkeypatch)
    antes = d.provider
    assert d.handle({"cmd": "backend", "value": "claude-code"})["ok"] is True
    assert d.provider is antes


def test_el_cambio_se_les_cuenta_a_las_ventanas(monkeypatch):
    d = _con_backend(monkeypatch)
    visor = con_visor(d)

    d.handle({"cmd": "backend", "value": "claude"})

    evs = visor.de("backend")
    assert evs and evs[0]["backend"] == "claude"
    assert evs[0]["ok"] is True


def test_la_bienvenida_dice_en_que_motor_esta(monkeypatch):
    # Es lo que deja a la ventana escribir el motor en la cabecera al abrirse.
    d = _con_backend(monkeypatch)
    hola = d._hello()

    assert hola["backend"] == "claude-code"
    assert hola["backend_label"]


# ── pensar no es hablar ──────────────────────────────────────────────────


class ProviderLento(FakeProvider):
    """Provider que tarda en soltar el primer trozo, como Claude Code."""

    def __init__(self, reply="Ya está.", antes=None) -> None:
        super().__init__(reply)
        self.antes = antes or (lambda: None)

    def stream_reply(self, system_prompt, messages):
        self.turnos += 1

        def gen():
            self.antes()          # aquí es donde el de verdad busca en internet
            yield from re.findall(r"\S+\s*", self.reply)

        return gen()


def test_mientras_piensa_no_dice_que_habla():
    """El fallo que se veía con Claude Code: diez segundos de «🗣️ hablando…»
    sin que se oyera nada, porque el estado saltaba antes de haber respuesta."""
    estados_al_pensar = []
    d = build(provider=ProviderLento(antes=lambda: estados_al_pensar.append(d.state)))
    visor = con_visor(d)

    turno(d)

    # Cuando el proveedor aún estaba trabajando, el estado era «pensando».
    assert estados_al_pensar == [THINKING]
    secuencia = [e["state"] for e in visor.de("state")]
    assert secuencia == [RECORDING, PROCESSING, THINKING, SPEAKING, IDLE]


def test_el_salto_a_hablando_va_antes_del_primer_trozo():
    d = build(provider=FakeProvider("Muy buenas."))
    visor = con_visor(d)

    turno(d)

    tipos = visor.tipos()
    assert tipos.index("state", tipos.index("user")) < tipos.index("delta")


def test_sin_streaming_tambien_acaba_hablando():
    """`run_tools_turn` no da tokens: sin red de seguridad se quedaría pensando."""
    tools = [Tool("ejecutar_comando", "ejecuta", {}, lambda **k: "Hecho")]
    execute = Acciones(tools, LOG)
    d = build(provider=ProviderConAcciones(), tools=tools, execute=execute)
    visor = con_visor(d)

    turno(d)

    assert SPEAKING in [e["state"] for e in visor.de("state")]


def test_se_puede_interrumpir_mientras_piensa():
    """Antes el barge-in solo valía en SPEAKING; pensando dura mucho más."""
    voz = FakeSpeech()
    d = build(speech=voz)
    d._state = THINKING

    resp = d.handle({"cmd": "start", "mode": "assistant"})

    assert resp == {"ok": True, "state": RECORDING}
    assert voz.paradas == 1


def test_cancelar_mientras_piensa_calla_la_voz():
    voz = FakeSpeech()
    d = build(speech=voz)
    d._state = THINKING

    assert d.handle({"cmd": "cancel"}) == {"ok": True, "state": IDLE}
    assert voz.paradas == 1


# ── las herramientas del proveedor (Claude Code) ─────────────────────────


class ProviderConSusHerramientas(FakeProvider):
    """Provider que trae las suyas y avisa por `on_tool`, como Claude Code."""

    accepts_tools = False

    def __init__(self, reply="Hecho.") -> None:
        super().__init__(reply)
        self.on_tool = None
        self.on_file = None

    def stream_reply(self, system_prompt, messages):
        self.turnos += 1
        self.on_tool("Bash", "Bash · free -h", True)
        return iter(re.findall(r"\S+\s*", self.reply))


def test_las_herramientas_del_proveedor_salen_en_la_ventana():
    d = build(provider=ProviderConSusHerramientas())
    visor = con_visor(d)

    turno(d)

    herramienta = visor.de("tool")[0]
    assert herramienta["name"] == "Bash"
    assert herramienta["text"] == "Bash · free -h"
    assert herramienta["ok"] is True


def test_al_cambiar_de_motor_el_nuevo_tambien_avisa(monkeypatch):
    nuevo = ProviderConSusHerramientas()
    monkeypatch.setattr(mod, "build_provider", lambda cfg: nuevo)
    d = build()

    assert d.handle({"cmd": "backend", "value": "claude"})["ok"] is True
    assert nuevo.on_tool == d._al_ejecutar_el_proveedor
    assert nuevo.on_file == d._mostrar_documento


# ── el fichero escrito se ve en la ventana ───────────────────────────────


def test_al_escribir_un_fichero_se_manda_a_la_ventana(tmp_path):
    destino = tmp_path / "lista.md"

    def escribir(args):
        destino.write_text("# La compra\n- pan\n", encoding="utf-8")
        return f"Hecho: he escrito {destino}."

    tools = [Tool("escribir_fichero", "escribe", {}, escribir)]
    execute = Acciones(tools, LOG)
    d = build(provider=ProviderConAcciones(
        llamadas=(("escribir_fichero", {"ruta": str(destino)}),)), 
        tools=tools, execute=execute)
    visor = con_visor(d)

    turno(d)

    doc = visor.de("document")[0]
    assert doc["path"] == str(destino)
    assert doc["text"] == "# La compra\n- pan\n"


def test_lo_que_se_enseña_es_el_fichero_no_lo_que_se_le_metio(tmp_path):
    """En modo «añadir», el argumento son las líneas nuevas; el fichero, todo."""
    destino = tmp_path / "lista.md"
    destino.write_text("- pan\n", encoding="utf-8")

    def añadir(args):
        with open(destino, "a", encoding="utf-8") as f:
            f.write(args.get("contenido", ""))
        return f"Hecho: he ampliado {destino}."

    tools = [Tool("escribir_fichero", "escribe", {}, añadir)]
    execute = Acciones(tools, LOG)
    d = build(provider=ProviderConAcciones(llamadas=(
        ("escribir_fichero", {"ruta": str(destino), "contenido": "- leche\n",
                              "modo": "añadir"}),)), tools=tools, execute=execute)
    visor = con_visor(d)

    turno(d)

    assert visor.de("document")[0]["text"] == "- pan\n- leche\n"


def test_un_fichero_que_falla_no_se_enseña(tmp_path):
    tools = [Tool("escribir_fichero", "escribe",  {},
                  lambda a: "NO he escrito nada: es una carpeta.")]
    execute = Acciones(tools, LOG)
    d = build(provider=ProviderConAcciones(llamadas=(
        ("escribir_fichero", {"ruta": str(tmp_path / "no.md")}),)),
        tools=tools, execute=execute)
    visor = con_visor(d)

    turno(d)

    assert visor.de("document") == []


def test_un_comando_que_deja_un_fichero_no_se_enseña(tmp_path):
    """`echo … > x` también escribe, pero adivinar cuál es inventarse el documento."""
    tools = [Tool("ejecutar_comando", "ejecuta", {}, lambda a: "Hecho: listo.")]
    execute = Acciones(tools, LOG)
    d = build(provider=ProviderConAcciones(llamadas=(
        ("ejecutar_comando", {"comando": f"echo hola > {tmp_path}/x.md"}),)),
        tools=tools, execute=execute)
    visor = con_visor(d)

    turno(d)

    assert visor.de("document") == []


def test_el_write_de_claude_code_tambien_enseña_el_fichero(tmp_path):
    destino = tmp_path / "nota.md"
    destino.write_text("# Nota\n", encoding="utf-8")

    class ProviderQueEscribe(FakeProvider):
        accepts_tools = False

        def __init__(self):
            super().__init__("Ahí lo tienes.")
            self.on_tool = None
            self.on_file = None

        def stream_reply(self, system_prompt, messages):
            self.on_tool("Write", f"Write · {destino}", True)
            self.on_file(str(destino))       # al volver, con el fichero ya escrito
            return iter(["Ahí lo tienes."])

    d = build(provider=ProviderQueEscribe())
    visor = con_visor(d)

    turno(d)

    assert visor.de("document")[0]["text"] == "# Nota\n"


def test_al_leer_un_fichero_tambien_se_enseña(tmp_path):
    """«enséñame el resumen» no se queda en contártelo: se ve el documento."""
    origen = tmp_path / "resumen.md"
    origen.write_text("# Resumen\n\nTodo bien.\n", encoding="utf-8")

    tools = [Tool("leer_fichero", "lee", {},
                  lambda a: f"Contenido de {origen}: # Resumen")]
    execute = Acciones(tools, LOG)
    d = build(provider=ProviderConAcciones(
        llamadas=(("leer_fichero", {"ruta": str(origen)}),)),
        tools=tools, execute=execute)
    visor = con_visor(d)

    turno(d)

    doc = visor.de("document")[0]
    assert doc["path"] == str(origen)
    assert doc["text"] == "# Resumen\n\nTodo bien.\n"


def test_un_fichero_que_no_se_puede_leer_no_deja_hueco(tmp_path):
    tools = [Tool("leer_fichero", "lee", {}, lambda a: "NO he leído nada: no existe.")]
    execute = Acciones(tools, LOG)
    d = build(provider=ProviderConAcciones(
        llamadas=(("leer_fichero", {"ruta": str(tmp_path / "fantasma.md")}),)),
        tools=tools, execute=execute)
    visor = con_visor(d)

    turno(d)

    assert visor.de("document") == []
