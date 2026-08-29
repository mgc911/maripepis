"""Markdown → marcado de Pango, para enseñar un fichero dentro del chat.

Vive aquí al lado de `chat.py` y **solo usa biblioteca estándar**, a propósito y
por dos motivos:

* La ventana corre con el Python del sistema y sin PYTHONPATH: no puede importar
  `maripepis`. Pero sí importa a su vecino, porque al ejecutar un fichero por su
  ruta Python mete su directorio el primero en `sys.path`. De ahí `import marcado`.
* Al no tocar `gi`, esto sí se puede probar desde el `.venv` del proyecto
  (`maripepis.ui.marcado`), que es donde corren los tests. `chat.py` no.

No es un renderizador de Markdown: es el trozo que se usa al escribir un fichero
a mano —títulos, negritas, listas, código, enlaces— traducido al marcado de
Pango, que es lo que entiende `Gtk.Label`. Lo que no se reconoce se queda como
texto, que es el peor caso aceptable; lo que no se puede hacer es escupir marcado
roto, porque entonces `Gtk.Label` no pinta **nada**.
"""

from __future__ import annotations

import re

# Los tamaños de los seis niveles de título. Pango los entiende por nombre, y así
# no hay que pelearse con puntos ni con el tamaño de fuente del tema.
_TITULOS = ("xx-large", "x-large", "large", "medium", "medium", "medium")

_NEGRITA = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_CURSIVA = re.compile(
    r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])"
    r"|(?<![_\w])_(?!\s)(.+?)(?<!\s)_(?![_\w])"
)
_ENLACE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_VINETA = re.compile(r"^(\s*)[-*+]\s+")
_TITULO = re.compile(r"^(#{1,6})\s+(.*)$")
_REGLA = re.compile(r"^\s*([-*_])\1{2,}\s*$")
_CITA = re.compile(r"^\s*>\s?(.*)$")
_VALLA = re.compile(r"^\s*```")
# Las etiquetas que pone `_NEGRITA`, para no meter cursivas a caballo.
_ETIQUETA = re.compile(r"(</?b>)")


def escapar(texto: str) -> str:
    """Lo que Pango se tomaría como marcado, convertido en texto normal.

    Va SIEMPRE primero. Si se hace al revés, un `<b>` que venía dentro del
    fichero se pintaría como negrita y, peor, un `<` suelto rompería el marcado
    entero y la etiqueta se quedaría en blanco.
    """
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _enfasis(texto: str) -> str:
    """Negritas y cursivas, sin que se crucen entre sí.

    La negrita va primero (`**x**` no es la cursiva de `*x*`) y la cursiva se
    aplica **solo dentro de cada trozo** que queda entre las etiquetas de
    negrita. Aplicándola a la línea entera, un `___así___` salía
    `<b><i>x</b></i>`: etiquetas cruzadas, que para Pango es marcado roto y deja
    la etiqueta **en blanco**. Se prefiere un guion bajo de más en pantalla.
    """
    trozos = _ETIQUETA.split(_NEGRITA.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>",
                                          texto))
    return "".join(
        trozo if _ETIQUETA.fullmatch(trozo)
        else _CURSIVA.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", trozo)
        for trozo in trozos
    )


def _enlaces(texto: str) -> str:
    """Enlaces, y énfasis en todo lo demás.

    El enlace se aparta y deja una marca en su sitio; el énfasis se aplica al
    texto con las marcas puestas y luego se devuelven los enlaces. Dos motivos, y
    los dos se ven feos en pantalla:

    * El énfasis **no** puede entrar en la URL: un `http://…/mi_fichero_largo`
      acabaría con un `<i>` en medio del `href` y ahí ya no hay enlace que valga.
    * Pero sí tiene que poder rodearla: partir la línea en trozos dejaba los dos
      asteriscos de `**[texto](url)**` en trozos distintos, sin pareja, y se
      pintaban tal cual.

    La marca lleva NUL a los lados justo para que ninguna de las dos expresiones
    de énfasis pueda morderla.
    """
    guardados: list[str] = []

    def _apartar(m: re.Match) -> str:
        destino = m.group(2).replace('"', "&quot;")
        guardados.append(f'<a href="{destino}">{_enfasis(m.group(1))}</a>')
        return f"\x00{len(guardados) - 1}\x00"

    texto = _enfasis(_ENLACE.sub(_apartar, texto))
    for i, enlace in enumerate(guardados):
        texto = texto.replace(f"\x00{i}\x00", enlace)
    return texto


def _inline(texto: str) -> str:
    """Todo lo de dentro de una línea ya escapada.

    El código va primero y se aparta: dentro de `` `--foo*bar*` `` los asteriscos
    son asteriscos. Se trocea por las comillas invertidas y solo se formatea lo
    que cae fuera.
    """
    partes = texto.split("`")
    if len(partes) % 2 == 0:
        # Número impar de comillas invertidas: la última está suelta y no abre
        # nada. Se le devuelve la suya como texto, y así los pares no se
        # descuadran y no se traga media línea en monoespaciada.
        suelto = partes.pop()
        partes[-1] += "`" + suelto
    for i, parte in enumerate(partes):
        partes[i] = f"<tt>{parte}</tt>" if i % 2 else _enlaces(parte)
    return "".join(partes)


def a_pango(texto: str) -> str:
    """Traduce Markdown a marcado de Pango. Nunca devuelve marcado roto.

    Se escapa **por línea y después de reconocerla**, no antes: un `>` de cita se
    convierte en `&gt;` y dejaría de parecer una cita. Lo que sí llega escapado
    es todo lo que se le pasa a `_inline`.
    """
    salida: list[str] = []
    en_codigo = False

    for linea in texto.splitlines():
        if _VALLA.match(linea):
            en_codigo = not en_codigo   # la valla no se pinta: cambia de modo
            continue
        if en_codigo:
            salida.append(f"<tt>{escapar(linea)}</tt>" if linea.strip() else "")
            continue

        if _REGLA.match(linea):
            salida.append('<span alpha="45%">────────────</span>')
            continue

        if titulo := _TITULO.match(linea):
            nivel = len(titulo.group(1))
            salida.append(
                f'<span size="{_TITULOS[nivel - 1]}" weight="bold">'
                f"{_inline(escapar(titulo.group(2)))}</span>"
            )
            continue

        if cita := _CITA.match(linea):
            salida.append(
                f'<span alpha="65%">│ <i>{_inline(escapar(cita.group(1)))}</i></span>'
            )
            continue

        # La viñeta se cambia por un punto de verdad, respetando la sangría: es
        # lo que distingue una lista anidada de una lista a secas.
        salida.append(_inline(escapar(_VINETA.sub(r"\1• ", linea))))

    return "\n".join(salida)


def parece_markdown(ruta: str) -> bool:
    """¿Este fichero se pinta como Markdown, o tal cual en monoespaciada?

    Por la extensión y nada más. Adivinarlo por el contenido sale mal justo con
    lo que más duele: un script lleno de `#` no es un documento con seis títulos.
    """
    return ruta.lower().endswith((".md", ".markdown", ".mdown", ".mkd"))
