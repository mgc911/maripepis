"""La casa: las luces, y lo que venga después.

Vive aparte de `tools/` por lo mismo que WhatsApp — aquí está el trato con el
aparato, y en `tools/hogar.py` solo la traducción de lo que se dice hablando— y
además porque hay una parte que no es del turno: vincularse al puente Hue se
hace una vez, desde la terminal, con el dedo encima del botón. Eso no cabe en
una herramienta que el modelo pueda llamar, ni debe.

De momento solo Hue. La API local del puente es de las buenas: sin nube, sin
token que caduque y con respuesta inmediata. Lo que entre después —altavoces
Cast, enchufes, Matter— entra por aquí al lado, cada uno con su propio módulo,
porque de Google no va a venir: no hay API suya que valga para esto (el porqué,
en `hue.py`).
"""

from .hue import (
    CREDENCIALES,
    Luz,
    Puente,
    SinPuente,
    SinVincular,
    conectar,
    descubrir,
    guardar_credenciales,
    leer_credenciales,
    pedir_llave,
)

__all__ = [
    "CREDENCIALES", "Luz", "Puente", "SinPuente", "SinVincular", "conectar",
    "descubrir", "guardar_credenciales", "leer_credenciales", "pedir_llave",
]
