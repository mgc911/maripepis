"""Lo común a todos los tests.

Desde que las herramientas salen a internet (`buscar_en_internet`,
`consultar_tiempo`), un test que se olvide de simular la petición deja de probar
nada y se pone a depender de que la Wikipedia conteste hoy. Aquí se corta por lo
sano: la red está cerrada salvo que el test la abra a propósito.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Ningún test sale a internet sin decirlo: `httpx.get` revienta si se llama."""
    def _prohibido(*args, **kwargs):
        raise AssertionError(
            f"Un test ha intentado salir a la red ({args[:1]}). Simula la petición "
            "con monkeypatch en vez de depender de que el servidor conteste."
        )

    monkeypatch.setattr(httpx, "get", _prohibido)
    monkeypatch.setattr(httpx, "post", _prohibido)
    monkeypatch.setattr(httpx, "stream", _prohibido)
