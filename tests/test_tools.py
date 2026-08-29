import pytest

from maripepis.tools.base import Tool
from maripepis.tools.system import (
    abrir_aplicacion,
    buscar_en_internet,
    build_default_tools,
)


def test_build_default_tools():
    tools = build_default_tools()
    nombres = {t.name for t in tools}
    assert nombres == {
        "abrir_navegador", "buscar_en_internet", "abrir_aplicacion",
        "escribir_fichero", "ejecutar_comando",
    }
    for t in tools:
        assert isinstance(t, Tool)


def test_la_shell_se_puede_quitar():
    nombres = {t.name for t in build_default_tools({"shell": {"enabled": False}})}
    assert "ejecutar_comando" not in nombres
    assert "escribir_fichero" not in nombres   # también toca tus ficheros
    assert "abrir_aplicacion" in nombres


def test_conversion_a_formatos():
    t = build_default_tools()[0]
    o = t.to_ollama()
    assert o["type"] == "function"
    assert o["function"]["name"] == "abrir_navegador"
    assert "parameters" in o["function"]

    c = t.to_claude()
    assert c["name"] == "abrir_navegador"
    assert "input_schema" in c


def test_handlers_piden_argumentos_si_faltan():
    # Sin argumentos no lanzan nada; devuelven una pregunta.
    assert "?" in buscar_en_internet({})
    assert "?" in abrir_aplicacion({})


def test_buscar_sin_xdg_open(monkeypatch):
    import maripepis.tools.system as sys_tools

    monkeypatch.setattr(sys_tools.shutil, "which", lambda _: None)
    msg = buscar_en_internet({"consulta": "el tiempo en Madrid"})
    assert "xdg-open" in msg


def test_execute_via_tool_run(monkeypatch):
    import maripepis.tools.system as sys_tools

    lanzados = []
    monkeypatch.setattr(sys_tools.shutil, "which", lambda _: "/usr/bin/xdg-open")
    monkeypatch.setattr(sys_tools, "_launch", lambda args: lanzados.append(args))

    tools = {t.name: t for t in build_default_tools()}
    msg = tools["buscar_en_internet"].run({"consulta": "gatos"})
    assert "gatos" in msg
    assert lanzados and lanzados[0][0] == "xdg-open"
    assert "duckduckgo" in lanzados[0][1]


def test_no_dice_que_abre_lo_que_no_existe(monkeypatch):
    # El bug original: cualquier nombre caía en gtk-launch y devolvía éxito.
    import maripepis.tools.system as sys_tools

    lanzados = []
    monkeypatch.setattr(sys_tools.shutil, "which", lambda c: None)
    monkeypatch.setattr(sys_tools, "_desktop_entry", lambda n: None)
    monkeypatch.setattr(sys_tools, "_launch", lambda args: lanzados.append(args))

    msg = abrir_aplicacion({"nombre": "kitty"})

    assert "NO he abierto nada" in msg
    assert "kitty" in msg
    assert lanzados == []  # y sobre todo: no lanza nada


def test_abre_por_binario(monkeypatch):
    import maripepis.tools.system as sys_tools

    lanzados = []
    monkeypatch.setattr(sys_tools.shutil, "which", lambda c: "/usr/bin/" + c)
    monkeypatch.setattr(sys_tools, "_launch", lambda args, cwd=None: lanzados.append(args))

    assert abrir_aplicacion({"nombre": "firefox"}) == "He abierto firefox."
    assert lanzados == [["/usr/bin/firefox"]]


def test_abre_en_la_carpeta_pedida(monkeypatch, tmp_path):
    # El fallo real: el modelo mandaba `directorio` y se tiraba a la basura, así
    # que la terminal salía en el WorkingDirectory del demonio.
    import maripepis.tools.system as sys_tools

    lanzados = []
    monkeypatch.setattr(sys_tools.shutil, "which", lambda c: "/usr/bin/" + c)
    monkeypatch.setattr(sys_tools, "_launch", lambda args, cwd=None: lanzados.append((args, cwd)))

    msg = abrir_aplicacion({"nombre": "alacritty", "directorio": str(tmp_path)})

    assert lanzados == [(["/usr/bin/alacritty"], tmp_path)]
    assert str(tmp_path) in msg


def test_sin_carpeta_abre_en_el_home(monkeypatch, tmp_path):
    import maripepis.tools.system as sys_tools

    lanzados = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys_tools.shutil, "which", lambda c: "/usr/bin/" + c)
    monkeypatch.setattr(sys_tools, "_launch", lambda args, cwd=None: lanzados.append((args, cwd)))

    abrir_aplicacion({"nombre": "alacritty"})

    assert lanzados[0][1] == tmp_path  # no el cwd del demonio


def test_carpeta_inexistente_abre_igual_en_el_home(monkeypatch, tmp_path):
    # Antes no abría nada. Quedarse sin terminal porque el modelo se inventó la
    # carpeta es peor que abrirla en el home, mientras se avise de ello.
    import maripepis.tools.system as sys_tools

    lanzados = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys_tools.shutil, "which", lambda c: "/usr/bin/" + c)
    monkeypatch.setattr(sys_tools, "_launch", lambda args, cwd=None: lanzados.append((args, cwd)))

    msg = abrir_aplicacion({"nombre": "alacritty", "directorio": str(tmp_path / "no-existe")})

    assert lanzados == [(["/usr/bin/alacritty"], tmp_path)]
    assert "no existe" in msg


def test_terminal_por_su_nombre_generico(monkeypatch):
    # Hablando se pide «una terminal», no «xdg-terminal-exec».
    import maripepis.tools.system as sys_tools

    lanzados = []
    monkeypatch.setenv("TERMINAL", "xdg-terminal-exec")
    monkeypatch.setattr(sys_tools.shutil, "which",
                        lambda c: "/usr/bin/" + c if c == "xdg-terminal-exec" else None)
    monkeypatch.setattr(sys_tools, "_launch", lambda args, cwd=None: lanzados.append(args))

    assert abrir_aplicacion({"nombre": "una terminal"}).startswith("He abierto")
    assert lanzados == [["/usr/bin/xdg-terminal-exec"]]


def test_alias_cae_al_siguiente_candidato(monkeypatch):
    import maripepis.tools.system as sys_tools

    lanzados = []
    monkeypatch.delenv("FILEMANAGER", raising=False)
    monkeypatch.setattr(sys_tools.shutil, "which",
                        lambda c: "/usr/bin/" + c if c == "nautilus" else None)
    monkeypatch.setattr(sys_tools, "_launch", lambda args, cwd=None: lanzados.append(args))

    assert abrir_aplicacion({"nombre": "el gestor de archivos"}).startswith("He abierto")
    assert lanzados == [["/usr/bin/nautilus"]]


def test_abre_por_desktop_entry(monkeypatch):
    import maripepis.tools.system as sys_tools

    lanzados = []
    # No hay binario "nautilus", pero sí un org.gnome.Nautilus.desktop.
    monkeypatch.setattr(sys_tools.shutil, "which",
                        lambda c: "/usr/bin/gtk-launch" if c == "gtk-launch" else None)
    monkeypatch.setattr(sys_tools, "_desktop_entry", lambda n: "org.gnome.Nautilus")
    monkeypatch.setattr(sys_tools, "_launch", lambda args, cwd=None: lanzados.append(args))

    assert abrir_aplicacion({"nombre": "nautilus"}) == "He abierto nautilus."
    assert lanzados == [["gtk-launch", "org.gnome.Nautilus"]]


def test_desktop_entry_busca_en_los_directorios_xdg(tmp_path, monkeypatch):
    import maripepis.tools.system as sys_tools

    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "org.gnome.Nautilus.desktop").touch()
    (apps / "Alacritty.desktop").touch()
    # Aísla del sistema: XDG_DATA_DIRS vacío significa "usa los de por defecto"
    # (/usr/share), así que hay que apuntarlo también al directorio de prueba.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path))

    assert sys_tools._desktop_entry("alacritty") == "Alacritty"      # sin distinguir mayúsculas
    assert sys_tools._desktop_entry("nautilus") == "org.gnome.Nautilus"  # id con puntos
    assert sys_tools._desktop_entry("kitty") is None


def test_launch_usa_uwsm_para_salir_del_cgroup(monkeypatch):
    # Como servicio, lo lanzado hereda el cgroup y moriría al reiniciar Maripepis.
    import maripepis.tools.system as sys_tools

    llamadas = []
    monkeypatch.setattr(sys_tools.shutil, "which", lambda c: "/usr/bin/uwsm-app")
    monkeypatch.setattr(sys_tools.subprocess, "Popen",
                        lambda args, **k: llamadas.append(args))

    sys_tools._launch(["/usr/bin/firefox"])

    assert llamadas == [["uwsm-app", "--", "/usr/bin/firefox"]]


def test_launch_sin_uwsm_lanza_directo(monkeypatch):
    import maripepis.tools.system as sys_tools

    llamadas = []
    monkeypatch.setattr(sys_tools.shutil, "which", lambda c: None)
    monkeypatch.setattr(sys_tools.subprocess, "Popen",
                        lambda args, **k: llamadas.append(args))

    sys_tools._launch(["/usr/bin/firefox"])

    assert llamadas == [["/usr/bin/firefox"]]
