"""Que las luces se apaguen cuando se dice «apaga», y que se diga cuando no.

Con el puente simulado: aquí se prueba la traducción de lo que se habla a lo que
entiende Hue, no que haya corriente en el salón. Lo que sí se prueba de verdad es
el contrato de `es_fallo`, porque de él depende que el asistente no cante un
«ya está» con las luces igual de encendidas que antes.
"""

import subprocess

import httpx
import pytest

from maripepis.hogar import Luz, SinPuente, SinVincular
from maripepis.hogar import hue
from maripepis.tools.base import es_fallo
from maripepis.tools.hogar import (
    build_home_tools,
    buscar,
    cambios_de_color,
    controlar_luces,
    estado_de_las_luces,
    normalizar,
)


class PuenteFalso:
    """Un puente que apunta lo que le mandan en vez de encender nada."""

    def __init__(self, grupos=(), bombillas=(), escenas=(), rompe=False):
        self._grupos = list(grupos)
        self._bombillas = list(bombillas)
        self._escenas = list(escenas)
        self._rompe = rompe
        self.aplicado = []      # [(Luz, cambios)]
        self.escena_activada = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def grupos(self):
        return list(self._grupos)

    def bombillas(self):
        return list(self._bombillas)

    def escenas(self):
        return list(self._escenas)

    def aplicar(self, luz, cambios):
        if self._rompe:
            raise httpx.HTTPError("device unreachable")
        self.aplicado.append((luz, cambios))

    def activar_escena(self, id_escena):
        if self._rompe:
            raise httpx.HTTPError("scene not found")
        self.escena_activada = id_escena


@pytest.fixture
def puente(monkeypatch):
    """Pone un puente falso y lo devuelve para mirar lo que se le ha mandado."""
    def _montar(**kwargs):
        falso = PuenteFalso(**kwargs)
        monkeypatch.setattr("maripepis.tools.hogar.conectar", lambda *a, **k: falso)
        return falso

    return _montar


SALON = Luz(id="g1", nombre="Salón", encendida=True, brillo=80, grupo=True)
COCINA = Luz(id="g2", nombre="Cocina", encendida=False, brillo=0, grupo=True)
LAMPARA = Luz(id="l1", nombre="Lamparita", encendida=False, brillo=0)


# --- Entender lo que se dice ----------------------------------------------


@pytest.mark.parametrize("dicho, esperado", [
    ("el salón", "salon"),
    ("Salon", "salon"),
    ("las luces del salón", "salon"),
    ("la luz de la cocina", "cocina"),
    ("todas las luces", "luces"),
    ("  EL   Salón  ", "salon"),
])
def test_normalizar_quita_relleno_y_tildes(dicho, esperado):
    assert normalizar(dicho) == esperado


def test_buscar_acierta_por_nombre_parcial():
    """«el dormitorio» tiene que dar con «Dormitorio principal»."""
    dormitorio = Luz(id="g3", nombre="Dormitorio principal", encendida=False, brillo=0)
    assert buscar("el dormitorio", [SALON, dormitorio]) is dormitorio


def test_buscar_prefiere_lo_exacto():
    otro = Luz(id="g9", nombre="Salón de arriba", encendida=False, brillo=0)
    assert buscar("salón", [otro, SALON]) is SALON


def test_buscar_no_se_inventa_una_luz():
    assert buscar("garaje", [SALON, COCINA]) is None


def test_los_grupos_van_antes_que_las_bombillas(puente):
    """Con una habitación y una bombilla llamadas igual, manda la habitación."""
    bombilla_salon = Luz(id="l9", nombre="Salón", encendida=False, brillo=0)
    falso = puente(grupos=[SALON], bombillas=[bombilla_salon])

    controlar_luces({"sitio": "el salón", "accion": "apagar"})

    assert [luz.id for luz, _ in falso.aplicado] == ["g1"]


# --- Colores ---------------------------------------------------------------


def test_un_color_va_en_coordenadas_y_un_blanco_en_temperatura():
    assert "xy" in cambios_de_color("rojo")["color"]
    assert cambios_de_color("cálido")["color_temperature"]["mirek"] == 450


def test_el_rojo_cae_donde_debe():
    """Sin esto, un error de signo en la conversión pasa desapercibido."""
    xy = cambios_de_color("rojo")["color"]["xy"]
    assert xy["x"] > 0.6 and xy["y"] < 0.4


def test_un_color_con_matiz_se_queda_con_el_color():
    assert cambios_de_color("azul clarito") == cambios_de_color("azul")


def test_un_color_que_no_existe_no_se_aproxima():
    assert cambios_de_color("color pistacho") is None


# --- Encender y apagar -----------------------------------------------------


def test_apagar_manda_apagar(puente):
    falso = puente(grupos=[SALON, COCINA])

    resultado = controlar_luces({"sitio": "el salón", "accion": "apagar"})

    assert not es_fallo(resultado)
    assert falso.aplicado == [(SALON, {"on": {"on": False}})]


def test_todo_alcanza_a_todos_los_grupos(puente):
    falso = puente(grupos=[SALON, COCINA])

    resultado = controlar_luces({"sitio": "toda la casa", "accion": "apagar"})

    assert [luz.id for luz, _ in falso.aplicado] == ["g1", "g2"]
    assert "toda la casa" in resultado


def test_alternar_mira_como_estaba(puente):
    falso = puente(grupos=[COCINA])                      # apagada

    controlar_luces({"sitio": "cocina", "accion": "alternar"})

    assert falso.aplicado[0][1] == {"on": {"on": True}}


def test_poner_brillo_enciende_la_luz(puente):
    """Atenuar una bombilla apagada no se ve: quien lo pide la quiere encendida."""
    falso = puente(grupos=[COCINA])

    controlar_luces({"sitio": "cocina", "brillo": 20})

    _, cambios = falso.aplicado[0]
    assert cambios["dimming"]["brightness"] == 20.0
    assert cambios["on"] == {"on": True}


def test_brillo_cero_apaga_de_verdad(puente):
    """En el puente, brillo 0 deja la luz al mínimo encendida. Aquí no."""
    falso = puente(grupos=[SALON])

    controlar_luces({"sitio": "salón", "brillo": 0})

    _, cambios = falso.aplicado[0]
    assert cambios["on"] == {"on": False}
    assert "dimming" not in cambios


def test_el_brillo_no_se_sale_de_la_escala(puente):
    falso = puente(grupos=[SALON])

    controlar_luces({"sitio": "salón", "brillo": 500})

    assert falso.aplicado[0][1]["dimming"]["brightness"] == 100.0


def test_un_brillo_que_no_es_un_numero_se_dice(puente):
    falso = puente(grupos=[SALON])

    resultado = controlar_luces({"sitio": "salón", "brillo": "muchísimo"})

    assert es_fallo(resultado)
    assert falso.aplicado == []


def test_pedir_un_color_tambien_enciende(puente):
    falso = puente(grupos=[SALON], bombillas=[LAMPARA])

    resultado = controlar_luces({"sitio": "lamparita", "color": "rojo"})

    _, cambios = falso.aplicado[0]
    assert cambios["on"] == {"on": True}
    assert "color" in cambios
    assert not es_fallo(resultado)


# --- Cuando no se puede ----------------------------------------------------


def test_un_sitio_que_no_existe_devuelve_los_que_si(puente):
    """El modelo tiene que poder preguntar «¿cuál de estas?» en vez de inventarse una."""
    falso = puente(grupos=[SALON, COCINA])

    resultado = controlar_luces({"sitio": "el garaje", "accion": "encender"})

    assert es_fallo(resultado)
    assert "Salón" in resultado and "Cocina" in resultado
    assert falso.aplicado == []


def test_sin_decir_que_hacer_no_se_toca_nada(puente):
    falso = puente(grupos=[SALON])

    resultado = controlar_luces({"sitio": "salón"})

    assert es_fallo(resultado)
    assert falso.aplicado == []


def test_un_color_desconocido_no_apaga_por_error(puente):
    """Devolver fallo sin haber mandado nada: lo peor sería dejarla a medias."""
    falso = puente(grupos=[SALON])

    resultado = controlar_luces({"sitio": "salón", "color": "ultravioleta"})

    assert es_fallo(resultado)
    assert falso.aplicado == []


def test_si_el_puente_falla_se_dice(puente):
    puente(grupos=[SALON], rompe=True)

    assert es_fallo(controlar_luces({"sitio": "salón", "accion": "apagar"}))


def test_una_bombilla_muerta_no_deja_el_resto_encendido(puente, monkeypatch):
    """En «apaga toda la casa», que una no responda no cancela las demás."""
    falso = puente(grupos=[SALON, COCINA])
    original = falso.aplicar

    def _falla_la_primera(luz, cambios):
        if luz.id == "g1":
            raise httpx.HTTPError("unreachable")
        original(luz, cambios)

    monkeypatch.setattr(falso, "aplicar", _falla_la_primera)
    resultado = controlar_luces({"sitio": "todo", "accion": "apagar"})

    assert not es_fallo(resultado)               # la cocina sí se apagó
    assert [luz.id for luz, _ in falso.aplicado] == ["g2"]


def test_sin_vincular_explica_como_arreglarlo(monkeypatch):
    def _sin_llave(*a, **k):
        raise SinVincular("todavía no hay llave del puente Hue")

    monkeypatch.setattr("maripepis.tools.hogar.conectar", _sin_llave)
    resultado = controlar_luces({"sitio": "salón", "accion": "apagar"})

    assert es_fallo(resultado)
    assert "maripepis-hue vincular" in resultado


def test_sin_puente_no_manda_reintentar(monkeypatch):
    """Reintentar contra un puente desenchufado solo alarga el silencio."""
    def _sin_puente(*a, **k):
        raise SinPuente("no encuentro el puente Hue en la red")

    monkeypatch.setattr("maripepis.tools.hogar.conectar", _sin_puente)
    resultado = controlar_luces({"sitio": "salón", "accion": "apagar"})

    assert es_fallo(resultado)
    assert "no lo reintentes" in resultado


# --- Escenas ---------------------------------------------------------------


def test_activar_una_escena_por_su_nombre(puente):
    falso = puente(grupos=[SALON], escenas=[("s1", "Relax", "g1")])

    resultado = controlar_luces({"sitio": "salón", "escena": "relax"})

    assert falso.escena_activada == "s1"
    assert not es_fallo(resultado)


def test_entre_dos_escenas_iguales_gana_la_del_sitio(puente):
    """Con un «Relax» por habitación, «pon relax en la cocina» es el de la cocina."""
    falso = puente(
        grupos=[SALON, COCINA],
        escenas=[("s1", "Relax", "g1"), ("s2", "Relax", "g2")],
    )

    controlar_luces({"sitio": "la cocina", "escena": "relax"})

    assert falso.escena_activada == "s2"


def test_una_escena_que_no_existe_devuelve_las_que_si(puente):
    falso = puente(grupos=[SALON], escenas=[("s1", "Relax", "g1")])

    resultado = controlar_luces({"sitio": "salón", "escena": "fiesta"})

    assert es_fallo(resultado)
    assert "Relax" in resultado
    assert falso.escena_activada is None


# --- Mirar cómo está -------------------------------------------------------


def test_todo_apagado_se_dice_en_una_frase(puente):
    puente(grupos=[COCINA])

    resultado = estado_de_las_luces({})

    assert not es_fallo(resultado)
    assert "apagado" in resultado


def test_el_estado_dice_cuales_y_a_cuanto(puente):
    puente(grupos=[SALON, COCINA])

    resultado = estado_de_las_luces({})

    assert "Salón" in resultado and "80%" in resultado
    assert "Cocina" not in resultado          # apagada: no se enumera


def test_el_estado_de_un_sitio_que_no_existe_es_un_fallo(puente):
    puente(grupos=[SALON])

    assert es_fallo(estado_de_las_luces({"sitio": "el garaje"}))


# --- Las herramientas ------------------------------------------------------


def test_las_dos_herramientas_se_construyen():
    nombres = {t.name for t in build_home_tools({})}
    assert nombres == {"controlar_luces", "estado_de_las_luces"}


def test_se_pueden_apagar_desde_la_configuracion():
    from maripepis.tools.system import build_default_tools

    nombres = {t.name for t in build_default_tools({"hogar": {"enabled": False}})}
    assert "controlar_luces" not in nombres


# --- El puente: encontrarlo y guardarse la llave ---------------------------


def test_el_mdns_se_lee_bien(monkeypatch):
    """La línea resuelta de avahi, tal como sale de verdad."""
    salida = (
        "+;enp12s0;IPv4;Hue Bridge - 25FDA8;_hue._tcp;local\n"
        "=;enp12s0;IPv6;Hue Bridge - 25FDA8;_hue._tcp;local;x.local;fe80::1;443;\n"
        "=;enp12s0;IPv4;Hue Bridge - 25FDA8;_hue._tcp;local;x.local;192.168.1.33;443;\n"
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=salida, stderr=""),
    )
    assert hue._por_mdns() == "192.168.1.33"


def test_sin_avahi_no_revienta(monkeypatch):
    def _no_esta(*a, **k):
        raise FileNotFoundError("avahi-browse")

    monkeypatch.setattr(subprocess, "run", _no_esta)
    assert hue._por_mdns() == ""


def test_la_llave_se_guarda_y_se_relee(monkeypatch, tmp_path):
    fichero = tmp_path / "hue.toml"
    monkeypatch.setattr(hue, "CREDENCIALES", fichero)

    hue.guardar_credenciales("192.168.1.33", "AbCd-1234")

    assert hue.leer_credenciales() == ("192.168.1.33", "AbCd-1234")


def test_la_llave_no_la_puede_leer_nadie_mas(monkeypatch, tmp_path):
    """Con esa cadena, cualquiera de la red enciende las luces de esta casa."""
    fichero = tmp_path / "hue.toml"
    monkeypatch.setattr(hue, "CREDENCIALES", fichero)

    hue.guardar_credenciales("192.168.1.33", "AbCd-1234")

    assert fichero.stat().st_mode & 0o077 == 0


def test_sin_fichero_no_hay_credenciales(monkeypatch, tmp_path):
    monkeypatch.setattr(hue, "CREDENCIALES", tmp_path / "no-existe.toml")
    assert hue.leer_credenciales() == ("", "")


def test_conectar_sin_llave_pide_vincular(monkeypatch, tmp_path):
    monkeypatch.setattr(hue, "CREDENCIALES", tmp_path / "no-existe.toml")

    with pytest.raises(SinVincular):
        hue.conectar()


def test_apagar_la_luz_a_secas_apaga_la_casa(puente):
    """«Apaga la luz», sin decir dónde, no es una habitación llamada «luz»."""
    falso = puente(grupos=[SALON, COCINA])

    resultado = controlar_luces({"sitio": "la luz", "accion": "apagar"})

    assert not es_fallo(resultado)
    assert [luz.id for luz, _ in falso.aplicado] == ["g1", "g2"]


# --- La orden de la shell (para claude-code, que no acepta nuestras tools) ---


@pytest.fixture
def peticiones(monkeypatch):
    """Intercepta controlar_luces y devuelve lo que se le ha pasado."""
    vistas = []

    def _falso(args):
        vistas.append(args)
        return "He dejado Salón apagada."

    monkeypatch.setattr("maripepis.tools.hogar.controlar_luces", _falso)
    return vistas


def test_la_orden_traduce_sus_banderas(peticiones):
    from maripepis.hogar.cliente import luz

    assert luz(["el salón", "--brillo", "20", "--color", "rojo"]) == 0
    assert peticiones[0] == {"sitio": "el salón", "brillo": "20", "color": "rojo"}


def test_la_orden_aguanta_las_banderas_sin_guiones(peticiones):
    """Un modelo que escribe la orden a mano se come los guiones a menudo."""
    from maripepis.hogar.cliente import luz

    luz(["cocina", "apagar", "brillo", "50"])

    assert peticiones[0] == {"sitio": "cocina", "accion": "apagar", "brillo": "50"}


def test_la_orden_sin_sitio_no_llama_a_nadie(peticiones):
    from maripepis.hogar.cliente import luz

    assert luz([]) == 2
    assert peticiones == []


def test_la_orden_sale_con_error_si_no_se_ha_hecho(monkeypatch, capsys):
    """Que se porte como una orden de shell: 1 es «no se ha hecho»."""
    from maripepis.hogar.cliente import luz

    monkeypatch.setattr(
        "maripepis.tools.hogar.controlar_luces",
        lambda args: "NO he tocado las luces: aquí no hay nada así.",
    )
    assert luz(["garaje", "apagar"]) == 1
    assert "NO he tocado" in capsys.readouterr().out


def test_el_estado_por_la_shell_pasa_el_sitio(monkeypatch, capsys):
    from maripepis.hogar.cliente import estado

    monkeypatch.setattr(
        "maripepis.tools.hogar.estado_de_las_luces",
        lambda args: f"pedido: {args}",
    )
    estado("la cocina")

    assert "{'sitio': 'la cocina'}" in capsys.readouterr().out


def test_las_instrucciones_llevan_la_orden_hecha():
    """Sin la orden dentro, el modelo se inventa un curl al puente."""
    from maripepis.cli import instrucciones_de_hogar_por_shell

    texto = instrucciones_de_hogar_por_shell("/ruta/maripepis-hue")

    assert "/ruta/maripepis-hue luz" in texto
    assert "/ruta/maripepis-hue estado" in texto
