import os

from maripepis.utils.cuda import _nvidia_lib_dirs


def test_nvidia_lib_dirs_son_directorios_reales():
    # Con los wheels CUDA instalados devuelve rutas existentes; sin ellos, lista vacía.
    dirs = _nvidia_lib_dirs()
    assert isinstance(dirs, list)
    for d in dirs:
        assert os.path.isdir(d)
