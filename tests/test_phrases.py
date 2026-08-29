from maripepis.utils.phrases import is_exit, normalize, strip_wake_word


def test_normalize_quita_acentos_y_puntuacion():
    assert normalize("¡Adiós, Maripepis!") == "adios maripepis"


def test_is_exit():
    exits = {normalize(x) for x in ["salir", "adiós maripepis", "hasta luego"]}
    assert is_exit("Salir.", exits)
    assert is_exit("¡Adiós, Maripepis!", exits)
    assert is_exit("Hasta luego", exits)
    assert not is_exit("qué hora es", exits)


def test_wake_word_desactivada_pasa_todo():
    assert strip_wake_word("hola qué tal", "") == (True, "hola qué tal")


def test_wake_word_coincide_y_extrae_comando():
    matched, cmd = strip_wake_word("Oye Maripepis, ¿qué hora es?", "oye maripepis")
    assert matched is True
    assert "hora" in cmd
    assert "maripepis" not in normalize(cmd)


def test_wake_word_no_coincide():
    matched, cmd = strip_wake_word("qué tiempo hace", "oye maripepis")
    assert matched is False
    assert cmd == ""


def test_wake_word_sola_sin_comando():
    matched, cmd = strip_wake_word("Oye Maripepis", "oye maripepis")
    assert matched is True
    assert cmd == ""
