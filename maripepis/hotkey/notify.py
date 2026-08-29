"""Avisos de escritorio con `notify-send`: la única interfaz cuando no hay terminal.

Dos detalles que no son opcionales:

* **Reemplazo en sitio.** Todos los avisos llevan la pista
  ``x-canonical-private-synchronous`` con el mismo valor, así mako sustituye el
  anterior en vez de apilar cuatro notificaciones por cada frase.
* **Escapado de markup.** mako interpreta markup de Pango, y la transcripción es
  texto del usuario: un `&` o un `<` sin escapar rompe el cuerpo del aviso.
"""

from __future__ import annotations

import shutil
import subprocess

_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def escape(text: str) -> str:
    """Escapa el markup de Pango. El `&` va primero para no escapar dos veces."""
    for old, new in _ESCAPES:
        text = text.replace(old, new)
    return text


class Notifier:
    """Avisos de escritorio. Nunca rompe: si falla, solo lo apunta en el log."""

    def __init__(self, logger, *, enabled: bool = True, app_name: str = "Maripepis",
                 max_chars: int = 240, sync_key: str = "maripepis",
                 command: str = "notify-send") -> None:
        self.logger = logger
        self.enabled = enabled
        self.app_name = app_name
        self.max_chars = max_chars
        self.sync_key = sync_key
        self.command = command
        self._available = shutil.which(command) is not None if enabled else False
        if enabled and not self._available:
            logger.warning("No encuentro `%s`; sigo sin avisos de escritorio.", command)

    def is_available(self) -> bool:
        return self._available

    def _clean(self, text: str) -> str:
        """Deja el texto en una línea, recortado y sin markup suelto."""
        one_line = " ".join(str(text).split())
        if self.max_chars > 0 and len(one_line) > self.max_chars:
            one_line = one_line[: self.max_chars - 1].rstrip() + "…"
        return escape(one_line)

    def _build_command(self, summary: str, body: str, urgency: str,
                       timeout_ms: int | None) -> list[str]:
        cmd = [self.command, "-a", self.app_name, "-u", urgency]
        if timeout_ms is not None:
            cmd += ["-t", str(int(timeout_ms))]
        cmd += ["-h", f"string:x-canonical-private-synchronous:{self.sync_key}"]
        cmd += [self._clean(summary)]
        if body:
            cmd += [self._clean(body)]
        return cmd

    def show(self, summary: str, body: str = "", *, urgency: str = "low",
             timeout_ms: int | None = None) -> None:
        """Muestra (o reemplaza) el aviso. El texto del usuario va en `body`."""
        if not self._available:
            self.logger.debug("aviso: %s · %s", summary, body)
            return
        try:
            subprocess.run(
                self._build_command(summary, body, urgency, timeout_ms),
                check=False, timeout=2,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:  # noqa: BLE001 - un aviso nunca debe tumbar un turno
            self.logger.debug("No pude avisar (%s): %s", e, summary)

    def error(self, body: str) -> None:
        self.show("⚠️ Maripepis", body, urgency="critical", timeout_ms=6000)
