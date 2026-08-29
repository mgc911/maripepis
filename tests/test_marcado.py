"""El traductor de Markdown a marcado de Pango que usa la ventana de chat.

Se puede probar desde el `.venv` (a diferencia de `ui/chat.py`) porque `marcado`
no importa `gi`: es biblioteca estándar y nada más. Ver su cabecera.
"""

from maripepis.ui.marcado import a_pango, escapar, parece_markdown


def test_lo_que_romperia_el_marcado_se_escapa_primero():
    # Sin esto, Gtk.Label no pinta el texto: pinta nada.
    assert escapar("if a < b && c > d") == "if a &lt; b &amp;&amp; c &gt; d"
    assert a_pango("un <b>falso</b> negrita") == "un &lt;b&gt;falso&lt;/b&gt; negrita"


def test_titulos_por_nivel():
    assert a_pango("# Lista") == '<span size="xx-large" weight="bold">Lista</span>'
    assert a_pango("### Lista") == '<span size="large" weight="bold">Lista</span>'
    # Siete almohadillas ya no es un título: es texto que empieza por almohadillas.
    assert a_pango("####### no") == "####### no"


def test_negritas_cursivas_y_codigo():
    assert a_pango("**hola**") == "<b>hola</b>"
    assert a_pango("__hola__") == "<b>hola</b>"
    assert a_pango("*hola*") == "<i>hola</i>"
    assert a_pango("`ls -la`") == "<tt>ls -la</tt>"


def test_dentro_del_codigo_no_hay_formato():
    # Un asterisco en un comando es un asterisco, no el principio de una cursiva.
    assert a_pango("usa `rm *.tmp` y ya") == "usa <tt>rm *.tmp</tt> y ya"


def test_una_comilla_invertida_suelta_no_se_come_la_linea():
    assert a_pango("el fichero ` a medias") == "el fichero ` a medias"


def test_vinetas_y_sangria():
    assert a_pango("- leche\n- pan") == "• leche\n• pan"
    # La sangría es lo que distingue una lista anidada; se respeta.
    assert a_pango("- leche\n  - entera") == "• leche\n  • entera"


def test_bloque_de_codigo():
    salida = a_pango("```python\nx = 1\n```")

    assert salida == "<tt>x = 1</tt>"
    assert "```" not in salida


def test_dentro_de_un_bloque_de_codigo_tampoco_hay_formato():
    assert a_pango("```\n**esto no es negrita**\n```") == "<tt>**esto no es negrita**</tt>"


def test_enlaces():
    assert a_pango("[la wiki](https://es.wikipedia.org)") == (
        '<a href="https://es.wikipedia.org">la wiki</a>'
    )


def test_el_enfasis_no_entra_en_la_url():
    """Un `_` en la URL con `<i>` en medio deja de ser un enlace."""
    salida = a_pango("[doc](https://x.com/mi_fichero_largo.md)")

    assert salida == '<a href="https://x.com/mi_fichero_largo.md">doc</a>'
    assert "<i>" not in salida


def test_citas_y_reglas():
    assert "│" in a_pango("> ojo con esto")
    assert "<i>ojo con esto</i>" in a_pango("> ojo con esto")
    assert "───" in a_pango("---")


def test_texto_normal_se_queda_como_esta():
    assert a_pango("Lista de la compra para el sábado") == (
        "Lista de la compra para el sábado"
    )


def test_un_documento_entero():
    salida = a_pango(
        "# La compra\n\n"
        "Para el **sábado**:\n\n"
        "- leche\n"
        "- pan de `masa madre`\n\n"
        "> y no se te olvide el vino\n"
    )

    assert '<span size="xx-large" weight="bold">La compra</span>' in salida
    assert "<b>sábado</b>" in salida
    assert "• leche" in salida
    assert "<tt>masa madre</tt>" in salida
    assert "<i>y no se te olvide el vino</i>" in salida


def test_solo_se_pinta_como_markdown_lo_que_lo_es():
    assert parece_markdown("/home/manu/lista.md") is True
    assert parece_markdown("/home/manu/LISTA.MD") is True
    assert parece_markdown("/home/manu/notas.markdown") is True
    # Un script lleno de `#` no es un documento con seis títulos.
    assert parece_markdown("/home/manu/script.py") is False
    assert parece_markdown("/home/manu/notas.txt") is False


def test_las_etiquetas_no_se_cruzan():
    """`<b><i>x</b></i>` es marcado roto y Pango deja la etiqueta EN BLANCO.

    Se prefiere un guion bajo de más en pantalla a una línea que no se ve.
    """
    assert a_pango("___c___") == "<b>_c</b>_"
    salida = a_pango("__a *b__ c*")
    assert salida.count("<b>") == salida.count("</b>")
    assert "<b><i>" not in salida or "</i></b>" in salida


def test_negrita_alrededor_de_un_enlace():
    assert a_pango("**[otro](http://y.z/a_b)**") == (
        '<b><a href="http://y.z/a_b">otro</a></b>'
    )
