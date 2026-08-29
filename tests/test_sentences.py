from maripepis.utils.sentences import iter_sentences


def test_reconstruye_el_texto_completo():
    toks = list("Hola. ¿Qué tal? Todo bien por aquí. Fin.")
    sents = list(iter_sentences(iter(toks)))
    assert "".join(sents) == "".join(toks)
    assert len(sents) >= 2  # se ha troceado en varias frases


def test_on_token_se_llama_por_cada_token():
    seen: list[str] = []
    list(iter_sentences(iter(["a", "b", "c."]), on_token=seen.append, min_chars=1))
    assert seen == ["a", "b", "c."]


def test_respuesta_corta_es_una_sola_frase():
    assert list(iter_sentences(iter(["Sí."]))) == ["Sí."]


def test_flujo_por_frases():
    toks = ["Prime", "ra fra", "se larga. ", "Segun", "da frase larga tambien."]
    sents = list(iter_sentences(iter(toks)))
    assert sents[0].strip() == "Primera frase larga."
    assert "".join(sents) == "".join(toks)
