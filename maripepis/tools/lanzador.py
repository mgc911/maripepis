"""Abrir una aplicación y desentenderse de ella, sin llevársela por delante.

Vive aparte porque lo usan dos herramientas que no se conocen entre sí
(`system.abrir_aplicacion` y `whatsapp.preparar_mensaje_whatsapp`), y meterlo en
cualquiera de las dos obligaba a la otra a importarla en círculo.

Lo que resuelve es un fallo que solo se ve corriendo como servicio: `systemd`
mata el cgroup entero al reiniciar la unidad, así que sin `uwsm-app` un
`systemctl --user restart maripepis` cierra de golpe todo lo que el asistente
hubiera abierto — el navegador, la terminal, tu WhatsApp.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("maripepis.tools")


def lanzar(args: list[str], cwd: Path | None = None) -> None:
    """Lanza un proceso en segundo plano, desligado de maripepis.

    Con `uwsm-app` (Hyprland/uwsm) la aplicación va a su propio *scope* de
    systemd. Importa cuando Maripepis corre como servicio: `start_new_session`
    cambia la sesión, pero **no el cgroup**, así que sin esto todo lo que abriera
    moriría con un `systemctl --user restart maripepis`.

    `cwd` importa más de lo que parece: sin él, una terminal abierta por el
    demonio hereda su `WorkingDirectory` (el del proyecto) y aparece en un sitio
    que no tiene nada que ver con lo que has pedido.
    """
    if shutil.which("uwsm-app"):
        args = ["uwsm-app", "--", *args]
    log.info("Lanzo %s%s", args, f" desde {cwd}" if cwd else "")
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(cwd) if cwd else None,
    )
