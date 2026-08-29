"""Ayudas de texto: normalización, detección de salida y palabra de activación."""

from __future__ import annotations

import unicodedata


def normalize(text: str) -> str:
    """Minúsculas, sin acentos ni puntuación, espacios colapsados."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")  # sin diacríticos
    cleaned = "".join(c if (c.isalnum() or c.isspace()) else " " for c in text.lower())
    return " ".join(cleaned.split())


def is_exit(text: str, exit_set: set[str]) -> bool:
    """True si el texto (normalizado) es una frase de salida."""
    return normalize(text) in exit_set


def strip_wake_word(text: str, wake_word: str) -> tuple[bool, str]:
    """Aplica la palabra de activación.

    Devuelve ``(coincide, comando)``:
      - Sin `wake_word` configurada → siempre ``(True, text)``.
      - Si el texto empieza por la palabra de activación → ``(True, resto)``.
      - Si no → ``(False, "")`` (no nos hablaban a nosotros).
    """
    if not wake_word:
        return True, text

    wn = normalize(wake_word)
    tn = normalize(text)
    if tn != wn and not tn.startswith(wn + " "):
        return False, ""

    n_words = len(wn.split())
    parts = text.split(None, n_words)
    command = parts[n_words] if len(parts) > n_words else ""
    command = command.lstrip(" ,.:;¿¡-–—").strip()
    return True, command
