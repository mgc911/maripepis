"""Configuración sencilla de logging."""

from __future__ import annotations

import logging


def get_logger(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Silencia el ruido de librerías (HTTP y Whisper) para no ensuciar la salida.
    for noisy in ("httpx", "httpcore", "urllib3", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("maripepis")
