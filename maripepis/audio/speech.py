"""Worker de voz: sintetiza y reproduce frases en segundo plano, en orden.

Permite ir hablando cada frase en cuanto está lista mientras el LLM sigue
generando las siguientes (menos latencia percibida). `stop()` descarta lo
pendiente e interrumpe la reproducción en curso (base para el barge-in).
"""

from __future__ import annotations

import queue
import threading


class SpeechWorker:
    def __init__(self, tts, player, logger) -> None:
        self.tts = tts
        self.player = player
        self.logger = logger
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def label(self) -> str:
        return self.tts.label

    def _run(self) -> None:
        while True:
            item = self._q.get()
            try:
                if item is None:
                    return
                if self._stop.is_set():
                    continue
                wav = self.tts.synthesize(item)
                if not self._stop.is_set():
                    self.player.play_wav_bytes(wav)
            except Exception as e:  # noqa: BLE001
                self.logger.warning("No pude reproducir la voz: %s", e)
            finally:
                self._q.task_done()

    def say(self, text: str) -> None:
        if text and text.strip():
            self._q.put(text)

    def wait(self) -> None:
        """Bloquea hasta que se ha reproducido todo lo encolado."""
        self._q.join()

    def stop(self) -> None:
        """Descarta la cola e interrumpe la reproducción actual (barge-in)."""
        self._stop.set()
        try:
            while True:
                self._q.get_nowait()
                self._q.task_done()
        except queue.Empty:
            pass
        self.player.stop()
        self._stop.clear()

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=3)
