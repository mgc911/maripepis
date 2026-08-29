"""Motor STT con faster-whisper (transcribe WAV a texto, local)."""

from __future__ import annotations

import os
import tempfile

from .base import STTEngine


class WhisperEngine(STTEngine):
    def __init__(
        self,
        model: str = "small",
        language: str = "es",
        device: str = "auto",
        compute_type: str = "int8",
        initial_prompt: str = "",
        beam_size: int = 5,
    ) -> None:
        self.model_name = model
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self.initial_prompt = initial_prompt
        self.beam_size = beam_size
        self._model = None  # se carga de forma perezosa (es costoso)

    @property
    def label(self) -> str:
        return f"Whisper · {self.model_name}"

    def check(self) -> None:
        try:
            import faster_whisper  # noqa: F401
        except ModuleNotFoundError as e:  # pragma: no cover - depende del entorno
            raise RuntimeError(
                "La escucha por voz necesita `faster-whisper`. "
                'Instálalo con:  pip install -e ".[stt]"'
            ) from e

    def load(self) -> None:
        """Carga el modelo (descarga la primera vez). Idempotente."""
        if self._model is not None:
            return
        self.check()
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.model_name, device=self.device, compute_type=self.compute_type
        )

    def transcribe(self, wav_bytes: bytes) -> str:
        self.load()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
            tmp.write(wav_bytes)

        try:
            lang = self.language if self.language not in (None, "", "auto") else None
            segments, _info = self._model.transcribe(
                path,
                language=lang,
                beam_size=self.beam_size,
                initial_prompt=self.initial_prompt or None,  # pista de vocabulario/contexto
                vad_filter=True,  # filtro VAD interno: descarta no-voz antes de transcribir
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,  # evita alucinaciones encadenadas
            )
            return "".join(seg.text for seg in segments).strip()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
