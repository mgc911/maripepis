import pytest

from maripepis.tools.ficheros import build_file_tool, escribir_fichero


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
    esquema = tool.to_ollama()["function"]["parameters"]
    assert esquema["required"] == ["ruta", "contenido"]
    assert "añadir" in esquema["properties"]["modo"]["enum"]
