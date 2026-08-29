"""Trocea un flujo de tokens del LLM en frases, para hablar mientras genera."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

_BOUNDARIES = ".!?…\n"


def _find_boundary(buf: str, min_chars: int) -> int | None:
    """Índice donde cortar una frase completa, o None si aún no la hay."""
    for i, ch in enumerate(buf):
        if ch in _BOUNDARIES and (i + 1) >= min_chars:
            j = i + 1
            if j < len(buf) and buf[j] == " ":
                j += 1  # incluye el espacio siguiente en el corte
            return j
    return None


def iter_sentences(
    tokens: Iterable[str],
    on_token: Callable[[str], None] | None = None,
    min_chars: int = 12,
) -> Iterator[str]:
    """Consume `tokens` y va emitiendo frases completas.

    `on_token` (opcional) se llama con cada token conforme llega, para poder
    imprimirlo en vivo aunque el troceado sea por frases. `min_chars` evita
    frases demasiado cortas (se acumulan hasta el siguiente signo de puntuación).
    """
    buf = ""
    for tok in tokens:
        if on_token is not None:
            on_token(tok)
        buf += tok
        while True:
            idx = _find_boundary(buf, min_chars)
            if idx is None:
                break
            sent, buf = buf[:idx], buf[idx:]
            if sent.strip():
                yield sent
    if buf.strip():
        yield buf
