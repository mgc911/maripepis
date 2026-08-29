"""Memoria permanente: lo que Maripepis sabe del usuario y de su equipo.

Es un fichero de texto (Markdown) que se añade al *system prompt* al arrancar,
así que **sobrevive a reinicios** y a ``Conversation.reset()`` — al contrario que
el historial, que se recorta con ``max_history`` y se olvida solo tras
``[hotkey].context_timeout_s``.

Va aparte del ``system_prompt`` de ``config.toml`` a propósito: eso define *cómo*
habla (tono, longitud), y esto *qué sabe*. Editar la memoria no requiere tocar
código ni configuración: se guarda el fichero y `systemctl --user restart
maripepis`.

Ojo: viaja en **cada** petición al LLM. Cuanto más corta, menos latencia (y menos
coste con el backend Claude); de ahí el recorte de `max_chars`.
"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_FILENAME = "memoria.md"
DEFAULT_MAX_CHARS = 4000

# Los comentarios HTML son para quien edita el fichero, no para el modelo: así se
# pueden dejar notas dentro sin gastar prompt ni confundir al LLM con órdenes
# que no van con él.
_COMENTARIO = re.compile(r"<!--.*?-->", re.DOTALL)

_PREAMBULO = (
    "\n\nEsto es lo que ya sabes del usuario y de su equipo (memoria permanente). "
    "Úsalo cuando venga al caso, sin recitarlo ni mencionar que lo tienes escrito. "
    "Si algo no está aquí, dilo en vez de inventarlo:\n\n"
)


def _config_dir(cfg: dict) -> Path:
    """Directorio del `config.toml` en uso (o el actual, si no consta)."""
    ruta = cfg.get("_path")
    return Path(ruta).resolve().parent if ruta else Path.cwd()


def memory_path(cfg: dict) -> Path | None:
    """Devuelve la ruta del fichero de memoria, o ``None`` si no hay ninguno.

    Orden de búsqueda:
      1. ``[memory].path`` (si es relativa, contra el directorio del `config.toml`).
      2. ``~/.config/maripepis/memoria.md`` — el sitio para datos personales.
      3. ``memoria.md`` junto al `config.toml`.

    Las rutas relativas se resuelven contra el `config.toml` y **no** contra el
    directorio actual: el demonio arranca desde systemd, donde el `cwd` no es
    necesariamente el del proyecto.
    """
    mem = cfg.get("memory", {})

    configurada = str(mem.get("path", "") or "").strip()
    if configurada:
        p = Path(configurada).expanduser()
        if not p.is_absolute():
            p = _config_dir(cfg) / p
        return p if p.is_file() else None

    for candidata in (
        Path.home() / ".config" / "maripepis" / DEFAULT_FILENAME,
        _config_dir(cfg) / DEFAULT_FILENAME,
    ):
        if candidata.is_file():
            return candidata
    return None


def load_memory(cfg: dict, logger=None) -> str:  # noqa: ANN001
    """Devuelve el bloque de memoria listo para añadir al system prompt.

    Devuelve ``""`` cuando está desactivada, no hay fichero, está vacío o no se
    puede leer: la memoria es un extra, nunca un motivo para no arrancar.
    """
    mem = cfg.get("memory", {})
    if not mem.get("enabled", True):
        return ""

    ruta = memory_path(cfg)
    if ruta is None:
        if str(mem.get("path", "") or "").strip() and logger:
            logger.warning("No encuentro el fichero de memoria %r; sigo sin ella.", mem["path"])
        return ""

    try:
        texto = _COMENTARIO.sub("", ruta.read_text(encoding="utf-8")).strip()
    except OSError as e:
        if logger:
            logger.warning("No puedo leer la memoria (%s); sigo sin ella.", e)
        return ""

    if not texto:
        return ""

    tope = int(mem.get("max_chars", DEFAULT_MAX_CHARS) or 0)
    if tope > 0 and len(texto) > tope:
        # Corta por el último salto de línea para no partir una frase por la mitad.
        recorte = texto[:tope]
        corte = recorte.rfind("\n")
        texto = recorte[:corte] if corte > 0 else recorte
        if logger:
            logger.warning(
                "La memoria (%s) supera max_chars=%d; uso solo el principio.", ruta, tope
            )

    if logger:
        logger.info("Memoria cargada de %s (%d caracteres).", ruta, len(texto))
    return _PREAMBULO + texto
