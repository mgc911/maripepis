"""Historial de conversación en formato neutro, agnóstico del proveedor."""

from __future__ import annotations


class Conversation:
    """Guarda el historial y lo entrega listo para cualquier proveedor.

    Mantiene los turnos como ``{"role": "user"|"assistant", "content": str}``
    alternando y empezando por ``user``. Al leer :attr:`messages`, recorta al
    tamaño configurado y garantiza que el primer mensaje sea de ``user``, que es
    lo que exige Claude.
    """

    def __init__(self, system_prompt: str, max_history: int = 10) -> None:
        self.system_prompt = system_prompt
        self.max_history = max_history
        self._history: list[dict] = []

    def add_user(self, text: str) -> None:
        self._history.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self._history.append({"role": "assistant", "content": text})

    def reset(self) -> None:
        """Vacía el historial y mantiene el system prompt (conversación nueva)."""
        self._history.clear()

    def undo_last_user(self) -> None:
        """Elimina el último turno de usuario (p.ej. si falló la respuesta)."""
        if self._history and self._history[-1]["role"] == "user":
            self._history.pop()

    @property
    def messages(self) -> list[dict]:
        if self.max_history > 0:
            msgs = self._history[-self.max_history :]
        else:
            msgs = list(self._history)
        # Nunca empezar por un turno de asistente.
        if msgs and msgs[0]["role"] == "assistant":
            msgs = msgs[1:]
        return msgs
