import pytest

from maripepis.tools.carpetas import (
    carpetas,
    descripcion,
    resolver,
    resolver_ruta,
    traducir_rutas,
)


@pytest.fixture
def home_es(monkeypatch, tmp_path):
    """Un home a la española, como el de un CachyOS recién instalado."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    (tmp_path / ".config").mkdir()
    (tmp_path / ".config" / "user-dirs.dirs").write_text(
        'XDG_DESKTOP_DIR="$HOME/"\n'
        'XDG_DOWNLOAD_DIR="$HOME/Descargas"\n'
        'XDG_DOCUMENTS_DIR="$HOME/Documentos"\n'
        'XDG_PICTURES_DIR="$HOME/Imágenes"\n',
        encoding="utf-8",
    )
    for d in ("Descargas", "Documentos", "Imágenes"):
        (tmp_path / d).mkdir()
    return tmp_path


def test_lee_las_carpetas_de_verdad(home_es):
    mapa = carpetas()
    assert mapa["descargas"] == home_es / "Descargas"
    assert mapa["documentos"] == home_es / "Documentos"
    assert mapa["escritorio"] == home_es          # aquí el escritorio ES el home


def test_sin_user_dirs_se_cae_a_los_nombres_ingleses(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "vacio"))
    assert carpetas()["descargas"] == tmp_path / "Downloads"


def test_entiende_los_nombres_hablados(home_es):
    assert resolver("descargas") == home_es / "Descargas"
    assert resolver("la carpeta descargas") == home_es / "Descargas"
    assert resolver("el escritorio") == home_es
    assert resolver("Documentos") == home_es / "Documentos"
    assert resolver("") == home_es


def test_entiende_las_carpetas_con_tilde_y_sus_sinonimos(home_es):
    assert resolver("imágenes") == home_es / "Imágenes"
    assert resolver("imagenes") == home_es / "Imágenes"
    assert resolver("fotos") == home_es / "Imágenes"


def test_una_carpeta_de_verdad_gana_al_nombre_hablado(home_es):
    # Quien tiene una carpeta llamada «fotos» la ha hecho a propósito; mandarle
    # las cosas a Imágenes porque hablando suena parecido sería peor.
    (home_es / "fotos").mkdir()
    assert resolver("fotos") == home_es / "fotos"


def test_expande_las_variables_que_el_modelo_se_inventa(home_es):
    # Path.expanduser() no toca $HOME, y `mkdir -p $HOME/x` con cwd malo se perdía.
    assert resolver("$HOME/Proyectos") == home_es / "Proyectos"
    assert resolver("~/Proyectos") == home_es / "Proyectos"
    assert resolver("Proyectos") == home_es / "Proyectos"   # relativo: cuelga del home


def test_traduce_las_carpetas_inglesas_que_aqui_no_existen(home_es):
    comando, cambios = traducir_rutas("echo hola > ~/Downloads/notas.txt")
    assert comando == f"echo hola > {home_es}/Descargas/notas.txt"
    assert cambios

    comando, _ = traducir_rutas("mkdir -p $HOME/Desktop/fotos")
    assert comando == f"mkdir -p {home_es}/fotos"


def test_no_toca_una_carpeta_inglesa_que_se_usa_de_verdad(home_es):
    # Si tiene cosas dentro, es suya: cambiarle la ruta sería peor el remedio.
    (home_es / "Downloads").mkdir()
    (home_es / "Downloads" / "algo.iso").touch()
    comando, cambios = traducir_rutas("ls ~/Downloads")
    assert comando == "ls ~/Downloads"
    assert not cambios


def test_no_confunde_carpetas_que_solo_empiezan_igual(home_es):
    comando, _ = traducir_rutas("ls ~/Downloads2")
    assert comando == "ls ~/Downloads2"


def test_ruta_de_fichero_como_se_dice_hablando(home_es):
    assert resolver_ruta("notas.txt") == home_es / "notas.txt"
    assert resolver_ruta("descargas/notas.txt") == home_es / "Descargas" / "notas.txt"
    assert resolver_ruta("notas.txt", "documentos") == home_es / "Documentos" / "notas.txt"
    assert resolver_ruta("~/Downloads/n.txt") == home_es / "Descargas" / "n.txt"
    assert resolver_ruta("/tmp/n.txt") == pytest.importorskip("pathlib").Path("/tmp/n.txt")


def test_no_apila_la_carpeta_dos_veces(home_es):
    # El modelo se cubre las espaldas diciendo el sitio dos veces; apilarlo
    # dejaría el fichero en Descargas/descargas, donde no lo busca nadie.
    assert resolver_ruta("descargas/manu.txt", "descargas") == home_es / "Descargas" / "manu.txt"
    assert (resolver_ruta("viaje/roma.txt", "documentos")
            == home_es / "Documentos" / "viaje" / "roma.txt")


def test_tampoco_apila_una_carpeta_cualquiera(home_es):
    # Salido de una petición real: carpeta="descargas/prueba" + ruta="prueba/hola.txt"
    # dejaba el fichero en Descargas/prueba/prueba/hola.txt.
    destino = resolver_ruta("prueba/hola.txt", "descargas/prueba")
    assert destino == home_es / "Descargas" / "prueba" / "hola.txt"


def test_la_descripcion_lleva_las_rutas_reales(home_es):
    texto = descripcion()
    assert str(home_es / "Descargas") in texto
    assert "~/Downloads" in texto      # y avisa de la que NO debe usar
