"""Las carpetas del usuario, tal y como las nombra al hablar.

Cuando alguien dice «guárdalo en descargas», el modelo escribe `~/Downloads`:
es lo que ha visto un millón de veces. En un sistema en español no se llama
así — XDG las pone en `~/Descargas`, y el escritorio puede ser el propio home.
El fichero acaba existiendo, sí, pero en una carpeta vacía que nadie mira, que
para quien lo pidió es exactamente igual de inútil que no haberlo hecho.

Aquí se traduce, leyendo las carpetas de verdad de `~/.config/user-dirs.dirs`
(el estándar XDG, el mismo que usa el gestor de archivos). Con ellas se
resuelven tanto los nombres hablados («descargas», «el escritorio») como los
que se inventa el modelo (`~/Downloads`, `$HOME/Desktop`).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ..utils.phrases import normalize

log = logging.getLogger("maripepis.carpetas")

# Clave XDG → (como se dice hablando, como la escribe el modelo en inglés).
# El orden manda en la descripción de las herramientas, así que va de más usada
# a menos.
_XDG: tuple[tuple[str, str, str], ...] = (
    ("DESKTOP", "escritorio", "Desktop"),
    ("DOWNLOAD", "descargas", "Downloads"),
    ("DOCUMENTS", "documentos", "Documents"),
    ("PICTURES", "imágenes", "Pictures"),
    ("MUSIC", "música", "Music"),
    ("VIDEOS", "vídeos", "Videos"),
)

# Sinónimos hablados que no son el nombre de la carpeta.
_SINONIMOS: dict[str, str] = {
    "casa": "home",
    "mi carpeta": "home",
    "carpeta personal": "home",
    "el home": "home",
    "descarga": "descargas",
    "documento": "documentos",
    "fotos": "imágenes",
    "imagenes": "imágenes",
    "musica": "música",
    "videos": "vídeos",
    "peliculas": "vídeos",
}


def _user_dirs() -> dict[str, Path]:
    """Lee `~/.config/user-dirs.dirs`. Formato: ``XDG_X_DIR="$HOME/yyy"``."""
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    fichero = config / "user-dirs.dirs"
    dirs: dict[str, Path] = {}
    try:
        texto = fichero.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return dirs
    for linea in texto.splitlines():
        m = re.match(r'\s*XDG_(\w+)_DIR\s*=\s*"(.*)"\s*$', linea)
        if not m:
            continue
        clave, valor = m.group(1), m.group(2)
        valor = valor.replace("$HOME/", str(Path.home()) + "/").replace("$HOME", str(Path.home()))
        dirs[clave] = Path(valor.rstrip("/") or "/")
    return dirs


def carpetas() -> dict[str, Path]:
    """Las carpetas del usuario por su nombre hablado, `home` incluida.

    Sin `user-dirs.dirs` (o con una entrada que falte) se cae al nombre inglés
    de siempre, que es lo que hay en un sistema sin XDG configurado.
    """
    home = Path.home()
    dirs = _user_dirs()
    mapa: dict[str, Path] = {"home": home}
    for clave, hablado, ingles in _XDG:
        mapa[hablado] = dirs.get(clave) or (home / ingles)
    return mapa


def resolver(ruta: str) -> Path:
    """La carpeta que el usuario (o el modelo) quiere decir con `ruta`.

    Entiende una ruta normal, una con variables (`$HOME/x`, `~/x`) y un nombre
    hablado («descargas», «el escritorio», «fotos»). Lo literal manda: si
    `~/fotos` existe de verdad, es esa y no la de imágenes — quien tiene una
    carpeta con ese nombre la ha hecho a propósito. Lo relativo cuelga del
    *home*, no del `cwd`: como demonio, el directorio actual lo pone systemd y
    no significa nada para quien está hablando.
    """
    texto = (ruta or "").strip()
    if not texto:
        return Path.home()

    literal, _ = traducir_rutas(texto)  # `~/Downloads` no es su carpeta de descargas
    expandida = os.path.expandvars(literal)
    if "$" in expandida:  # una variable que no existe: `$HOME` bajo systemd, p.ej.
        expandida = re.sub(r"\$\{?HOME\}?", str(Path.home()), expandida)
    destino = Path(expandida).expanduser()
    if not destino.is_absolute():
        destino = Path.home() / destino
    if destino.is_dir():
        return destino

    mapa = carpetas()
    clave = normalize(texto)
    for prefijo in ("la ", "el ", "mi ", "en ", "carpeta ", "de "):  # «la carpeta descargas»
        while clave.startswith(prefijo):
            clave = clave[len(prefijo):]
    clave = normalize(_SINONIMOS.get(clave, clave))  # los sinónimos llevan tildes
    por_nombre = {normalize(k): k for k in mapa}
    if clave in por_nombre:
        return mapa[por_nombre[clave]]
    return destino


def resolver_ruta(ruta: str, carpeta: str = "") -> Path:
    """La ruta de un **fichero**, dicha como se dice hablando.

    Entiende «notas.txt» a secas (va al *home*), «descargas/notas.txt» —con el
    nombre hablado de la carpeta, en minúsculas y sin acentos, que es como lo
    escribe el modelo— y la ruta completa de toda la vida.
    """
    nombre = (ruta or "").strip()
    if not nombre:
        return Path.home()

    absoluta = Path(os.path.expandvars(nombre)).expanduser().is_absolute()
    # `carpeta="descargas"` + `ruta="descargas/manu.txt"` es lo que sale cuando el
    # modelo se cubre las espaldas repitiendo el sitio. Apilarlas dejaría el
    # fichero en Descargas/descargas: si la ruta ya dice la carpeta, manda ella.
    partes = list(Path(nombre.lstrip("./")).parts)
    ya_la_dice = len(partes) > 1 and _es_carpeta_conocida(partes[0])
    if carpeta.strip() and not absoluta and not ya_la_dice:
        base = resolver(carpeta)
        # Y lo mismo con una carpeta cualquiera: «carpeta=descargas/viaje» +
        # «ruta=viaje/notas.txt» no son dos «viaje», es el mismo dicho dos veces.
        while len(partes) > 1 and base.name and normalize(partes[0]) == normalize(base.name):
            partes.pop(0)
        return base.joinpath(*partes)

    nombre, _ = traducir_rutas(nombre)
    expandida = Path(os.path.expandvars(nombre)).expanduser()
    if expandida.is_absolute():
        return expandida

    # Relativa: si el primer tramo es una carpeta suya («descargas/x.txt»),
    # cuelga de ella; si no, del home.
    partes = expandida.parts
    if len(partes) > 1:
        base = resolver(partes[0])
        if base != Path.home() or normalize(partes[0]) in {"home", "casa"}:
            return base.joinpath(*partes[1:])
    return Path.home() / expandida


def _es_carpeta_conocida(tramo: str) -> bool:
    """¿«descargas», «Documentos», «Desktop»... es el nombre de una carpeta suya?"""
    clave = normalize(tramo)
    if not clave:
        return False
    for _c, hablado, ingles in _XDG:
        if clave in (normalize(hablado), normalize(ingles)):
            return True
    return False


def _prefijos_home() -> str:
    """Las formas de escribir el home en un comando, para las expresiones."""
    return r"(?:~|\$HOME|\$\{HOME\})"


def _variantes(hablado: str, ingles: str, real: Path) -> set[str]:
    """Los nombres con los que el modelo se refiere a esta carpeta y no son el suyo.

    `Downloads` porque lo ha visto mil veces, `documentos` en minúscula porque
    así lo ha dicho el usuario, `imagenes` sin tilde porque es más cómodo. En un
    disco que distingue mayúsculas, cada uno de esos es una carpeta nueva y
    vacía al lado de la de verdad.
    """
    sin_tilde = normalize(hablado)
    nombres = {
        ingles, ingles.lower(), hablado, hablado.capitalize(),
        sin_tilde, sin_tilde.capitalize(),
    }
    return {n for n in nombres if n and n != real.name}


def traducir_rutas(comando: str) -> tuple[str, list[str]]:
    """Cambia `~/Downloads` (o `documentos/`) por la carpeta de verdad, si procede.

    Solo toca lo que el modelo se ha inventado: un nombre que **no es** el de la
    carpeta real y que además está vacío o no existe. Si el usuario usa de
    verdad su `~/Downloads` (tiene algo dentro), no se toca nada: cambiarle la
    ruta a un comando suyo sería peor que el problema que arregla.

    Devuelve el comando y la lista de cambios hechos, para el log.
    """
    home = Path.home()
    mapa = carpetas()
    cambios: list[str] = []
    for _clave, hablado, ingles in _XDG:
        real = mapa[hablado]
        for variante in sorted(_variantes(hablado, ingles, real)):
            falsa = home / variante
            if falsa == real or _tiene_contenido(falsa):
                continue
            # Con el home delante (`~/Downloads`, `$HOME/Downloads`,
            # `/home/manu/Downloads`) o relativa a secas (`documentos/recibos`,
            # que se ejecuta desde el home). Siempre el nombre entero de la
            # carpeta: `~/Downloads2` no es esto.
            patron = re.compile(
                rf"(?:(?:{_prefijos_home()}|{re.escape(str(home))})/|(?<![\w/.$~-]))"
                rf"{re.escape(variante)}(?=/)"
            )
            # La «/» final no se sustituye: la deja fuera el lookahead.
            nuevo, n = patron.subn(_escapar_reemplazo(str(real)), comando)
            if n:
                cambios.append(f"{variante}/ → {real}/")
                comando = nuevo
    return comando, cambios


def _escapar_reemplazo(texto: str) -> str:
    """`re.sub` interpreta las contrabarras del reemplazo; aquí son literales."""
    return texto.replace("\\", "\\\\")


def _tiene_contenido(ruta: Path) -> bool:
    try:
        return any(ruta.iterdir())
    except OSError:
        return False


def descripcion() -> str:
    """Las carpetas de este equipo, para meterlas en la descripción de la herramienta."""
    mapa = carpetas()
    home = Path.home()
    partes = [f"la personal es {home}"]
    aparte = ""
    for _clave, hablado, _ingles in _XDG:
        ruta = mapa[hablado]
        if ruta == home:
            # Sin carpeta propia (XDG_DESKTOP_DIR="$HOME/"). Decir que «es la
            # misma que la personal» lo despista y acaba dejándolo en Documentos:
            # mejor una frase suelta que no admita interpretación.
            aparte += (
                f" Este equipo no tiene carpeta de «{hablado}» aparte: lo que se pida"
                f" «en el {hablado}» va directo a {home}."
            )
            continue
        partes.append(f"«{hablado}» es {ruta}")
    return (
        "Las carpetas de este usuario NO están en inglés: " + ", ".join(partes) + ". "
        "Usa esas rutas tal cual; no escribas ~/Downloads, ~/Desktop ni ~/Documents, "
        "que aquí no son las suyas." + aparte
    )
