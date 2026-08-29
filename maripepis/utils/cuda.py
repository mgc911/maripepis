"""Prepara las librerías CUDA (cuBLAS/cuDNN de los wheels de pip) para CTranslate2.

Los wheels `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` instalan las `.so` dentro
del venv, pero el cargador dinámico no las busca ahí. Como en Linux cambiar
`LD_LIBRARY_PATH` a mitad de proceso no afecta a `dlopen`, reejecutamos el
proceso una vez con la variable ya puesta. Solo actúa si los wheels existen.
"""

from __future__ import annotations

import importlib.util
import os
import sys

_GUARD = "MARIPEPIS_CUDA_REEXEC"


def _nvidia_lib_dirs() -> list[str]:
    dirs: list[str] = []
    for pkg in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            spec = importlib.util.find_spec(pkg)
        except (ImportError, ValueError):
            spec = None
        if spec and spec.submodule_search_locations:
            for base in spec.submodule_search_locations:
                lib = os.path.join(base, "lib")
                if os.path.isdir(lib):
                    dirs.append(lib)
    return dirs


def ensure_cuda_libs() -> None:
    """Si están los wheels CUDA del venv, los mete en LD_LIBRARY_PATH y reejecuta.

    Idempotente y seguro: no hace nada si no hay wheels o si ya están en el path.
    """
    if os.environ.get(_GUARD):
        return
    dirs = _nvidia_lib_dirs()
    if not dirs:
        return

    parts = [p for p in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep) if p]
    missing = [d for d in dirs if d not in parts]
    if not missing:
        return

    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(missing + parts)
    os.environ[_GUARD] = "1"
    # sys.orig_argv reproduce el arranque exacto (`-m maripepis`, script o `-c`).
    os.execv(sys.orig_argv[0], sys.orig_argv)
