"""Las luces desde la terminal: vincularse, ver cuáles hay y encenderlas.

Las dos primeras son para ti. Vincular pide estar de pie delante del puente con
el dedo en el botón —esa es justo la seguridad del invento, que la llave solo se
da a quien está físicamente en la casa—, y listar las luces es para saber cómo se
llaman de verdad antes de ponerte a pedirlas en voz alta.

`luz` y `estado` son otra cosa: son para los proveedores que traen sus propias
herramientas y no aceptan las nuestras (`claude-code`), que es como está montado
esto por defecto. A esos no les llega `controlar_luces`, así que se les da esta
orden y la ejecutan con su Bash. Por dentro llaman a la misma función que la
herramienta y devuelven su mismo texto, palabra por palabra: el modelo lee lo
mismo por los dos caminos, incluida la lista de sitios cuando se inventa uno.
"""

from __future__ import annotations

import sys
import time

from .hue import (
    CREDENCIALES,
    SinPuente,
    SinVincular,
    conectar,
    descubrir,
    guardar_credenciales,
    leer_credenciales,
    pedir_llave,
)

#: Cuánto se espera al botón. Cuarenta segundos son de sobra para ir andando
#: hasta el puente desde cualquier punto de una casa, que es la unidad de medida
#: que importa aquí.
ESPERA_BOTON = 40
INTERVALO = 2


def vincular(ip: str = "") -> int:
    """Consigue la llave del puente y la guarda. Devuelve el código de salida."""
    ip = ip or leer_credenciales()[0] or descubrir()
    if not ip:
        print("No encuentro ningún puente Hue en la red.", file=sys.stderr)
        print("Si sabes su IP, dímela:  maripepis-hue vincular 192.168.1.33",
              file=sys.stderr)
        return 1

    print(f"Puente encontrado en {ip}.")
    print("Pulsa ahora el botón redondo del puente. Te espero...")

    for restante in range(ESPERA_BOTON, 0, -INTERVALO):
        try:
            if llave := pedir_llave(ip):
                guardar_credenciales(ip, llave)
                print(f"\nVinculado. La llave queda en {CREDENCIALES}")
                print("Pruébalo:  maripepis-hue luces")
                return 0
        except SinPuente as e:
            print(f"\nNo ha salido bien: {e}", file=sys.stderr)
            return 1
        print(f"  esperando el botón... {restante}s ", end="\r", flush=True)
        time.sleep(INTERVALO)

    print("\nNo has pulsado el botón a tiempo. Vuelve a lanzarlo cuando quieras.",
          file=sys.stderr)
    return 1


def luces() -> int:
    """Enseña las habitaciones y las bombillas, con el nombre que tienen en Hue."""
    try:
        with conectar() as puente:
            grupos, bombillas = puente.grupos(), puente.bombillas()
    except SinVincular as e:
        print(f"{e}.\nEjecuta:  maripepis-hue vincular", file=sys.stderr)
        return 1
    except SinPuente as e:
        print(f"{e}.", file=sys.stderr)
        return 1

    for titulo, cosas in (("Habitaciones y zonas", grupos), ("Bombillas", bombillas)):
        print(f"\n{titulo}:")
        if not cosas:
            print("  (ninguna)")
        for luz in cosas:
            estado = f"encendida al {luz.brillo}%" if luz.encendida else "apagada"
            print(f"  {luz.nombre:<28} {estado}")
    print("\nEstos son los nombres que entiende hablando.")
    return 0


#: Las banderas de `luz`, y también sin guiones: un modelo que escribe la orden
#: a mano se los come la mitad de las veces, y negárselo por eso sería dejar la
#: luz encendida por un detalle de sintaxis.
_ACCIONES = {"apagar", "encender", "alternar"}
_CON_VALOR = {"brillo", "color", "escena"}


def luz(args: list[str]) -> int:
    """Cambia unas luces y escribe lo que ha pasado. Mismo texto que la herramienta.

    Se escribe en stdout y se sale con 1 si no se ha hecho, para que la orden se
    porte como cualquier otra en una shell. Pero lo que de verdad lee el modelo es
    el texto: ahí va el motivo, y la lista de sitios cuando ha dicho uno que no
    existe.
    """
    from ..tools.base import es_fallo
    from ..tools.hogar import controlar_luces

    if not args:
        print("NO he tocado las luces: no me has dicho cuáles.", file=sys.stderr)
        return 2

    peticion: dict = {"sitio": args[0]}
    resto = args[1:]
    i = 0
    while i < len(resto):
        clave = resto[i].lstrip("-")
        if clave in _ACCIONES:
            peticion["accion"] = clave
        elif clave in _CON_VALOR and i + 1 < len(resto):
            peticion[clave] = resto[i + 1]
            i += 1
        i += 1

    resultado = controlar_luces(peticion)
    print(resultado)
    return 1 if es_fallo(resultado) else 0


def estado(sitio: str = "") -> int:
    """Qué luces están encendidas, en la frase que se diría en voz alta."""
    from ..tools.base import es_fallo
    from ..tools.hogar import estado_de_las_luces

    resultado = estado_de_las_luces({"sitio": sitio} if sitio else {})
    print(resultado)
    return 1 if es_fallo(resultado) else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    orden = args[0] if args else ""

    if orden == "vincular":
        return vincular(args[1] if len(args) > 1 else "")
    if orden == "luces":
        return luces()
    if orden == "luz":
        return luz(args[1:])
    if orden == "estado":
        return estado(args[1] if len(args) > 1 else "")

    print(__doc__.strip().splitlines()[0])
    print("\nUso:")
    print("  maripepis-hue vincular [IP]   consigue la llave (hay que pulsar el botón)")
    print("  maripepis-hue luces           enseña las luces y cómo se llaman")
    print("  maripepis-hue luz SITIO ...   apagar | encender | alternar |")
    print("                                --brillo 0-100 | --color rojo | --escena relax")
    print("  maripepis-hue estado [SITIO]  qué hay encendido")
    return 0 if orden in ("", "-h", "--help", "ayuda") else 2


if __name__ == "__main__":
    raise SystemExit(main())
