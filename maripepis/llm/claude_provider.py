"""Proveedor nube: habla con la API de Anthropic (Claude) vía SDK oficial."""

from __future__ import annotations

from collections.abc import Iterator

from .base import LLMProvider


class ClaudeProvider(LLMProvider):
    def __init__(
        self,
        model: str = "claude-opus-4-8",
        max_tokens: int = 1024,
    ) -> None:
        try:
            import anthropic
        except ModuleNotFoundError as e:  # pragma: no cover - depende del entorno
            raise RuntimeError(
                "El backend 'claude' necesita el paquete `anthropic`. "
                'Instálalo con:  pip install -e ".[claude]"'
            ) from e

        # Lee ANTHROPIC_API_KEY del entorno (o un perfil de `ant auth login`).
        self.client = anthropic.Anthropic()

        # Sin clave, el SDK se construye tan feliz y no protesta hasta la primera
        # petición. Eso aquí es un desastre: maripepis arrancaría bien, o el
        # switch de la ventana se pondría en «Claude», y te enterarías al hablar
        # —que es cuando no estás mirando la pantalla—. Mejor no llegar a existir.
        if not (self.client.api_key or getattr(self.client, "auth_token", None)):
            raise RuntimeError(
                "El backend 'claude' necesita ANTHROPIC_API_KEY en el entorno. "
                "Expórtala (fish: set -x ANTHROPIC_API_KEY sk-ant-...) o usa el "
                "backend 'claude-code', que va con tu suscripción."
            )
        self.model = model
        self.max_tokens = max_tokens

    @property
    def label(self) -> str:
        return f"Claude · {self.model}"

    def stream_reply(self, system: str, messages: list[dict]) -> Iterator[str]:
        # Claude lleva el system FUERA de `messages`; estos deben empezar por
        # "user" y alternar (lo garantiza Conversation.messages).
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            yield from stream.text_stream

    def run_tools_turn(self, system, messages, tools, execute, max_iters: int = 5) -> str:
        claude_tools = [t.to_claude() for t in tools]
        msgs: list[dict] = list(messages)
        resp = None

        for _ in range(max_iters):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=msgs,
                tools=claude_tools,
            )
            if resp.stop_reason != "tool_use":
                break

            msgs.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": execute(block.name, block.input),
                        }
                    )
            msgs.append({"role": "user", "content": results})

        text = ""
        if resp is not None:
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or "(no pude completar la acción)"
