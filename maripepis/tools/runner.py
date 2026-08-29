"""Quien de verdad ejecuta las herramientas, y se acuerda de lo que pasó.

Acordarse no es un lujo: un modelo de 7B lee «NO he ejecutado nada» y contesta
«listo, ya la tienes» tan tranquilo. Por escrito cantaría; dicho en voz alta, y
sin ver la pantalla, no hay forma humana de distinguir esa mentira de que
funcione. Así que el resultado de verdad se apunta aquí, y el turno lo usa para
desmentir al modelo cuando hace falta.

Y de paso se cuenta fuera: quien mira la ventana de chat (o la terminal) ve la
orden que se ha ejecutado, no solo el «ya lo tienes» del modelo. Para eso está
`on_call`, un espectador opcional al que se le avisa de cada llamada.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from .base import Tool, es_fallo

# El argumento que de verdad cuenta de cada herramienta: lo que hay que enseñar
# es «mkdir -p ~/fotos», no el JSON entero de la llamada.
_ARGUMENTO_PRINCIPAL = {
    "ejecutar_comando": "comando",
    "escribir_fichero": "ruta",
    "buscar_en_internet": "consulta",
    "abrir_aplicacion": "nombre",
    "abrir_navegador": "url",
}
# Lo que no cabe en una línea y no se echa de menos: el texto de un fichero.
_ARGUMENTOS_LARGOS = ("contenido",)
_MAX_RESUMEN = 160


class Acciones:
    """Ejecuta las herramientas por su nombre y guarda el resultado del turno."""

    def __init__(self, tools: Iterable[Tool], logger: logging.Logger | None = None,
                 *, on_call: Callable[[str, dict, str], None] | None = None) -> None:
        self._por_nombre = {t.name: t for t in tools}
        self._log = logger or logging.getLogger("maripepis.tools")
        #: Espectador opcional, ``on_call(nombre, args, resultado)`` tras cada
        #: llamada: es lo que hace que los comandos se vean por ahí fuera (la
        #: ventana de chat, la REPL) en vez de quedarse en el log. Mira, no toca:
        #: lo que devuelva no cambia nada, y si revienta, el turno sigue.
        self.on_call = on_call
        #: Motivo del último fallo sin arreglar, o ``None`` si lo último salió bien.
        self.ultimo_fallo: str | None = None
        #: Cuántas herramientas se han llamado en este turno.
        self.llamadas = 0

    @property
    def nombres(self) -> set[str]:
        return set(self._por_nombre)

    def reset(self) -> None:
        """Turno nuevo: lo del anterior ya no cuenta."""
        self.ultimo_fallo = None
        self.llamadas = 0

    def __call__(self, nombre: str, args) -> str:  # noqa: ANN001
        # Se registra la llamada y su resultado: sin esto, cuando el asistente
        # dice «he abierto la terminal» no hay forma de saber si la abrió, si la
        # herramienta dijo que no o si se lo ha inventado.
        self.llamadas += 1
        resultado = self._ejecutar(nombre, args)
        self._avisar(nombre, args, resultado)
        return resultado

    def _ejecutar(self, nombre: str, args) -> str:  # noqa: ANN001
        """La llamada en sí: busca la herramienta, la corre y apunta el fallo."""
        tool = self._por_nombre.get(nombre)
        if tool is None:
            self._log.warning("Herramienta desconocida: %s", nombre)
            self.ultimo_fallo = f"no tengo ninguna herramienta llamada {nombre}"
            return f"Error: no existe ninguna herramienta llamada {nombre}."

        self._log.info("Herramienta %s(%s)", nombre, args)
        try:
            resultado = tool.run(args if isinstance(args, dict) else {})
        except Exception as e:  # noqa: BLE001
            self._log.error("Herramienta %s ha reventado: %s", nombre, e)
            self.ultimo_fallo = f"{nombre} ha dado un error: {e}"
            return f"Error ejecutando {nombre}: {e}"

        self._log.info("Herramienta %s → %s", nombre, " ".join(resultado.split())[:200])
        self.ultimo_fallo = resumen_del_fallo(resultado) if es_fallo(resultado) else None
        return resultado

    def _avisar(self, nombre: str, args, resultado: str) -> None:  # noqa: ANN001
        """Le cuenta la llamada al espectador. Nunca deja caer el turno por él."""
        if self.on_call is None:
            return
        try:
            self.on_call(nombre, args if isinstance(args, dict) else {}, resultado)
        except Exception as e:  # noqa: BLE001 - una ventana no tumba una acción
            self._log.warning("El espectador de acciones ha fallado: %s", e)


def resumen_del_fallo(resultado: str) -> str:
    """La primera frase del fallo, sin las instrucciones que van dirigidas al modelo.

    Lo que devuelven las herramientas cuando algo falla lleva una coletilla para
    que el modelo reintente («Corrige el comando y vuelve a llamarla»). Eso no se
    dice en voz alta: aquí se queda solo el motivo.
    """
    frase = " ".join(resultado.split())
    for corte in (". Corrige", ". Pregúntale", ". Díselo", ". Créala", ". Si de verdad"):
        frase = frase.split(corte)[0]
    frase = frase.split(". Ha dicho:")[0]
    frase = frase.removeprefix("NO he ejecutado nada: ").removeprefix("NO he escrito nada: ")
    frase = frase.removeprefix("NO he abierto nada: ").removeprefix("NO ha salido bien: ")
    frase = frase.rstrip(". ")
    return frase[:200]


def resumen_de_la_llamada(nombre: str, args) -> str:  # noqa: ANN001
    """Una línea con lo que se ha hecho, para leerla: la orden, no el JSON.

    ``("ejecutar_comando", {"comando": "mkdir -p ~/fotos"})``
    → ``ejecutar_comando · mkdir -p ~/fotos``

    De una herramienta sin argumento principal se enseña lo que traiga, menos el
    contenido de un fichero (que es el documento entero, no una orden).
    """
    if not isinstance(args, dict):
        args = {}
    clave = _ARGUMENTO_PRINCIPAL.get(nombre)
    valor = str(args.get(clave) or "") if clave else ""
    if not valor.strip():
        valor = ", ".join(
            f"{k}={v}" for k, v in args.items()
            if k not in _ARGUMENTOS_LARGOS and str(v).strip()
        )
    valor = " ".join(valor.split())
    if len(valor) > _MAX_RESUMEN:
        valor = valor[: _MAX_RESUMEN - 1].rstrip() + "…"
    return f"{nombre} · {valor}" if valor else nombre
