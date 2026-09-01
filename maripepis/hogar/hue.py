"""El puente Hue: encontrarlo, vincularse una vez y hablarle en local.

Esto empezó siendo «conéctate a mi cuenta de Google y controla el Google Home».
No se puede, y conviene dejarlo escrito para que nadie lo intente otra vez: el
SDK de Google Assistant —lo único que dejaba mandar «apaga la luz» por código—
lo cerró Google en 2023; la API de Smart Device Management sigue viva pero solo
llega a los Nest de la propia Google (termostato, cámaras, timbre) y va con
registro de pago; las Home APIs nuevas son SDKs de Android y de iOS con
verificación de marca. Ninguna de las tres sirve para un script en Linux.

Así que se le habla a la bombilla, no a la nube. Y sale ganando: la API local del
puente no pasa por internet, no caduca un token a media noche y contesta en
milisegundos, que es lo que hace falta cuando alguien acaba de decir «apaga» en
voz alta y está esperando a que se apague.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("maripepis.hogar")

#: Dónde se guarda la llave del puente. Fuera del proyecto a propósito, igual que
#: la agenda de WhatsApp: es una credencial y `config.toml` está en git.
CREDENCIALES = Path("~/.config/maripepis/hue.toml").expanduser()

#: Corto porque es la red de casa. Si el puente no contesta en tres segundos es
#: que no está, y más vale decirlo que dejar a alguien mirando la lámpara.
TIMEOUT = 3.0

#: Con qué nombre aparece Maripepis en la lista de aplicaciones del puente (la de
#: la app de Hue, donde se le quita el acceso a algo si un día molesta).
APLICACION = "maripepis"


class SinPuente(Exception):
    """No hay puente al que hablarle: ni configurado, ni encontrado en la red."""


class SinVincular(Exception):
    """Hay puente, pero todavía no nos ha dado llave. Hay que pulsar el botón."""


# --- Encontrar el puente --------------------------------------------------


def _por_mdns() -> str:
    """La IP del puente según el propio puente, que se anuncia en la red.

    Es la vía buena: no depende de internet ni de que Philips tenga el día bueno.
    Solo hace falta que avahi esté escuchando, que en cualquier escritorio actual
    lo está.
    """
    try:
        salida = subprocess.run(
            ["avahi-browse", "-rtp", "_hue._tcp"],
            capture_output=True, text=True, timeout=6, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        log.info("No pude preguntar por mDNS: %s", e)
        return ""

    for linea in salida.splitlines():
        campos = linea.split(";")
        # '=' es la línea ya resuelta; la IPv4 es el campo 8. La IPv6 del puente
        # también se anuncia, pero la API v2 solo escucha limpio por IPv4.
        if campos[0] == "=" and len(campos) > 7 and campos[2] == "IPv4":
            return campos[7]
    return ""


def _por_internet() -> str:
    """El servicio de descubrimiento de Philips: qué puentes hay en esta IP pública.

    Solo como último recurso. Sale por internet para averiguar algo que está en la
    habitación de al lado, y contesta con la IP local, así que si el mDNS no ha
    ido lo normal es que esto tampoco haga falta.
    """
    try:
        r = httpx.get("https://discovery.meethue.com", timeout=TIMEOUT * 2)
        if r.status_code == 200 and (datos := r.json()):
            return str(datos[0].get("internalipaddress") or "")
    except (httpx.HTTPError, ValueError, IndexError, KeyError) as e:
        log.info("El descubrimiento de Philips no contestó: %s", e)
    return ""


def descubrir() -> str:
    """La IP del puente, buscándola primero en la red de casa. '' si no aparece."""
    return _por_mdns() or _por_internet()


def _responde(ip: str) -> bool:
    """¿Hay un puente Hue de verdad en esa IP? Se pregunta sin llave: es público."""
    if not ip:
        return False
    try:
        r = httpx.get(f"https://{ip}/api/config", timeout=TIMEOUT, verify=False)
        return r.status_code == 200 and "bridgeid" in r.json()
    except (httpx.HTTPError, ValueError):
        return False


# --- La llave -------------------------------------------------------------


def leer_credenciales() -> tuple[str, str]:
    """`(ip, llave)` de lo guardado, o `("", "")` si todavía no hay nada."""
    try:
        datos = tomllib.loads(CREDENCIALES.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "", ""
    return str(datos.get("puente") or ""), str(datos.get("llave") or "")


def guardar_credenciales(ip: str, llave: str) -> None:
    """Guarda la llave en un fichero que solo pueda leer su dueño.

    El 0600 no es paranoia de manual: con esa cadena, cualquiera que la lea puede
    encender y apagar las luces de esta casa desde la red local.
    """
    CREDENCIALES.parent.mkdir(parents=True, exist_ok=True)
    CREDENCIALES.write_text(
        "# Llave del puente Hue de Maripepis. La crea `maripepis-hue vincular`.\n"
        f'puente = "{ip}"\n'
        f'llave = "{llave}"\n',
        "utf-8",
    )
    CREDENCIALES.chmod(0o600)


def pedir_llave(ip: str) -> str:
    """Pide una llave nueva al puente. Devuelve '' si el botón no está pulsado.

    Va por la API v1, que es la que sigue emitiendo llaves: la v2 las usa pero no
    las reparte. Los puentes actuales aceptan las dos, así que no es deuda: es
    dónde vive esta operación concreta.
    """
    cuerpo = {"devicetype": f"{APLICACION}#{socket.gethostname()}", "generateclientkey": True}
    try:
        r = httpx.post(f"https://{ip}/api", json=cuerpo, timeout=TIMEOUT, verify=False)
        respuesta = r.json()[0]
    except (httpx.HTTPError, ValueError, IndexError) as e:
        raise SinPuente(f"el puente {ip} no contesta ({e})") from e

    if llave := respuesta.get("success", {}).get("username"):
        return str(llave)
    # 101 es «no has pulsado el botón», que no es un error: es que falta el paso
    # de estar delante del puente. Cualquier otro sí lo es.
    error = respuesta.get("error", {})
    if error.get("type") == 101:
        return ""
    raise SinPuente(f"el puente respondió: {error.get('description') or respuesta}")


# --- Hablar con el puente -------------------------------------------------


@dataclass(frozen=True)
class Luz:
    """Una luz o un grupo, con lo justo para hablar de ella en voz alta."""

    id: str
    nombre: str
    encendida: bool
    brillo: int          # 0-100; 0 también cuando la luz no sabe atenuarse
    grupo: bool = False  # un grupo (habitación o zona), no una bombilla suelta

    @property
    def recurso(self) -> str:
        return "grouped_light" if self.grupo else "light"


class Puente:
    """El puente Hue, ya vinculado. Todo lo de aquí va por la red local."""

    def __init__(self, ip: str, llave: str) -> None:
        self.ip = ip
        self.llave = llave
        # verify=False y no es descuido: el puente presenta un certificado
        # firmado por la CA de Philips, con el bridgeid de nombre en vez de la IP,
        # así que ninguna verificación estándar lo da por bueno. Validarlo de
        # verdad pide traerse esa CA y comparar el bridgeid a mano; contra una IP
        # de la red local, y para encender una bombilla, no compensa.
        self._http = httpx.Client(
            base_url=f"https://{ip}/clip/v2",
            headers={"hue-application-key": llave},
            timeout=TIMEOUT,
            verify=False,
        )

    def cerrar(self) -> None:
        self._http.close()

    def __enter__(self) -> Puente:
        return self

    def __exit__(self, *_) -> None:  # noqa: ANN002
        self.cerrar()

    def _get(self, recurso: str) -> list[dict]:
        r = self._http.get(f"/resource/{recurso}")
        if r.status_code in (401, 403):
            raise SinVincular(f"el puente no acepta la llave guardada ({r.status_code})")
        r.raise_for_status()
        return list(r.json().get("data") or [])

    def _put(self, recurso: str, id_: str, cuerpo: dict) -> None:
        r = self._http.put(f"/resource/{recurso}/{id_}", json=cuerpo)
        if r.status_code in (401, 403):
            raise SinVincular(f"el puente no acepta la llave guardada ({r.status_code})")
        r.raise_for_status()
        # El puente contesta 200 con los errores dentro del cuerpo, así que un
        # status bueno no basta para decir que se ha hecho.
        if errores := (r.json().get("errors") or []):
            raise httpx.HTTPError(str(errores[0].get("description") or errores[0]))

    def bombillas(self) -> list[Luz]:
        """Las luces sueltas, con su nombre de la app de Hue."""
        return [
            Luz(
                id=str(d["id"]),
                nombre=str(d.get("metadata", {}).get("name") or "sin nombre"),
                encendida=bool(d.get("on", {}).get("on")),
                brillo=round(float(d.get("dimming", {}).get("brightness") or 0)),
            )
            for d in self._get("light")
        ]

    def grupos(self) -> list[Luz]:
        """Habitaciones y zonas: «el salón», «la planta de arriba».

        Es lo que se dice hablando. Nadie pide «enciende Hue color lamp 1»: pide
        el salón, y el salón es un grupo cuyo estado hay que ir a buscar a otro
        recurso, porque la habitación en sí no sabe si está encendida.
        """
        estado = {
            str(g["id"]): g
            for g in self._get("grouped_light")
        }
        salida: list[Luz] = []
        for tipo in ("room", "zone"):
            for d in self._get(tipo):
                servicio = next(
                    (s for s in d.get("services", []) if s.get("rtype") == "grouped_light"),
                    None,
                )
                if not servicio:
                    continue
                g = estado.get(str(servicio["rid"]), {})
                salida.append(Luz(
                    id=str(servicio["rid"]),
                    nombre=str(d.get("metadata", {}).get("name") or "sin nombre"),
                    encendida=bool(g.get("on", {}).get("on")),
                    brillo=round(float(g.get("dimming", {}).get("brightness") or 0)),
                    grupo=True,
                ))
        return salida

    def escenas(self) -> list[tuple[str, str, str]]:
        """`(id, nombre, id del grupo)` de cada escena guardada en el puente."""
        return [
            (str(d["id"]),
             str(d.get("metadata", {}).get("name") or "sin nombre"),
             str(d.get("group", {}).get("rid") or ""))
            for d in self._get("scene")
        ]

    def aplicar(self, luz: Luz, cambios: dict) -> None:
        """Le manda los cambios a una luz o a un grupo. Levanta si no se aplican."""
        self._put(luz.recurso, luz.id, cambios)

    def activar_escena(self, id_escena: str) -> None:
        self._put("scene", id_escena, {"recall": {"action": "active"}})


def conectar(ip_configurada: str = "") -> Puente:
    """El puente listo para usar, o una excepción que explique qué falta.

    El orden es el que menos molesta: lo que diga la configuración, luego lo
    guardado al vincular, y solo si eso no responde se sale a buscar por la red.
    Un puente que cambia de IP es lo normal en casa (un router que se reinicia y
    reparte otra cosa), así que redescubrirlo solo evita una llamada al técnico.
    """
    guardada, llave = leer_credenciales()
    if not llave:
        raise SinVincular(
            "todavía no hay llave del puente Hue: hay que pulsar su botón una vez"
        )

    for ip in (ip_configurada, guardada):
        if ip and _responde(ip):
            return Puente(ip, llave)

    if (nueva := descubrir()) and _responde(nueva):
        # Se apunta la IP nueva: la llave sigue valiendo, es del puente, no de su
        # dirección. Así el próximo arranque no tiene que volver a buscar.
        guardar_credenciales(nueva, llave)
        log.info("El puente Hue ha cambiado de IP: ahora es %s", nueva)
        return Puente(nueva, llave)

    raise SinPuente("no encuentro el puente Hue en la red")
