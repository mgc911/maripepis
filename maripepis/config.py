"""Carga de la configuración desde `config.toml` (TOML nativo de Python 3.11+)."""

from __future__ import annotations

import tomllib
from pathlib import Path


def load_config(path: str | None = None) -> dict:
    """Devuelve la configuración como un diccionario anidado.

    Orden de búsqueda si no se pasa `path`:
      1. `config.toml` en el directorio actual.
      2. `config.toml` en la raíz del proyecto (junto al paquete).
    """
    if path:
        cfg_path = Path(path).expanduser()
    else:
        cfg_path = Path.cwd() / "config.toml"
        if not cfg_path.exists():
            cfg_path = Path(__file__).resolve().parent.parent / "config.toml"

    if not cfg_path.exists():
        raise FileNotFoundError(
            f"No encuentro config.toml (busqué en {cfg_path}). "
            "Pásalo con --config /ruta/a/config.toml."
        )

    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)

    # Deja la ruta a la vista: sin terminal (demonio), es lo primero que hay que
    # saber cuando algo no cuadra (rutas relativas como [tts].voice dependen del cwd).
    cfg["_path"] = str(cfg_path.resolve())
    return cfg
