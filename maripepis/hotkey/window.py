"""Arranque de la ventana de chat: un proceso aparte, con otro Python.

Tres decisiones que explican por qué esto no es un `import`:

* **Otro intérprete.** La ventana es GTK4 vía `python-gobject`, que vive en el
  Python del **sistema**. El `.venv` del proyecto no lo ve, así que `sys.executable`
  no vale: se lanza con `python3` (o el que diga `[hotkey] window_python`).
* **Sin importar el paquete.** Se ejecuta el fichero por su ruta, no `-m`, para
  no depender de que PYTHONPATH sobreviva al `uwsm-app`. El visor solo necesita
  el socket y la biblioteca estándar.
* **`uwsm-app` por delante** (igual que en `tools/system.py`): si no, la ventana
  hereda el cgroup del servicio y se cierra con `systemctl --user restart maripepis`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

VISOR = Path(__file__).resolve().parents[1] / "ui" / "chat.py"


def _interprete(python: str = "") -> str | None:
    """El Python que ve GTK4: el configurado, o el `python3` del sistema."""
    exe = python or "python3"
    ruta = shutil.which(exe)
    if ruta:
        return ruta
    return exe if os.path.isfile(exe) and os.access(exe, os.X_OK) else None


def command(socket_path: str, python: str = "") -> list[str] | None:
    """El comando que abre la ventana, o None si falta el intérprete o el visor."""
    exe = _interprete(python)
    if exe is None or not VISOR.is_file():
        return None
    cmd = [exe, str(VISOR), "--socket", socket_path]
    if shutil.which("uwsm-app"):
        cmd = ["uwsm-app", "--", *cmd]
    return cmd


def launch(socket_path: str, logger, *, python: str = "") -> bool:
    """Abre la ventana en segundo plano. Nunca lanza: como mucho, se queda sin ella.

    `stderr` se hereda a propósito: si GTK protesta, se lee en
    `journalctl --user -u maripepis`. Una ventana que no abre en silencio es
    justo lo que no se puede depurar.
    """
    cmd = command(socket_path, python)
    if cmd is None:
        logger.warning(
            "No abro la ventana de chat: no encuentro %s o %s.",
            python or "python3", VISOR,
        )
        return False
    try:
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as e:
        logger.warning("No pude abrir la ventana de chat: %s", e)
        return False
    logger.info("Abro la ventana de chat (%s).", " ".join(cmd))
    return True
