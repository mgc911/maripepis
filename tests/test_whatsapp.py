"""La herramienta que le manda un wasap a alguien. Y que nunca lo hace a la primera.

Lo que más se prueba aquí no es que funcione, sino que **no funcione de más**:
que no se invente un teléfono, que no elija por su cuenta entre dos Martas y que
no dé por enviado lo que no ha salido. Es la única acción de Maripepis que le
llega a otra persona, y la única que no se puede deshacer.
"""

from __future__ import annotations

import json
import time

import pytest

from maripepis.tools import whatsapp as wa
from maripepis.tools.base import es_fallo
from maripepis.utils.turnos import nuevo_turno
from maripepis.veracidad import (
    desmiente_envio,
    dice_haber_enviado,
    espera_confirmacion,
)


@pytest.fixture(autouse=True)
def runtime_propio(tmp_path, monkeypatch):
    """Ningún test toca el pendiente de verdad.

    El mensaje que espera un «sí» vive en `$XDG_RUNTIME_DIR`, que en este equipo
    es el de la sesión del usuario: sin esto, un test que preparase un mensaje se
    lo dejaría puesto a la maripepis que esté corriendo, y el siguiente «mándalo»
    de verdad soltaría el del test.
    """
    runtime = tmp_path / "run"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    # Y sin marca heredada: la de un turno de Claude Code taparía la del proceso
    # y los dos pasos de un test parecerían el mismo turno.
    monkeypatch.delenv("MARIPEPIS_TURNO", raising=False)


# --- El teléfono -----------------------------------------------------------

@pytest.mark.parametrize(("dictado", "esperado"), [
    ("600112233", "34600112233"),          # el de aquí de toda la vida: se le pone el 34
    ("600 11 22 33", "34600112233"),       # como lo escribe cualquiera
    ("+34 600 112 233", "34600112233"),    # ya trae país
    ("0034600112233", "34600112233"),      # el otro modo de escribir el «+»
    ("+1 202 555 0143", "12025550143"),    # y no todo el mundo vive aquí
    ("34600112233", "34600112233"),        # más de nueve cifras: ya lo traía
])
def test_numeros_que_se_entienden(dictado, esperado):
    assert wa.numero(dictado) == esperado


@pytest.mark.parametrize("basura", ["", "12", "no me acuerdo", "6001122334455667788"])
def test_lo_que_no_es_un_telefono_no_pasa(basura):
    # Vacío corta la acción: abrirle el chat a un número inventado es peor que
    # decir que no se ha entendido.
    assert wa.numero(basura) == ""


def test_el_prefijo_es_configurable():
    assert wa.numero("912345678", prefijo="+351") == "351912345678"


# --- La agenda -------------------------------------------------------------

def _agenda(tmp_path, texto):
    fichero = tmp_path / "contactos.toml"
    fichero.write_text(texto, encoding="utf-8")
    return {"agenda": str(fichero)}


def test_agenda_con_seccion_y_sin_ella(tmp_path):
    # Las dos formas en que a uno se le ocurre escribir el fichero valen.
    con = _agenda(tmp_path, '[contactos]\nmarta = "+34600112233"\n')
    assert wa.contactos(con) == {"marta": "34600112233"}

    otro = tmp_path / "suelto"
    otro.mkdir()
    assert wa.contactos(_agenda(otro, 'marta = "600112233"\n')) == {"marta": "34600112233"}


def test_los_nombres_con_espacios_van_entre_comillas(tmp_path):
    cfg = _agenda(tmp_path, '"mi hermana" = "+34600112233"\n')
    assert wa.contactos(cfg) == {"mi hermana": "34600112233"}


def test_sin_fichero_la_agenda_esta_vacia_y_no_revienta(tmp_path):
    assert wa.contactos({"agenda": str(tmp_path / "no-existe.toml")}) == {}


def test_un_contacto_con_un_numero_ilegible_se_salta(tmp_path, caplog):
    cfg = _agenda(tmp_path, 'marta = "600112233"\npepe = "el de siempre"\n')
    assert wa.contactos(cfg) == {"marta": "34600112233"}
    assert "pepe" in caplog.text


def test_una_agenda_rota_no_tumba_el_turno(tmp_path, caplog):
    cfg = _agenda(tmp_path, "esto no es = = toml\n")
    assert wa.contactos(cfg) == {}
    assert "agenda" in caplog.text.lower()


def test_una_agenda_que_existe_pero_no_se_lee_no_es_una_agenda_que_falta(tmp_path):
    # «Créate una agenda» delante de una que existe deja a cualquiera mirando la
    # pantalla. Lo normal es que sea un nombre con espacios sin comillas.
    cfg = _agenda(tmp_path, "mi hermana = 600112233\n")
    resultado = wa.preparar_mensaje({"contacto": "hermana", "texto": "hola"}, cfg)
    assert "existe" in resultado and "comillas" in resultado


# --- Encontrar a quién ------------------------------------------------------

AGENDA = {"marta": "34600000001", "mi hermana": "34600000001",
          "juana": "34600000002", "marta garcia": "34600000003"}


def test_encuentra_por_el_nombre_hablado():
    assert wa.buscar("a Marta García", AGENDA) == [("marta garcia", "34600000003")]


def test_el_nombre_exacto_gana_al_parecido():
    # Con «marta» y «marta garcía» apuntadas, «Marta» no es una duda: es Marta.
    assert wa.buscar("Marta", AGENDA) == [("marta", "34600000001")]


def test_no_encaja_por_trozos_de_palabra():
    # «Ana» dentro de «Juana» mandaría el mensaje a quien no era.
    assert wa.buscar("Ana", AGENDA) == []


def test_dos_nombres_del_mismo_numero_son_la_misma_persona():
    assert wa.buscar("mi hermana", AGENDA) == [("mi hermana", "34600000001")]


def test_la_duda_de_verdad_se_devuelve_entera():
    dos = {"marta lopez": "34600000001", "marta ruiz": "34600000002"}
    assert len(wa.buscar("Marta", dos)) == 2


# --- El enlace --------------------------------------------------------------

def test_el_texto_va_codificado_entero():
    # ZapZap mete esta URL dentro de una cadena de JavaScript: una comilla sin
    # codificar no sería una comilla, sería código en tu sesión de WhatsApp.
    url = wa.enlace("34600112233", 'di "hola" & <b>adiós</b>')
    assert '"' not in url and "<" not in url and "&text=" in url
    assert url.startswith("whatsapp://send?phone=34600112233&text=")


# --- La herramienta ---------------------------------------------------------

@pytest.fixture
def zapzap(monkeypatch):
    """ZapZap instalado y abierto; devuelve lo que se le acaba lanzando."""
    lanzados: list[list[str]] = []
    monkeypatch.setattr(wa.shutil, "which", lambda c: f"/usr/bin/{c}")
    monkeypatch.setattr(wa, "zapzap_abierto", lambda: True)
    monkeypatch.setattr(wa, "lanzar", lambda args, cwd=None: lanzados.append(args))
    return lanzados


def test_deja_el_mensaje_escrito_y_dice_que_no_lo_envia(tmp_path, zapzap):
    cfg = _agenda(tmp_path, 'marta = "+34600112233"\n')

    resultado = wa.preparar_mensaje({"contacto": "Marta", "texto": "llego en diez minutos"}, cfg)

    assert not es_fallo(resultado)
    assert "NO está enviado" in resultado
    assert zapzap == [["/usr/bin/zapzap",
                       "whatsapp://send?phone=34600112233"
                       "&text=llego%20en%20diez%20minutos"]]


def test_al_modelo_se_le_prohibe_decir_que_lo_ha_enviado(tmp_path, zapzap):
    cfg = _agenda(tmp_path, 'marta = "+34600112233"\n')
    resultado = wa.preparar_mensaje({"contacto": "Marta", "texto": "hola"}, cfg)
    para_el_modelo = resultado.split("[Para el modelo]")[1]
    assert "NO digas que lo has enviado" in para_el_modelo


def test_un_numero_dictado_vale_sin_agenda(tmp_path, zapzap):
    resultado = wa.preparar_mensaje(
        {"contacto": "+34600112233", "texto": "hola"}, {"agenda": str(tmp_path / "nada.toml")})
    assert not es_fallo(resultado)
    assert "phone=34600112233" in zapzap[0][1]


def test_sin_agenda_explica_como_se_crea(tmp_path, zapzap):
    cfg = {"agenda": str(tmp_path / "contactos.toml")}
    resultado = wa.preparar_mensaje({"contacto": "Marta", "texto": "hola"}, cfg)
    assert es_fallo(resultado)
    assert str(tmp_path / "contactos.toml") in resultado
    assert zapzap == []


def test_a_quien_no_esta_en_la_agenda_no_se_le_escribe(tmp_path, zapzap):
    cfg = _agenda(tmp_path, 'marta = "+34600112233"\n')
    resultado = wa.preparar_mensaje({"contacto": "Pepe", "texto": "hola"}, cfg)
    assert es_fallo(resultado)
    assert "marta" in resultado          # y se le dice a quién sí puede escribir
    assert zapzap == []


def test_ante_la_duda_pregunta_en_vez_de_elegir(tmp_path, zapzap):
    cfg = _agenda(tmp_path, '"marta lopez" = "+34600000001"\n"marta ruiz" = "+34600000002"\n')
    resultado = wa.preparar_mensaje({"contacto": "Marta", "texto": "hola"}, cfg)
    assert es_fallo(resultado)
    assert "marta lopez" in resultado and "marta ruiz" in resultado
    assert zapzap == []


def test_sin_contacto_o_sin_texto_pregunta(tmp_path, zapzap):
    cfg = _agenda(tmp_path, 'marta = "+34600112233"\n')
    assert "?" in wa.preparar_mensaje({"texto": "hola"}, cfg)
    assert "?" in wa.preparar_mensaje({"contacto": "Marta"}, cfg)
    assert zapzap == []


def test_una_parrafada_no_pasa(tmp_path, zapzap):
    cfg = _agenda(tmp_path, 'marta = "+34600112233"\n')
    resultado = wa.preparar_mensaje({"contacto": "Marta", "texto": "a" * 2000}, cfg)
    assert es_fallo(resultado)
    assert zapzap == []


def test_con_zapzap_cerrado_no_da_el_mensaje_por_escrito(tmp_path, monkeypatch):
    # Su SingleApplication solo mira los argumentos si YA hay otra instancia:
    # arrancando de cero, el enlace se pierde sin decir nada.
    lanzados: list[list[str]] = []
    monkeypatch.setattr(wa.shutil, "which", lambda c: f"/usr/bin/{c}")
    monkeypatch.setattr(wa, "zapzap_abierto", lambda: False)
    monkeypatch.setattr(wa, "lanzar", lambda args, cwd=None: lanzados.append(args))
    cfg = _agenda(tmp_path, 'marta = "+34600112233"\n')

    resultado = wa.preparar_mensaje({"contacto": "Marta", "texto": "hola"}, cfg)

    assert es_fallo(resultado)
    assert lanzados == [["/usr/bin/zapzap"]]      # se abre, pero sin el enlace


def test_sin_cliente_instalado_lo_dice(tmp_path, monkeypatch):
    monkeypatch.setattr(wa.shutil, "which", lambda c: None)
    cfg = _agenda(tmp_path, 'marta = "+34600112233"\n')
    resultado = wa.preparar_mensaje({"contacto": "Marta", "texto": "hola"}, cfg)
    assert es_fallo(resultado)
    assert "zapzap" in resultado


def test_la_descripcion_lleva_los_nombres_y_ningun_telefono(tmp_path):
    cfg = _agenda(tmp_path, 'marta = "+34600112233"\nhermana = "+34600112233"\n')
    texto = wa.descripcion(cfg)
    assert "marta" in texto and "hermana" in texto
    assert "600112233" not in texto        # esto viaja al LLM en cada petición


# --- Y que no diga que lo ha enviado ---------------------------------------

class AccionesConWhatsApp:
    """Un turno con WhatsApp: qué herramientas hay puestas y cuáles han ido bien.

    Las dos preguntas hacen falta, y son distintas. Cuáles se llamaron dice si el
    mensaje existe; cuáles existen dice en qué modo está el equipo, que es lo que
    decide si «preparado» significa escrito en el chat o esperando un «sí». Por
    defecto, las dos: el modo envío, que es el que tiene los dos pasos.
    """

    def __init__(self, ok=("preparar_mensaje_whatsapp",), hay=None):
        self.ultimo_fallo = None
        self.llamadas = 1
        self._ok = set(ok)
        self.nombres = set(hay if hay is not None else
                           ("preparar_mensaje_whatsapp", "enviar_mensaje_whatsapp"))

    def herramientas_ok(self):
        return self._ok


@pytest.mark.parametrize("mentira", [
    "Ya se lo he enviado a Marta.",
    "Mensaje enviado.",
    "Listo, se lo he mandado.",
    "El mensaje ya está enviado y le ha llegado.",
])
def test_pilla_al_modelo_dando_el_mensaje_por_enviado(mentira):
    assert dice_haber_enviado(mentira)


@pytest.mark.parametrize("verdad", [
    "Se lo he dejado escrito en el chat, pero no lo he enviado.",
    "Todavía no se lo he mandado: dale a Enter tú.",
    "NO está enviado.",
    "Le he escrito a Marta; dale a enviar cuando quieras.",
])
def test_no_desmiente_al_modelo_cuando_acierta(verdad):
    # «no lo he enviado» es justo lo que se le ha pedido que diga.
    assert not dice_haber_enviado(verdad)


def test_si_ni_siquiera_lo_escribio_el_desmentido_es_otro():
    aviso = desmiente_envio("Ya se lo he mandado.", AccionesConWhatsApp(ok=()))
    assert aviso == "no he enviado ningún mensaje"


def test_un_turno_de_whatsapp_correcto_no_se_desmiente_solo():
    # «le he escrito» es de las palabras que exigen escribir_fichero: sin la
    # excepción, un turno perfecto acababa con un «no he llegado a escribir el
    # fichero» que no venía a cuento.
    from maripepis.veracidad import lo_que_no_ha_hecho

    assert lo_que_no_ha_hecho("Le he escrito a Marta lo que me has dicho.",
                              AccionesConWhatsApp()) == ""


def test_un_numero_dictado_a_medias_no_abre_ningun_chat(tmp_path, zapzap):
    # «escríbele al 600 11 22» — abrir ese chat es abrírselo a un desconocido.
    resultado = wa.preparar_mensaje(
        {"contacto": "600 11 22", "texto": "hola"}, {"agenda": str(tmp_path / "nada.toml")})
    assert es_fallo(resultado)
    assert "no me cuadra" in resultado
    assert zapzap == []


def test_una_agenda_vacia_no_se_confunde_con_una_rota(tmp_path, zapzap):
    cfg = _agenda(tmp_path, "# aquí van los contactos\n")
    resultado = wa.preparar_mensaje({"contacto": "Marta", "texto": "hola"}, cfg)
    assert "no tiene ningún contacto" in resultado
    assert "comillas" not in resultado


# --- Por la shell: el camino de los proveedores con herramientas propias -----

def _config(tmp_path, agenda):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[tools.whatsapp]\nagenda = "{agenda}"\n', encoding="utf-8")
    return str(cfg)


def test_la_orden_escribe_el_mensaje_y_sale_con_cero(tmp_path, zapzap, capsys):
    agenda = _agenda(tmp_path, 'edu = "+34600112233"\n')["agenda"]

    codigo = wa.main(["Edu", "llego tarde", "--config", _config(tmp_path, agenda)])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert "NO está enviado" in salida
    assert zapzap[0][1].startswith("whatsapp://send?phone=34600112233")


def test_la_orden_sale_con_uno_si_no_ha_escrito_nada(tmp_path, zapzap, capsys):
    agenda = _agenda(tmp_path, 'edu = "+34600112233"\n')["agenda"]

    codigo = wa.main(["Pepe", "hola", "--config", _config(tmp_path, agenda)])

    assert codigo == 1                       # un Bash que solo mire el código se entera
    assert "no está en la agenda" in capsys.readouterr().out
    assert zapzap == []


def test_a_claude_code_se_le_da_la_orden_hecha_no_la_receta():
    # Contarle que ZapZap entiende enlaces whatsapp:// acaba en un enlace montado
    # a mano con un teléfono inventado, sin agenda y sin «NO está enviado».
    from maripepis.cli import instrucciones_de_whatsapp_por_shell

    texto = instrucciones_de_whatsapp_por_shell("/venv/bin/maripepis-whatsapp")

    assert "/venv/bin/maripepis-whatsapp 'CONTACTO' 'TEXTO'" in texto
    assert "NO montes tú un enlace whatsapp://" in texto
    assert "Nunca digas que lo has enviado" in texto


@pytest.mark.parametrize(("suyas", "puede"), [
    ("Bash,WebSearch,Write,Read", True),
    ("default", True),
    ("WebSearch", False),
    ("", False),
])
def test_sin_shell_no_hay_whatsapp_que_contarle(suyas, puede):
    from maripepis.cli import puede_ejecutar_ordenes

    assert puede_ejecutar_ordenes({"llm": {"claude_code": {"tools": suyas}}}) is puede


# --- El modo envío: cuando ya no hay Enter que valga -------------------------
#
# Aquí se le da la vuelta a todo lo de arriba. Lo que se prueba ya no es que NO
# envíe, sino las tres cosas que sustituyen al Enter: que las barandillas de a
# quién le llega siguen puestas —y que cuando saltan, el demonio ni se entera—,
# que entre el «prepáralo» y el «mándalo» tiene que hablar el usuario, y que
# cuando dice que lo ha enviado sea verdad.

@pytest.fixture
def sesion(monkeypatch):
    """El demonio contesta que sí; devuelve lo que se le acaba pidiendo."""
    pedidos: list[tuple] = []

    def _enviar(destino, texto, path=None):
        pedidos.append((destino, texto, path))
        return {"ok": True, "id": "MSG-1", "destino": f"{destino}@s.whatsapp.net"}

    monkeypatch.setattr(wa, "_enviar_por_la_sesion", _enviar)
    return pedidos


ENVIO = {"modo": "envio"}


def _preparado(tmp_path, agenda='edu = "+34600112233"\n', **args):
    """Deja un mensaje preparado y devuelve (cfg, salida). Sin cambiar de turno."""
    cfg = _agenda(tmp_path, agenda) | ENVIO
    args = {"contacto": "Edu", "texto": "llego en diez"} | args
    return cfg, wa.preparar_envio(args, cfg)


@pytest.mark.parametrize(("configurado", "esperado"), [
    ({}, "borrador"),                          # el modo seguro es el de por defecto
    ({"modo": "envio"}, "envio"),
    ({"modo": "ENVÍO"}, "borrador"),           # con tilde no es la palabra: no cuela
    ({"modo": ""}, "borrador"),
    ({"modo": "enviar"}, "borrador"),          # un dedazo no puede acabar mandando nada
])
def test_ante_la_duda_no_se_envia(configurado, esperado):
    assert wa.modo_de(configurado) == esperado


def test_en_borrador_hay_una_herramienta_y_en_envio_tres(tmp_path):
    """Y con distinto nombre, que es de lo que se fía `veracidad`."""
    agenda = _agenda(tmp_path, 'edu = "+34600112233"\n')

    borrador = wa.build_whatsapp_tools(agenda)
    envio = wa.build_whatsapp_tools(agenda | ENVIO)

    assert [t.name for t in borrador] == ["preparar_mensaje_whatsapp"]
    assert [t.name for t in envio] == ["preparar_mensaje_whatsapp",
                                       "enviar_mensaje_whatsapp",
                                       "borrar_mensaje_whatsapp"]
    assert "NO lo envía" in borrador[0].description
    assert "NO envía nada" in envio[0].description   # preparar, en envío, tampoco
    assert "ENVÍA el mensaje" in envio[1].description
    assert "retira el ÚLTIMO mensaje" in envio[2].description
    assert "edu" in envio[0].description             # la agenda sigue estando


@pytest.mark.parametrize("cual", [1, 2])             # confirmar y borrar
def test_ni_confirmar_ni_borrar_aceptan_argumentos(tmp_path, cual):
    """Media herramienta es esto: sin argumentos no hay envío que inventarse, ni
    mensajes ajenos que ponerse a retirar."""
    herramienta = wa.build_whatsapp_tools(
        _agenda(tmp_path, 'edu = "+34600112233"\n') | ENVIO)[cual]

    assert herramienta.parameters["properties"] == {}
    assert herramienta.parameters["required"] == []
    assert herramienta.parameters["additionalProperties"] is False


def test_preparar_no_habla_con_el_demonio_y_lo_dice(tmp_path, sesion):
    cfg, salida = _preparado(tmp_path)

    assert sesion == []                        # todavía no ha salido nada
    assert not es_fallo(salida)
    assert "SIN ENVIAR" in salida
    # El nombre, tal y como está apuntado en la agenda: es lo que se lee en voz alta.
    assert "edu" in salida and "llego en diez" in salida
    para_el_modelo = salida.split("[Para el modelo]")[1]
    assert "NO digas que está enviado" in para_el_modelo
    assert "no llames ahora a enviar_mensaje_whatsapp" in para_el_modelo


def test_con_el_si_del_usuario_sale(tmp_path, sesion):
    cfg, _ = _preparado(tmp_path)

    nuevo_turno()                              # el usuario ha dicho que sí
    salida = wa.enviar_mensaje({}, cfg)

    assert sesion == [("34600112233", "llego en diez", None)]
    assert not es_fallo(salida)
    assert "ENVIADO" in salida
    assert "no digas que lo ha leído" in salida.lower()


def test_en_el_mismo_turno_no_sale_por_mucho_que_insista(tmp_path, sesion):
    """La prueba de toda la fase: el modelo no puede contestarse a sí mismo.

    Un 7B lee «léeselo y espera» y encadena las dos llamadas en la misma vuelta.
    Si eso colara, la confirmación sería un adorno y esto sería el envío directo
    con un paso de más.
    """
    cfg, _ = _preparado(tmp_path)

    salida = wa.enviar_mensaje({}, cfg)

    assert es_fallo(salida)
    assert "este mismo turno" in salida
    assert sesion == []

    # Y el mensaje sigue preparado: se ha negado a mandarlo, no lo ha tirado.
    nuevo_turno()
    assert not es_fallo(wa.enviar_mensaje({}, cfg))
    assert sesion == [("34600112233", "llego en diez", None)]


def test_sin_nada_preparado_no_hay_nada_que_confirmar(tmp_path, sesion):
    cfg = _agenda(tmp_path, 'edu = "+34600112233"\n') | ENVIO

    salida = wa.enviar_mensaje({}, cfg)

    assert es_fallo(salida)
    assert "no tengo ningún mensaje preparado" in salida
    assert "preparar_mensaje_whatsapp" in salida     # y se le dice por dónde empezar
    assert sesion == []


def _pasa_un_rato(monkeypatch, segundos=None):
    """Adelanta el reloj. El de verdad, hacia delante: `time.monotonic()` daría
    un valor mucho más pequeño que la fecha guardada y lo que se estaría probando
    sería un reloj que salta hacia atrás, que es otro caso y ya tiene su test."""
    ahora = time.time()
    monkeypatch.setattr(time, "time", lambda: ahora + (segundos or wa.CADUCA + 1))


def test_un_pendiente_de_hace_un_rato_ya_no_vale(tmp_path, sesion, monkeypatch):
    """Pasado el minuto no se manda: no se sabe si es el que le leyeron."""
    cfg, _ = _preparado(tmp_path)
    nuevo_turno()
    _pasa_un_rato(monkeypatch)

    salida = wa.enviar_mensaje({}, cfg)

    assert es_fallo(salida)
    assert "ha caducado" in salida
    assert sesion == []
    assert not wa.fichero_pendiente().exists()      # y se tira, no se queda ahí


def test_un_pendiente_con_la_fecha_en_el_futuro_tampoco(tmp_path, sesion):
    """El reloj ha saltado entre los dos pasos: de eso no se deduce nada."""
    cfg, _ = _preparado(tmp_path)
    nuevo_turno()
    datos = json.loads(wa.fichero_pendiente().read_text(encoding="utf-8"))
    wa.fichero_pendiente().write_text(
        json.dumps(datos | {"creado": time.time() + 3600}), encoding="utf-8")

    assert es_fallo(wa.enviar_mensaje({}, cfg))
    assert sesion == []


def test_confirmar_otra_cosa_distinta_no_manda_nada(tmp_path, sesion):
    """«Sí, pero dile mejor que a las ocho» no es un sí a lo que había."""
    cfg, _ = _preparado(tmp_path)
    nuevo_turno()

    salida = wa.enviar_mensaje({"contacto": "Edu", "texto": "llego a las ocho"}, cfg)

    assert es_fallo(salida)
    assert "llego en diez" in salida            # se le recuerda lo que sí había
    assert "preparar_mensaje_whatsapp" in salida
    assert sesion == []


def test_confirmar_a_otra_persona_tampoco(tmp_path, sesion):
    cfg, _ = _preparado(tmp_path, 'edu = "+34600112233"\nmarta = "+34600445566"\n')
    nuevo_turno()

    salida = wa.enviar_mensaje({"contacto": "Marta"}, cfg)

    assert es_fallo(salida)
    assert "edu" in salida
    assert sesion == []


def test_que_repita_lo_que_ya_habia_no_estorba(tmp_path, sesion):
    """Un modelo pequeño se inventa argumentos aunque no los lleve. Si son los
    mismos, es ruido: lo que se manda es lo guardado."""
    cfg, _ = _preparado(tmp_path)
    nuevo_turno()

    salida = wa.enviar_mensaje({"contacto": "edu", "texto": "  llego  en diez "}, cfg)

    assert not es_fallo(salida)
    assert sesion == [("34600112233", "llego en diez", None)]


def test_un_telefono_dictado_se_confirma_como_se_diga(tmp_path, sesion):
    """«Al 600 11 22 33» y «al 600112233» no son dos personas distintas."""
    cfg, _ = _preparado(tmp_path, contacto="600 11 22 33")
    nuevo_turno()

    salida = wa.enviar_mensaje({"contacto": "600112233"}, cfg)

    assert not es_fallo(salida)
    assert sesion == [("34600112233", "llego en diez", None)]


def test_un_si_no_manda_el_mensaje_dos_veces(tmp_path, sesion):
    """De los dos fallos posibles, el que no tiene arreglo es el duplicado."""
    cfg, _ = _preparado(tmp_path)
    nuevo_turno()
    wa.enviar_mensaje({}, cfg)

    nuevo_turno()
    otra = wa.enviar_mensaje({}, cfg)

    assert es_fallo(otra)
    assert len(sesion) == 1


def test_el_pendiente_no_lo_lee_cualquiera(tmp_path):
    """Es lo que acaba de dictar el usuario: sale en 0600 desde el open."""
    _preparado(tmp_path)
    assert wa.fichero_pendiente().stat().st_mode & 0o777 == 0o600


def test_un_pendiente_ilegible_es_como_no_tener_ninguno(tmp_path, sesion):
    cfg, _ = _preparado(tmp_path)
    wa.fichero_pendiente().write_text("{esto no es json", encoding="utf-8")
    nuevo_turno()

    assert "no tengo ningún mensaje preparado" in wa.enviar_mensaje({}, cfg)
    assert sesion == []


def test_el_texto_llega_al_demonio_tal_cual(tmp_path, sesion):
    """Lo va a leer otra persona: ni se adorna ni se firma por el camino."""
    cfg, _ = _preparado(tmp_path, texto="  te   llamo luego  ")
    nuevo_turno()
    wa.enviar_mensaje({}, cfg)
    assert sesion[0][1] == "te llamo luego"    # solo se juntan los espacios


def test_se_manda_a_quien_se_leyo_aunque_cambie_la_agenda(tmp_path, sesion):
    """Entre los dos pasos puede pasar de todo; el destinatario ya está decidido."""
    cfg, _ = _preparado(tmp_path)
    _agenda(tmp_path, 'edu = "+34600999888"\n')      # otro Edu, mismo nombre
    nuevo_turno()

    wa.enviar_mensaje({}, cfg)

    assert sesion[0][0] == "34600112233"       # el que le leyeron


@pytest.mark.parametrize(("args", "trozo"), [
    ({"contacto": "Pepe", "texto": "hola"}, "no está en la agenda"),
    ({"contacto": "Marta", "texto": "hola"}, "varios que encajan"),
    ({"contacto": "Edu", "texto": "x" * 1001}, "no le caben"),
    # Seis cifras ya parecen un teléfono dictado, pero no llegan a serlo.
    ({"contacto": "600112", "texto": "hola"}, "no me cuadra como número"),
])
def test_las_barandillas_siguen_puestas_y_no_se_prepara_nada(tmp_path, sesion, args, trozo):
    """Lo que importa de estas ramas: no queda ni un pendiente que confirmar."""
    # Dos Martas y ninguna exacta: eso es una duda de verdad, y ante una duda no
    # se elige, se pregunta. Que aquí no salga nada es lo que más importa de todo
    # el fichero: mandarle el mensaje a la Marta equivocada no se arregla luego.
    cfg = _agenda(tmp_path, 'edu = "+34600112233"\n'
                            '"marta garcía" = "+34600445566"\n'
                            '"marta lópez" = "+34600778899"\n') | ENVIO

    salida = wa.preparar_envio(args, cfg)

    assert es_fallo(salida)
    assert trozo in salida
    assert salida.startswith("NO he preparado nada")  # ni escrito ni enviado: preparado
    assert not wa.fichero_pendiente().exists()
    assert sesion == []


def test_sin_demonio_no_se_da_nada_por_enviado(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "_enviar_por_la_sesion", lambda *a, **k: None)
    cfg, _ = _preparado(tmp_path)
    nuevo_turno()

    salida = wa.enviar_mensaje({}, cfg)

    assert es_fallo(salida)
    assert "systemctl --user start maripepis-whatsapp" in salida
    assert "No des el mensaje por enviado" in salida


def test_si_el_demonio_dice_que_no_se_cuenta_el_motivo(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "_enviar_por_la_sesion",
                        lambda *a, **k: {"ok": False, "error": "llevo 6 mensajes en un minuto"})
    cfg, _ = _preparado(tmp_path)
    nuevo_turno()

    salida = wa.enviar_mensaje({}, cfg)

    assert es_fallo(salida)
    assert "llevo 6 mensajes en un minuto" in salida


# --- Los dos pasos por la shell ---------------------------------------------

def _config_envio(tmp_path, agenda):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[tools.whatsapp]\nagenda = "{agenda}"\nmodo = "envio"\n',
                   encoding="utf-8")
    return str(cfg)


def test_la_orden_de_la_shell_va_en_dos_pasos(tmp_path, sesion, capsys):
    """Claude Code no recibe nuestras herramientas: por aquí tiene las mismas dos."""
    agenda = _agenda(tmp_path, 'edu = "+34600112233"\n')["agenda"]
    config = _config_envio(tmp_path, agenda)

    codigo = wa.main(["Edu", "voy para allá", "--config", config])

    assert codigo == 0
    assert "SIN ENVIAR" in capsys.readouterr().out
    assert sesion == []

    nuevo_turno()                              # otra vuelta, con el «sí» dicho
    codigo = wa.main(["--enviar", "--config", config])

    assert codigo == 0
    assert "ENVIADO" in capsys.readouterr().out
    assert sesion[0][:2] == ("34600112233", "voy para allá")


def test_por_la_shell_el_turno_es_el_que_viene_en_el_entorno(tmp_path, sesion,
                                                             monkeypatch, capsys):
    """Dos órdenes de la misma vuelta de Claude Code son el mismo turno.

    Y este es el freno de esa vía entera: cada paso es un proceso nuevo, así que
    sin la marca heredada dos órdenes seguidas parecerían siempre dos turnos
    distintos y el «sí» se lo podría dar el modelo a sí mismo.
    """
    agenda = _agenda(tmp_path, 'edu = "+34600112233"\n')["agenda"]
    config = _config_envio(tmp_path, agenda)

    monkeypatch.setenv("MARIPEPIS_TURNO", "el-turno-de-claude")
    wa.main(["Edu", "voy para allá", "--config", config])
    assert wa.main(["--enviar", "--config", config]) == 1
    assert "este mismo turno" in capsys.readouterr().out
    assert sesion == []

    monkeypatch.setenv("MARIPEPIS_TURNO", "el-siguiente")   # el usuario ha hablado
    assert wa.main(["--enviar", "--config", config]) == 0
    assert len(sesion) == 1


def test_a_mano_desde_la_terminal_cada_orden_es_un_turno(tmp_path, sesion):
    """Sin marca heredada, cada proceso trae la suya, y así tiene que ser.

    Quien escribe las dos órdenes en una terminal ES la confirmación: ha visto lo
    que preparó y ha escrito `--enviar` después. Si aquí se exigiera lo mismo que
    a un modelo, probar esto a mano sería imposible.
    """
    agenda = _agenda(tmp_path, 'edu = "+34600112233"\n')["agenda"]
    config = _config_envio(tmp_path, agenda)

    wa.main(["Edu", "voy para allá", "--config", config])
    nuevo_turno()                              # otro proceso: otra marca de arranque

    assert wa.main(["--enviar", "--config", config]) == 0
    assert len(sesion) == 1


@pytest.mark.parametrize("orden", [
    ["--enviar", "Edu", "hola"],               # --enviar va solo: no manda lo que le digas
    ["Edu"],                                   # sin texto no hay mensaje
])
def test_la_orden_no_se_deja_usar_a_medias(tmp_path, orden):
    config = _config_envio(tmp_path, _agenda(tmp_path, 'edu = "+34600112233"\n')["agenda"])
    with pytest.raises(SystemExit) as salida:
        wa.main([*orden, "--config", config])
    assert salida.value.code == 2


def test_en_borrador_no_hay_nada_que_confirmar(tmp_path):
    """`--enviar` en modo borrador: el Enter lo sigue dando el usuario."""
    agenda = _agenda(tmp_path, 'edu = "+34600112233"\n')["agenda"]
    config = tmp_path / "config.toml"
    config.write_text(f'[tools.whatsapp]\nagenda = "{agenda}"\n', encoding="utf-8")

    with pytest.raises(SystemExit) as salida:
        wa.main(["--enviar", "--config", str(config)])
    assert salida.value.code == 2


# --- Y lo que se dice de todo esto en voz alta ------------------------------

def test_en_modo_envio_no_se_desmiente_la_verdad():
    """El desmentido de siempre, del revés: ahora sí lo ha enviado."""
    enviado = AccionesConWhatsApp(ok=("enviar_mensaje_whatsapp",))
    assert desmiente_envio("Ya se lo he enviado a Edu.", enviado) == ""

    # Sin llamar a ninguna de las dos, el mensaje no existe.
    ninguna = AccionesConWhatsApp(ok=())
    assert desmiente_envio("Ya se lo he enviado.", ninguna) == "no he enviado ningún mensaje"


def test_el_desmentido_dice_donde_esta_el_mensaje_en_cada_modo():
    """No es lo mismo «dale a Enter» que «dime que sí»: lo que tiene que hacer
    el usuario a continuación es distinto, y no ve la pantalla."""
    borrador = AccionesConWhatsApp(ok=("preparar_mensaje_whatsapp",),
                                   hay=("preparar_mensaje_whatsapp",))
    aviso = desmiente_envio("Ya se lo he mandado.", borrador)
    assert "escrito en el chat" in aviso

    envio = AccionesConWhatsApp(ok=("preparar_mensaje_whatsapp",))
    aviso = desmiente_envio("Ya se lo he mandado.", envio)
    assert "todavía" in aviso and "dime que sí" in aviso


def test_un_turno_que_espera_un_si_no_se_toma_por_uno_a_medias():
    """«Le mando esto a Edu, ¿te parece?» tiene la forma del turno dejado a
    medias, y no lo está: el empujón lo llevaría a confirmarlo él solo."""
    preparado = AccionesConWhatsApp(ok=("preparar_mensaje_whatsapp",))
    assert espera_confirmacion(preparado)

    # En borrador no hay nada que esperar: el mensaje ya está en el chat.
    borrador = AccionesConWhatsApp(ok=("preparar_mensaje_whatsapp",),
                                   hay=("preparar_mensaje_whatsapp",))
    assert not espera_confirmacion(borrador)


def test_al_modelo_no_se_le_promete_lo_que_no_pasa():
    """Ni en las herramientas propias ni por la shell: dos pasos y un «sí»."""
    from maripepis.cli import (
        instrucciones_de_herramientas,
        instrucciones_de_whatsapp_por_shell,
    )

    propias = instrucciones_de_herramientas(
        {"preparar_mensaje_whatsapp", "enviar_mensaje_whatsapp"})
    assert "DOS pasos" in propias
    assert "Nunca las llames las dos seguidas" in propias
    assert "no lleva argumentos" in propias

    shell = instrucciones_de_whatsapp_por_shell("/venv/bin/maripepis-whatsapp", "envio")
    assert "/venv/bin/maripepis-whatsapp --enviar" in shell
    assert "NO envía nada todavía" in shell

    # Y en borrador se le sigue diciendo lo de siempre.
    borrador = instrucciones_de_herramientas({"preparar_mensaje_whatsapp"})
    assert "deja el mensaje ESCRITO" in borrador
    assert "DOS pasos" not in borrador


# --- Grupos: lo mismo, pero lo leen doce ------------------------------------
#
# Un grupo no tiene teléfono, tiene un identificador que solo se ve desde dentro
# de la sesión. De ahí que se apunten a mano y de ahí que el enlace de ZapZap no
# pueda con ellos. Lo que se prueba aquí es que esa diferencia se note donde
# tiene que notarse: al leerle al usuario a quién va el mensaje.

GRUPO = "120363021234567890@g.us"


def _con_grupos(tmp_path, texto=None):
    return _agenda(tmp_path, texto or (
        'edu = "+34600112233"\n'
        f'[grupos]\nfamilia = "{GRUPO}"\n'))


def test_los_grupos_se_apuntan_aparte(tmp_path):
    cfg = _con_grupos(tmp_path)
    assert wa.contactos(cfg) == {"edu": "34600112233"}
    assert wa.grupos(cfg) == {"familia": GRUPO}


def test_un_grupo_con_un_identificador_que_no_lo_es_se_salta(tmp_path, caplog):
    # Lo que pasa de verdad: copiar el nombre del grupo en vez de su identificador.
    cfg = _agenda(tmp_path, '[grupos]\nfamilia = "Familia Guevara"\n'
                            f'buena = "{GRUPO}"\n')
    assert wa.grupos(cfg) == {"buena": GRUPO}
    assert "familia" in caplog.text and "@g.us" in caplog.text


def test_sin_seccion_de_grupos_no_hay_grupos(tmp_path):
    assert wa.grupos(_agenda(tmp_path, 'edu = "+34600112233"\n')) == {}


def test_una_persona_y_un_grupo_que_se_llaman_igual_son_una_duda(tmp_path, sesion):
    """Lo que no puede pasar: que uno pise al otro y el mensaje lo lean doce."""
    cfg = _con_grupos(tmp_path, 'familia = "+34600112233"\n'
                                f'[grupos]\nfamilia = "{GRUPO}"\n') | ENVIO

    salida = wa.preparar_envio({"contacto": "familia", "texto": "hola"}, cfg)

    assert es_fallo(salida)
    assert "varios que encajan" in salida
    assert "el grupo familia" in salida        # y se dice cuál de los dos es grupo
    assert not wa.fichero_pendiente().exists()


def test_al_grupo_se_le_manda_por_su_identificador(tmp_path, sesion):
    cfg = _con_grupos(tmp_path) | ENVIO

    wa.preparar_envio({"contacto": "familia", "texto": "llego tarde"}, cfg)
    nuevo_turno()
    salida = wa.enviar_mensaje({}, cfg)

    assert sesion == [(GRUPO, "llego tarde", None)]
    assert "al grupo familia" in salida


def test_al_leerlo_se_dice_que_es_un_grupo(tmp_path, sesion):
    """Sin ver la pantalla, es la única forma de notar que lo leen doce."""
    cfg = _con_grupos(tmp_path) | ENVIO

    salida = wa.preparar_envio({"contacto": "la familia", "texto": "llego tarde"}, cfg)

    assert "va al grupo familia" in salida
    assert sesion == []


@pytest.mark.parametrize("dicho", ["familia", "la familia", "el grupo familia",
                                   "grupo de la familia"])
def test_como_se_nombre_el_grupo_al_hablar(tmp_path, sesion, dicho):
    cfg = _con_grupos(tmp_path) | ENVIO
    assert not es_fallo(wa.preparar_envio({"contacto": dicho, "texto": "hola"}, cfg))


def test_en_borrador_a_un_grupo_no_se_le_puede_escribir(tmp_path, zapzap):
    """Y se dice por qué, con la salida: el enlace lleva un teléfono y no hay."""
    cfg = _con_grupos(tmp_path)

    salida = wa.preparar_mensaje({"contacto": "familia", "texto": "hola"}, cfg)

    assert es_fallo(salida)
    assert "es un grupo" in salida
    assert 'modo = "envio"' in salida
    assert zapzap == []


def test_en_borrador_los_grupos_ni_se_le_nombran_al_modelo(tmp_path):
    """Nombrar un destino imposible es invitar a un turno que acaba en «no puedo»."""
    cfg = _con_grupos(tmp_path)

    assert "familia" not in wa.descripcion(cfg)
    assert "familia" in wa.descripcion(cfg | ENVIO)
    assert GRUPO not in wa.descripcion(cfg | ENVIO)   # el identificador, nunca


# --- Deshacer: el «no, espera» ---------------------------------------------

@pytest.fixture
def revocacion(monkeypatch):
    """El demonio acepta retirar; devuelve cuántas veces se le ha pedido."""
    pedidos: list = []

    def _revocar(path=None):
        pedidos.append(path)
        return {"ok": True, "id": "MSG-1"}

    monkeypatch.setattr(wa, "_revocar_por_la_sesion", _revocar)
    return pedidos


def test_borrar_lo_que_todavia_no_ha_salido_no_molesta_a_whatsapp(tmp_path, revocacion):
    """«No, déjalo» dicho a tiempo: el mensaje estaba preparado y se tira."""
    cfg, _ = _preparado(tmp_path)
    nuevo_turno()

    salida = wa.borrar_mensaje({}, cfg)

    assert not es_fallo(salida)
    assert "NO había salido" in salida
    assert revocacion == []                    # ni se le pregunta al demonio
    assert not wa.fichero_pendiente().exists()


def test_y_entonces_el_si_de_despues_ya_no_manda_nada(tmp_path, sesion, revocacion):
    """Lo que de verdad importa de descartarlo: que no se pueda confirmar luego."""
    cfg, _ = _preparado(tmp_path)
    nuevo_turno()
    wa.borrar_mensaje({}, cfg)

    nuevo_turno()
    assert es_fallo(wa.enviar_mensaje({}, cfg))
    assert sesion == []


def test_borrar_lo_ya_enviado_se_lo_pide_al_demonio(tmp_path, revocacion):
    cfg = _agenda(tmp_path, 'edu = "+34600112233"\n') | ENVIO

    salida = wa.borrar_mensaje({}, cfg)

    assert not es_fallo(salida)
    assert "BORRADO" in salida
    assert "se eliminó este mensaje" in salida
    assert len(revocacion) == 1
    # Y no se promete lo que no se sabe: puede haberlo leído antes.
    assert "puede haberlo leído" in salida


def test_un_pendiente_caducado_no_tapa_el_borrado_de_verdad(tmp_path, revocacion,
                                                            monkeypatch):
    """Si lo preparado ya no vale, «bórralo» se refiere a lo que sí salió."""
    cfg, _ = _preparado(tmp_path)
    nuevo_turno()
    _pasa_un_rato(monkeypatch)

    salida = wa.borrar_mensaje({}, cfg)

    assert not es_fallo(salida)
    assert len(revocacion) == 1


def test_si_whatsapp_ya_no_deja_retirar_se_dice(tmp_path, monkeypatch):
    """Es la respuesta legítima que más va a doler: el mensaje se queda puesto."""
    monkeypatch.setattr(wa, "_revocar_por_la_sesion",
                        lambda path=None: {"ok": False, "error": "ya ha pasado demasiado tiempo"})
    cfg = _agenda(tmp_path, 'edu = "+34600112233"\n') | ENVIO

    salida = wa.borrar_mensaje({}, cfg)

    assert es_fallo(salida)
    assert "ya ha pasado demasiado tiempo" in salida
    assert "desde el móvil" in salida          # qué le queda al usuario


def test_sin_demonio_no_se_da_nada_por_borrado(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "_revocar_por_la_sesion", lambda path=None: None)
    cfg = _agenda(tmp_path, 'edu = "+34600112233"\n') | ENVIO

    salida = wa.borrar_mensaje({}, cfg)

    assert es_fallo(salida)
    assert "systemctl --user start maripepis-whatsapp" in salida


def test_borrar_no_se_hace_esperar_a_otro_turno(tmp_path, revocacion):
    """Al revés que enviar: retirar es el lado seguro, y se puede en el mismo turno.

    Aquí el freno de los dos turnos sería justo lo contrario de lo que hace falta:
    el usuario acaba de decir «bórralo» y WhatsApp solo lo permite un rato.
    """
    cfg, _ = _preparado(tmp_path)             # mismo turno, sin nuevo_turno()

    assert not es_fallo(wa.borrar_mensaje({}, cfg))


def test_un_borrado_correcto_no_se_desmiente_solo():
    """«He borrado el mensaje» exige `ejecutar_comando`... salvo por esto."""
    from maripepis.veracidad import lo_que_no_ha_hecho

    borrado = AccionesConWhatsApp(ok=("borrar_mensaje_whatsapp",))
    assert lo_que_no_ha_hecho("Ya lo he borrado.", borrado) == ""

    # Y un turno que retira habla de un mensaje enviado sin estar mintiendo.
    assert desmiente_envio("He borrado el mensaje enviado a Edu.", borrado) == ""


def test_por_la_shell_tambien_se_puede_borrar(tmp_path, revocacion, capsys):
    agenda = _agenda(tmp_path, 'edu = "+34600112233"\n')["agenda"]
    config = _config_envio(tmp_path, agenda)

    assert wa.main(["--borrar", "--config", config]) == 0
    assert "BORRADO" in capsys.readouterr().out
    assert len(revocacion) == 1


@pytest.mark.parametrize("orden", [
    ["--borrar", "Edu", "hola"],               # --borrar va solo, como --enviar
    ["--enviar", "--borrar"],                  # o se manda o se borra
])
def test_borrar_por_la_shell_no_se_deja_usar_a_medias(tmp_path, orden):
    config = _config_envio(tmp_path, _agenda(tmp_path, 'edu = "+34600112233"\n')["agenda"])
    with pytest.raises(SystemExit) as salida:
        wa.main([*orden, "--config", config])
    assert salida.value.code == 2


def test_en_borrador_no_hay_nada_que_borrar(tmp_path):
    """El mensaje sigue en el cuadro de texto: quitarlo es cosa del teclado."""
    agenda = _agenda(tmp_path, 'edu = "+34600112233"\n')["agenda"]
    config = tmp_path / "config.toml"
    config.write_text(f'[tools.whatsapp]\nagenda = "{agenda}"\n', encoding="utf-8")

    with pytest.raises(SystemExit) as salida:
        wa.main(["--borrar", "--config", str(config)])
    assert salida.value.code == 2


def test_al_modelo_se_le_cuenta_lo_de_los_grupos_y_lo_de_borrar():
    from maripepis.cli import (
        instrucciones_de_herramientas,
        instrucciones_de_whatsapp_por_shell,
    )

    propias = instrucciones_de_herramientas(
        {"preparar_mensaje_whatsapp", "enviar_mensaje_whatsapp", "borrar_mensaje_whatsapp"})
    assert "GRUPOS" in propias
    assert "borrar_mensaje_whatsapp" in propias
    assert "sin preguntarle nada" in propias

    shell = instrucciones_de_whatsapp_por_shell("/venv/bin/maripepis-whatsapp", "envio")
    assert "--borrar" in shell
    assert "si es un grupo, que es un grupo" in shell
