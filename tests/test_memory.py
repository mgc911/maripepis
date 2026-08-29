from maripepis.memory import load_memory, memory_path


def _cfg(tmp_path, **memoria):
    """Config mínima con `_path` apuntando a un config.toml de mentira."""
    return {"_path": str(tmp_path / "config.toml"), "memory": memoria}


def test_sin_fichero_no_hay_memoria(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert load_memory(_cfg(tmp_path, enabled=True)) == ""


def test_desactivada_no_lee_aunque_exista(tmp_path):
    (tmp_path / "memoria.md").write_text("Se llama Manu.", encoding="utf-8")
    assert load_memory(_cfg(tmp_path, enabled=False)) == ""


def test_memoria_junto_al_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "memoria.md").write_text("Se llama Manu.", encoding="utf-8")
    bloque = load_memory(_cfg(tmp_path, enabled=True))
    assert "Se llama Manu." in bloque
    # Llega como añadido al system prompt, con su contexto delante.
    assert bloque.startswith("\n\n")


def test_ruta_relativa_va_contra_el_config_no_contra_el_cwd(tmp_path, monkeypatch):
    (tmp_path / "datos").mkdir()
    (tmp_path / "datos" / "yo.md").write_text("Tiene una RTX 5070.", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)  # el demonio arranca desde otro sitio
    cfg = _cfg(tmp_path, enabled=True, path="datos/yo.md")
    assert memory_path(cfg) == tmp_path / "datos" / "yo.md"
    assert "RTX 5070" in load_memory(cfg)


def test_config_de_usuario_tiene_prioridad_sobre_la_del_proyecto(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config" / "maripepis").mkdir(parents=True)
    (home / ".config" / "maripepis" / "memoria.md").write_text("la de casa", encoding="utf-8")
    (tmp_path / "memoria.md").write_text("la del proyecto", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    assert "la de casa" in load_memory(_cfg(tmp_path, enabled=True))


def test_ruta_inexistente_no_rompe(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert load_memory(_cfg(tmp_path, enabled=True, path="no_existe.md")) == ""


def test_los_comentarios_html_no_llegan_al_modelo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "memoria.md").write_text(
        "<!--\n  nota para mí: sé breve\n-->\n\nSe llama Manu.", encoding="utf-8"
    )
    bloque = load_memory(_cfg(tmp_path, enabled=True))
    assert "Se llama Manu." in bloque
    assert "nota para mí" not in bloque


def test_recorta_a_max_chars_por_linea_entera(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "memoria.md").write_text("linea uno\nlinea dos\nlinea tres", encoding="utf-8")
    bloque = load_memory(_cfg(tmp_path, enabled=True, max_chars=15))
    assert "linea uno" in bloque
    assert "linea tres" not in bloque
    assert not bloque.endswith("linea d")  # corta por el salto de línea, no a mitad
