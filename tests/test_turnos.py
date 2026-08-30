"""La marca del turno: lo que distingue «el usuario ha dicho que sí» de «el
modelo se ha contestado a sí mismo».

Es un módulo de diez líneas y se prueba entero porque de él cuelga la única
barandilla mecánica del WhatsApp que envía. Todo lo demás de esa herramienta se
lo puede saltar un modelo lo bastante convencido; esto, no.
"""

from __future__ import annotations

import logging

from maripepis.tools.runner import Acciones
from maripepis.utils.turnos import TURNO_ENV, nuevo_turno, turno_actual


def test_cada_turno_estrena_marca():
    antes = turno_actual()
    despues = nuevo_turno()

    assert despues != antes
    assert turno_actual() == despues          # y se queda hasta el siguiente


def test_dos_lecturas_del_mismo_turno_dan_lo_mismo():
    nuevo_turno()
    assert turno_actual() == turno_actual()


def test_la_heredada_manda_sobre_la_del_proceso(monkeypatch):
    """Si venimos del `Bash` de Claude Code, el turno es el suyo.

    El nuestro dura lo que la orden, así que no significa nada: dos órdenes de la
    misma vuelta de conversación son procesos distintos y tienen que verse como
    un solo turno.
    """
    monkeypatch.setenv(TURNO_ENV, "el-turno-de-claude")
    assert turno_actual() == "el-turno-de-claude"

    nuevo_turno()                              # ni estrenando marca propia
    assert turno_actual() == "el-turno-de-claude"

    monkeypatch.delenv(TURNO_ENV)
    assert turno_actual() != "el-turno-de-claude"


def test_un_turno_de_herramientas_estrena_marca(monkeypatch):
    """`Acciones.reset()` es donde empieza un turno, y ahí se estrena.

    Va pegado a lo que ya se olvidaba —el último fallo, las llamadas— porque es
    lo mismo: lo del turno anterior deja de contar.
    """
    monkeypatch.delenv(TURNO_ENV, raising=False)
    acciones = Acciones([], logging.getLogger("test"))
    antes = turno_actual()

    acciones.reset()

    assert turno_actual() != antes


def test_dos_procesos_no_comparten_marca_de_arranque():
    """Preparar en una terminal y confirmar en otra son dos turnos de verdad.

    Con un contador que empezara en cero, dos procesos recién arrancados irían
    los dos por el mismo número y la confirmación a mano se negaría siempre a
    mandar nada.
    """
    import subprocess
    import sys

    def marca() -> str:
        return subprocess.run(  # noqa: S603
            [sys.executable, "-c",
             "from maripepis.utils.turnos import turno_actual; print(turno_actual())"],
            capture_output=True, text=True, check=True,
            env={"PATH": "/usr/bin", "PYTHONPATH": "."},
        ).stdout.strip()

    assert marca() != marca()
