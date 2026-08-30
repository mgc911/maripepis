import pytest

from maripepis.tools.base import es_fallo
from maripepis.tools.ficheros import (
    build_file_tool,
    escribir_fichero,
    leer_fichero,
    para_la_ventana,
)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


def test_escribe_lo_que_le_dictas(home):
    msg = escribir_fichero({"ruta": "lista.txt", "contenido": "leche\npan\nhuevos"})
    assert msg.startswith("Hecho")
    assert (home / "lista.txt").read_text(encoding="utf-8") == "leche\npan\nhuevos\n"


def test_los_acentos_y_las_comillas_no_se_pelean_con_nada(home):
    # El motivo de que exista la herramienta: con `echo ... > x` esto es un
    # campo de minas, y el modelo, antes que arriesgarse, abre un editor.
    texto = "Mañana: comprar «pan», leer O'Brien y $PATH literal"
    escribir_fichero({"ruta": "notas.md", "contenido": texto})
    assert (home / "notas.md").read_text(encoding="utf-8").strip() == texto


def test_no_pisa_un_fichero_sin_permiso(home):
    (home / "notas.txt").write_text("lo de antes\n", encoding="utf-8")
    msg = escribir_fichero({"ruta": "notas.txt", "contenido": "lo nuevo"})
    assert msg.startswith("NO he escrito nada")
    assert (home / "notas.txt").read_text(encoding="utf-8") == "lo de antes\n"


def test_sobrescribe_si_se_lo_piden(home):
    (home / "notas.txt").write_text("lo de antes\n", encoding="utf-8")
    escribir_fichero({"ruta": "notas.txt", "contenido": "lo nuevo", "modo": "sobrescribir"})
    assert (home / "notas.txt").read_text(encoding="utf-8") == "lo nuevo\n"


def test_anade_al_final_sin_pegar_las_lineas(home):
    (home / "notas.txt").write_text("leche\n", encoding="utf-8")
    escribir_fichero({"ruta": "notas.txt", "contenido": "pan", "modo": "añadir"})
    assert (home / "notas.txt").read_text(encoding="utf-8") == "leche\npan\n"


def test_anade_aunque_lo_anterior_no_acabara_en_salto(home):
    (home / "notas.txt").write_text("leche", encoding="utf-8")
    escribir_fichero({"ruta": "notas.txt", "contenido": "pan", "modo": "añadir"})
    assert (home / "notas.txt").read_text(encoding="utf-8") == "leche\npan\n"


def test_crea_las_carpetas_que_falten(home):
    # Sin user-dirs.dirs, «documentos» es el ~/Documents de toda la vida.
    escribir_fichero({"ruta": "documentos/viaje/roma.txt", "contenido": "hotel"})
    assert (home / "Documents" / "viaje" / "roma.txt").read_text(encoding="utf-8") == "hotel\n"


def test_no_escribe_encima_de_una_carpeta(home):
    (home / "cosas").mkdir()
    assert escribir_fichero({"ruta": "cosas", "contenido": "x"}).startswith("NO he escrito nada")


def test_pide_lo_que_le_falta(home):
    assert "?" in escribir_fichero({})
    assert "?" in escribir_fichero({"ruta": "x.txt"})


def test_esquema_para_los_proveedores():
    tool = build_file_tool()
    esquema = tool.to_claude()["input_schema"]
    assert esquema["required"] == ["ruta", "contenido"]
    assert "añadir" in esquema["properties"]["modo"]["enum"]


# --- Leer ------------------------------------------------------------------
# La mitad que faltaba: sin ella, «revisa el documento que me hiciste» no tiene
# respuesta posible y el modelo se inventa lo que pone.

def test_lee_lo_que_hay_dentro(home):
    (home / "resumen.txt").write_text("Lunes: sol\nMartes: lluvia\n", encoding="utf-8")

    msg = leer_fichero({"ruta": "resumen.txt"})

    assert not es_fallo(msg)
    assert "Lunes: sol" in msg and "Martes: lluvia" in msg
    assert str(home / "resumen.txt") in msg      # y dónde estaba, para el turno siguiente


def test_lee_de_la_carpeta_que_se_le_diga(home):
    # Lo que devuelve escribir_fichero y lo que lee esta tienen que casar: es el
    # ciclo «créame el documento» → «revísalo» del que salió todo esto.
    escribir_fichero({"ruta": "notas.txt", "carpeta": "descargas", "contenido": "hola"})
    assert "hola" in leer_fichero({"ruta": "notas.txt", "carpeta": "descargas"})


def test_leer_lo_que_no_existe_no_se_disimula(home):
    msg = leer_fichero({"ruta": "fantasma.txt"})
    assert es_fallo(msg)
    assert "no existe" in msg


def test_no_lee_una_carpeta(home):
    (home / "cosas").mkdir()
    assert es_fallo(leer_fichero({"ruta": "cosas"}))


def test_no_lee_un_binario(home):
    (home / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    msg = leer_fichero({"ruta": "foto.png"})
    assert es_fallo(msg)
    assert "no es un fichero de texto" in msg


def test_un_fichero_vacio_lo_dice_sin_dar_error(home):
    (home / "vacio.txt").write_text("", encoding="utf-8")
    msg = leer_fichero({"ruta": "vacio.txt"})
    assert not es_fallo(msg)
    assert "vacío" in msg


def test_recorta_lo_muy_largo_y_avisa(home, monkeypatch):
    monkeypatch.setattr("maripepis.tools.ficheros.MAX_LECTURA", 50)
    (home / "diario.txt").write_text("a" * 500, encoding="utf-8")

    msg = leer_fichero({"ruta": "diario.txt"})

    assert "primeros 50 caracteres" in msg
    assert msg.count("a") < 200          # no ha metido el fichero entero


def test_sin_ruta_pregunta(home):
    assert "?" in leer_fichero({})


# ── el fichero que se enseña en la ventana ───────────────────────────────


def test_la_ruta_que_se_enseña_es_la_que_se_escribio(home):
    """`fichero_escrito` y `escribir_fichero` tienen que resolver IGUAL.

    Si no, la ventana enseña otro fichero (o ninguno) mientras la voz dice que ya
    está: lo peor de los dos mundos. Aquí se comprueba con la ruta dicha como se
    dice hablando —«lista.md» a secas, «documentos» aparte—, que es donde la
    resolución tiene miga.
    """
    from maripepis.tools.runner import fichero_de_la_llamada

    args = {"ruta": "lista.md", "carpeta": "documentos", "contenido": "- pan\n"}
    escribir_fichero(args)

    destino = fichero_de_la_llamada("escribir_fichero", args)
    assert destino is not None
    assert destino.read_text(encoding="utf-8") == "- pan\n"


def test_para_la_ventana_devuelve_el_fichero_entero(home):
    destino = home / "nota.md"
    destino.write_text("# Hola\n\ntexto\n", encoding="utf-8")

    assert para_la_ventana(destino) == (str(destino), "# Hola\n\ntexto\n")


def test_para_la_ventana_calla_con_lo_que_no_se_puede_enseñar(home):
    vacio = home / "vacio.md"
    vacio.write_text("", encoding="utf-8")
    binario = home / "foto.png"
    binario.write_bytes(b"\x89PNG\x00\x01\x02")

    assert para_la_ventana(home / "no-existe.md") is None
    assert para_la_ventana(vacio) is None
    assert para_la_ventana(binario) is None
    assert para_la_ventana(home) is None          # una carpeta tampoco


def test_un_fichero_larguisimo_se_recorta_antes_de_mandarlo(home):
    from maripepis.tools.ficheros import MAX_VENTANA

    destino = home / "largo.md"
    destino.write_text("x" * (MAX_VENTANA + 5000), encoding="utf-8")

    _, contenido = para_la_ventana(destino)

    assert len(contenido) < MAX_VENTANA + 200
    assert contenido.endswith("[…recortado: el fichero sigue en el disco]")


def test_leer_un_fichero_tambien_dice_cual_es(home):
    """«enséñame el resumen que me hiciste» enseña el documento, no solo lo dice."""
    from maripepis.tools.runner import fichero_de_la_llamada

    args = {"ruta": "resumen.md", "carpeta": "documentos"}
    # La carpeta la resuelve `resolver_ruta` (Documents/Documentos, según el
    # sistema): se pregunta dónde iría antes de escribir ahí.
    destino = fichero_de_la_llamada("leer_fichero", args)
    assert destino is not None
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("# Resumen\n", encoding="utf-8")

    assert "# Resumen" in leer_fichero(args)
    assert para_la_ventana(destino) == (str(destino), "# Resumen\n")


def test_de_un_comando_no_se_adivina_el_fichero(home):
    from maripepis.tools.runner import fichero_de_la_llamada

    assert fichero_de_la_llamada("ejecutar_comando", {"comando": "cat lista.md"}) is None
