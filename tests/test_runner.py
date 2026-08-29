import logging

import pytest

from maripepis.tools.base import Tool, es_fallo
from maripepis.tools.runner import Acciones, resumen_de_la_llamada, resumen_del_fallo


def _tool(nombre, respuesta):
    return Tool(name=nombre, description="", parameters={}, handler=lambda args: respuesta)


@pytest.fixture
def log():
    return logging.getLogger("test")


def test_reconoce_el_fallo_de_una_herramienta():
    assert es_fallo("NO he ejecutado nada: ...")
    assert es_fallo("NO ha salido bien: ...")
    assert es_fallo("He cortado «sleep 30» a los 20 segundos")
    assert es_fallo("Error ejecutando abrir_aplicacion: ...")
    assert not es_fallo("Hecho: «mkdir fotos» ha terminado bien, sin salida.")


def test_apunta_lo_que_ha_fallado(log):
    acciones = Acciones([_tool("x", "NO ha salido bien: «cp a b» ha fallado con código 1.")], log)
    acciones("x", {})
    assert acciones.ultimo_fallo == "«cp a b» ha fallado con código 1"
    assert acciones.llamadas == 1


def test_un_reintento_con_exito_borra_el_fallo(log):
    fallar = ["NO ha salido bien: «mkdir x» ha fallado con código 1.", "Hecho: ya está."]
    tool = Tool(name="x", description="", parameters={},
                handler=lambda args: fallar.pop(0))
    acciones = Acciones([tool], log)
    acciones("x", {})
    acciones("x", {})
    assert acciones.ultimo_fallo is None


def test_una_herramienta_que_revienta_es_un_fallo(log):
    def _explota(args):
        raise RuntimeError("boom")

    acciones = Acciones([Tool(name="x", description="", parameters={}, handler=_explota)], log)
    assert acciones("x", {}).startswith("Error ejecutando")
    assert "boom" in acciones.ultimo_fallo


def test_herramienta_desconocida(log):
    acciones = Acciones([], log)
    assert acciones("inventada", {}).startswith("Error")
    assert acciones.ultimo_fallo


def test_el_turno_nuevo_empieza_limpio(log):
    acciones = Acciones([_tool("x", "NO he ejecutado nada: ...")], log)
    acciones("x", {})
    acciones.reset()
    assert acciones.ultimo_fallo is None
    assert acciones.llamadas == 0


def test_el_resumen_deja_fuera_lo_que_va_dirigido_al_modelo():
    largo = ("NO ha salido bien: «cp a b» ha fallado con código 1. Ha dicho: cp: no existe. "
             "Corrige el comando y vuelve a llamarla; no des por hecho que ha ido bien.")
    assert resumen_del_fallo(largo) == "«cp a b» ha fallado con código 1"


# ── lo que se enseña por ahí fuera ───────────────────────────────────────

def test_avisa_de_cada_llamada(log):
    vistas = []
    acciones = Acciones([_tool("x", "Hecho: ya está.")], log,
                        on_call=lambda n, a, r: vistas.append((n, a, r)))
    acciones("x", {"comando": "ls"})
    assert vistas == [("x", {"comando": "ls"}, "Hecho: ya está.")]


def test_tambien_avisa_de_lo_que_falla(log):
    vistas = []
    acciones = Acciones([], log, on_call=lambda n, a, r: vistas.append(r))
    acciones("inventada", {})
    assert vistas and vistas[0].startswith("Error")


def test_un_espectador_que_revienta_no_tumba_la_accion(log):
    def _explota(nombre, args, resultado):
        raise RuntimeError("la ventana se ha ido")

    acciones = Acciones([_tool("x", "Hecho: ya está.")], log, on_call=_explota)
    assert acciones("x", {}) == "Hecho: ya está."


def test_el_resumen_ensena_la_orden_y_no_el_json():
    assert resumen_de_la_llamada(
        "ejecutar_comando", {"comando": "mkdir -p ~/fotos"}
    ) == "ejecutar_comando · mkdir -p ~/fotos"
    assert resumen_de_la_llamada(
        "buscar_en_internet", {"consulta": "el tiempo en Madrid"}
    ) == "buscar_en_internet · el tiempo en Madrid"


def test_el_resumen_deja_fuera_el_contenido_de_un_fichero():
    # El documento entero no cabe en una línea, y lo que importa es dónde ha ido.
    resumen = resumen_de_la_llamada(
        "escribir_fichero", {"ruta": "compra.txt", "contenido": "leche\nhuevos"})
    assert resumen == "escribir_fichero · compra.txt"


def test_el_resumen_de_una_herramienta_sin_argumento_principal():
    assert resumen_de_la_llamada("abrir_navegador", {}) == "abrir_navegador"
    assert resumen_de_la_llamada("rara", {"a": "1", "b": "2"}) == "rara · a=1, b=2"
    assert resumen_de_la_llamada("rara", "no es un dict") == "rara"


def test_el_resumen_no_ocupa_media_ventana():
    largo = resumen_de_la_llamada("ejecutar_comando", {"comando": "echo " + "x" * 500})
    assert len(largo) < 200
    assert largo.endswith("…")


def test_sabe_que_herramientas_salieron_bien(log):
    acciones = Acciones([_tool("consultar_tiempo", "Parte de wttr.in: soleado."),
                         _tool("escribir_fichero", "NO he escrito nada: ya existe.")], log)
    acciones("consultar_tiempo", {})
    acciones("escribir_fichero", {})

    # La que falló no cuenta como hecha: es justo la que el modelo dirá que hizo.
    assert acciones.herramientas_ok() == {"consultar_tiempo"}


def test_las_instrucciones_para_el_modelo_no_se_dicen_en_voz_alta():
    # Antes se cortaba por una lista de frases, y el usuario acabó oyendo
    # «vuelve a llamarme en modo sobrescribir con el texto entero ya cor».
    from maripepis.tools.base import MARCA_MODELO

    largo = (f"NO he escrito nada: /home/manu/x.txt ya existe.{MARCA_MODELO} "
             'Repite AHORA esta misma llamada añadiendo modo="sobrescribir".')
    assert resumen_del_fallo(largo) == "/home/manu/x.txt ya existe"
