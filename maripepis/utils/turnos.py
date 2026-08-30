"""En qué turno estamos. Lo justo para saber si dos cosas pasaron en el mismo.

Existe por una sola pregunta, y es la que sostiene la confirmación hablada de
WhatsApp: cuando el modelo dice «mándalo», ¿lo dice porque el usuario acaba de
decir que sí, o lo dice él solo, en la misma vuelta en la que redactó el mensaje?

La diferencia no se ve mirando los argumentos de la llamada —son los mismos— ni
el reloj —un bucle de herramientas tarda menos que un usuario en contestar, pero
no siempre—. Se ve mirando si entre las dos llamadas ha hablado alguien. Eso es
un turno, y esto es la marca que lo identifica.

Hay dos caminos por los que se llega a una herramienta, y los dos tienen que
poder contestar:

- **Dentro del proceso** (nuestras herramientas): `Acciones.reset()` llama a
  `nuevo_turno()` al empezar cada turno, que es justo donde ya se olvidaba lo
  del anterior.
- **Por la shell** (Claude Code, que trae las suyas y ejecuta la nuestra con
  `Bash`): cada turno es un proceso `claude` distinto, y todo lo que lance
  hereda su entorno. Por eso el proveedor le mete la marca en `MARIPEPIS_TURNO`
  y aquí se lee de ahí: dos órdenes de la misma vuelta traen la misma marca.

La marca de arranque es distinta en cada proceso a propósito. Así, dos
invocaciones a mano desde la terminal —preparar en una, confirmar en otra— son
dos turnos de verdad y no una sola vuelta que se refleja a sí misma.
"""

from __future__ import annotations

import os
from uuid import uuid4

#: Por dónde le llega la marca a un proceso hijo (la orden de WhatsApp lanzada
#: con `Bash` desde Claude Code).
TURNO_ENV = "MARIPEPIS_TURNO"

# Un identificador y no un contador: contando, dos procesos recién arrancados
# irían los dos por el «1» y se tomarían por el mismo turno.
_marca = uuid4().hex


def nuevo_turno() -> str:
    """Empieza un turno nuevo y devuelve su marca."""
    global _marca  # noqa: PLW0603 - es el estado que da sentido al módulo
    _marca = uuid4().hex
    return _marca


def turno_actual() -> str:
    """La marca del turno en curso.

    Manda la heredada: si venimos de un `Bash` de Claude Code, el turno es el
    suyo, y el nuestro (un proceso que dura lo que la orden) no significa nada.
    """
    return os.environ.get(TURNO_ENV) or _marca
