"""Portapapeles de Wayland vía `wl-copy` (paquete wl-clipboard)."""

from __future__ import annotations

import shutil
import subprocess


def is_available(command: str = "wl-copy") -> bool:
    return shutil.which(command) is not None


def copy(text: str, *, command: str = "wl-copy", logger=None) -> bool:
    """Copia `text` al portapapeles. Devuelve False (y avisa) si no puede.

    Nada de `--foreground`: `wl-copy` se queda de fondo como dueño de la
    selección (así funciona Wayland) y bloquearía el turno.
    """
    if not is_available(command):
        if logger:
            logger.warning(
                "No encuentro `%s`; instala wl-clipboard para el dictado.", command
            )
        return False
    try:
        subprocess.run(
            [command], input=text.encode("utf-8"), check=False, timeout=3,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:  # noqa: BLE001
        if logger:
            logger.warning("No pude copiar al portapapeles: %s", e)
        return False


def paste(*, delay_ms: int = 250, logger=None) -> None:
    """Pega en la ventana activa (SHIFT+Insert) tras soltar los modificadores.

    Desactivado por defecto: `hyprctl dispatch sendshortcut` puede dejar teclas
    sintéticas pegadas (hyprwm/Hyprland#14099) y al soltar ALT+SHIFT+Z todavía
    tienes los modificadores pulsados. Ver `[hotkey].auto_paste` en config.toml.
    """
    import threading

    def _pega() -> None:
        try:
            subprocess.run(
                ["hyprctl", "dispatch", "sendshortcut", "SHIFT, Insert, activewindow"],
                check=False, timeout=3,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:  # noqa: BLE001
            if logger:
                logger.warning("No pude pegar automáticamente: %s", e)

    threading.Timer(max(0, delay_ms) / 1000, _pega).start()
