"""Mandarle un wasap a alguien. Nunca a la primera.

Hablando, «mándale un wasap a Marta» es una petición de lo más normal. Ejecutada,
es la única acción de todas las de Maripepis que **sale del equipo y le llega a
otra persona**, y la única que no se puede deshacer: un fichero mal escrito se
reescribe, una app abierta se cierra, un mensaje enviado ya no. Así que aquí
nunca se llega hasta el final de un tirón: entre lo que se entendió y lo que sale
hay siempre un momento en que el usuario puede decir que no.

Ese momento tiene dos formas, y son los dos modos de `[tools.whatsapp] modo`:

- **borrador** — se abre el chat en ZapZap con el mensaje escrito en el cuadro de
  texto, y el Enter lo da quien está delante, viendo a quién va y qué pone. Es lo
  que hacía esto desde el principio, y de ahí sale todo lo que se explica abajo.
- **envio** — sale por la sesión propia (`maripepis/whatsapp/`), donde no hay
  pantalla ni Enter que valgan, así que el freno se muda a la conversación:
  `preparar_envio` deja el mensaje esperando y contesta con lo que hay que leerle
  al usuario, y `enviar_mensaje` —sin destinatario, sin texto y desde otro turno—
  solo suelta lo que él acaba de oír y aprobar. Y detrás va `borrar_mensaje`,
  el «no, espera»: tira lo que aún no ha salido, o retira lo que sí.

Por la sesión propia se puede escribir además a **grupos**, que un enlace
`whatsapp://` nunca pudo abrir porque no tienen teléfono, solo un identificador
que se ve desde dentro de la sesión. Se apuntan a mano en `[grupos]` y se nombran
como lo que son —«va al grupo Familia»— en todo lo que se lee en voz alta: sin
ver la pantalla, es la única forma de notar que el mensaje lo van a leer doce.

Eso no es una limitación que haya que rodear (en borrador se podría: un Enter
sintético con `hyprctl` sobre la ventana, o el puerto de depuración del
WebEngine). Es lo que convierte un fallo del micrófono —o del modelo— en un
mensaje que no llegas a enviar, en vez de en un mensaje que ya no puedes retirar.
Quien pide esto por voz no está viendo la pantalla mientras habla; ese momento es
el único en el que se entera de a quién le va a llegar qué.

Lo de aquí abajo es del modo borrador. Por dentro es la puerta oficial de ZapZap,
no un apaño: su `.desktop` registra `x-scheme-handler/whatsapp`, y al arrancarlo
con un enlace `whatsapp://` estando ya abierto, su `SingleApplication` se lo pasa
por un socket a la instancia viva,
que lo abre dentro de WhatsApp Web (`MainWindow.xdgOpenChat`). No hay que
automatizar ninguna ventana ni pelearse con el DOM de WhatsApp.

Dos cosas que no son evidentes y que condicionan todo lo de abajo:

- **Con ZapZap cerrado, el enlace se pierde.** Su `SingleApplication` solo mira
  los argumentos en la rama de «ya hay otra instancia»; arrancando de cero los
  ignora sin decir nada. Por eso aquí se comprueba antes, y si no está abierto se
  dice, en vez de dar por hecho un mensaje que no ha llegado a escribirse.
- **La agenda tiene que ser nuestra.** La libreta de WhatsApp vive dentro de la
  sesión del navegador empotrado y desde fuera no se lee. Un enlace necesita el
  número con prefijo, así que los nombres («mi hermana») salen de un fichero
  propio, `~/.config/maripepis/contactos.toml`. Esto vale para los dos modos: la
  sesión propia tampoco te da la agenda.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import socket
import time
import tomllib
import urllib.parse
from pathlib import Path

from ..config import load_config
from ..utils.phrases import normalize
from ..utils.turnos import turno_actual
from ..whatsapp.cliente import enviar as _enviar_por_la_sesion
from ..whatsapp.cliente import revocar as _revocar_por_la_sesion
from ..whatsapp.protocol import es_grupo, partes_destino
from .base import MARCA_MODELO, Tool, es_fallo
from .lanzador import lanzar

log = logging.getLogger("maripepis.whatsapp")

#: El cliente de escritorio. Se puede cambiar en `[tools.whatsapp] cliente`, pero
#: tiene que entender un `whatsapp://` en la línea de órdenes.
CLIENTE = "zapzap"

#: El `QLocalServer` de ZapZap (su `__appid__`): el socket por el que la
#: instancia abierta recibe los enlaces.
SOCKET = "zapzap-application"

#: Dónde está la agenda si no se dice otra cosa. Fuera del proyecto a propósito:
#: son los teléfonos de tu familia, y `config.toml` va en git.
AGENDA = "~/.config/maripepis/contactos.toml"

#: Prefijo de país para los números de nueve cifras.
PREFIJO = "34"

#: Tope del mensaje. No es el de WhatsApp (que es enorme): es que un mensaje
#: dictado no ocupa esto, y si el modelo escribe una parrafada es que ha
#: entendido otra cosa.
MAX_TEXTO = 1000

#: Cuántos contactos se le nombran al modelo en la descripción de la herramienta.
MAX_EN_DESCRIPCION = 40

#: Los dos modos, y son dos formas distintas de parar antes del final:
#:
#: - ``borrador`` deja el mensaje escrito en ZapZap y el Enter lo das tú. Es lo
#:   que hacía esto desde el principio, y lo de arriba explica por qué.
#: - ``envio`` lo manda por la sesión propia del demonio (`maripepis/whatsapp/`).
#:   Ya no hay Enter ni pantalla, así que en su lugar hay dos herramientas y dos
#:   turnos: una redacta el mensaje y te lo lee, y la otra —cuando dices que sí—
#:   lo suelta.
#:
#: Por defecto, borrador. El modo seguro tiene que ser el que se elige solo.
MODOS = ("borrador", "envio")
MODO = "borrador"

#: Lo que dura un mensaje preparado esperando un «sí». Corto a propósito: un
#: pendiente viejo es un mensaje que ya no sabes si es el que te leyeron, y ante
#: eso vale más pedirlo otra vez —cuesta una frase— que mandar el de antes.
CADUCA = 60.0

#: Dónde se guarda ese mensaje mientras espera. En el directorio de tiempo de
#: ejecución (tmpfs, 0700, se va con la sesión) y no en `~`: es lo que el usuario
#: acaba de dictar, y no tiene por qué sobrevivir al reinicio.
#:
#: En un fichero y no en una variable porque los dos caminos que llevan aquí no
#: comparten proceso: nuestras herramientas viven dentro del demonio de maripepis,
#: pero Claude Code ejecuta la orden con su `Bash`, y ahí cada paso es un proceso
#: nuevo. Con el pendiente en memoria, la confirmación por shell no existiría.
PENDIENTE = "maripepis-whatsapp-pendiente.json"

# Lo que se cuela delante de un nombre al hablar: «mándale un wasap **a** Marta»,
# «escríbele **a mi** hermana».
_MULETILLAS = ("a ", "al ", "para ", "mi ", "mis ", "el ", "la ", "los ", "las ")


def _clave(nombre: str) -> str:
    """El nombre de un contacto reducido a lo que se puede comparar."""
    clave = normalize(nombre)
    cambiado = True
    while cambiado:
        cambiado = False
        for muletilla in _MULETILLAS:
            if clave.startswith(muletilla):
                clave, cambiado = clave[len(muletilla):], True
    return clave


def numero(texto: str, prefijo: str = PREFIJO) -> str:
    """El teléfono tal y como lo quiere un enlace de WhatsApp: dígitos, con país.

    Nueve cifras es el número de aquí de toda la vida y se le pone el prefijo de
    `[tools.whatsapp] prefijo`. Lo que empieza por `+` o por `00` ya lo trae, y
    entonces manda lo escrito: quien apunta un número internacional sabe lo que
    hace. Lo que ya pasa de nueve cifras sin `+` se toma como que también lo trae.

    Devuelve ``""`` si eso no es un teléfono, y eso corta la acción: es preferible
    decir que no se entiende el número a abrirle un chat a un desconocido.
    """
    crudo = " ".join((texto or "").split())
    digitos = re.sub(r"\D", "", crudo)
    if crudo.startswith("+"):
        pass                                  # ya trae país
    elif digitos.startswith("00"):
        digitos = digitos[2:]                 # el otro modo de escribir el «+»
    elif len(digitos) == 9:
        digitos = re.sub(r"\D", "", prefijo) + digitos
    if not 8 <= len(digitos) <= 15:           # los topes de E.164
        return ""
    return digitos


def modo_de(cfg: dict | None = None) -> str:
    """El modo de `[tools.whatsapp] modo`, y ante la duda el de no enviar.

    Un `modo` mal escrito no puede acabar nunca en «envía de verdad»: de los dos
    errores posibles, dejar escrito un mensaje que había que enviar se arregla
    dándole a Enter, y enviar uno que no había que enviar, no.
    """
    modo = str((cfg or {}).get("modo") or MODO).strip().lower()
    if modo not in MODOS:
        log.warning("«%s» no es un modo de WhatsApp (%s); me quedo en «%s».",
                    modo, " o ".join(MODOS), MODO)
        return MODO
    return modo


def fichero_agenda(cfg: dict | None = None) -> Path:
    """Dónde está el fichero de contactos."""
    ruta = str((cfg or {}).get("agenda") or "").strip() or AGENDA
    return Path(ruta).expanduser()


def _leer_agenda(cfg: dict | None = None) -> dict:
    """El TOML de la agenda, o ``{}`` si no hay o no se entiende.

    Un fichero roto no tumba el turno y tampoco se adivina: se avisa por el log y
    se sigue como si estuviera vacío, que es lo que acaba contando `_sin_agenda`
    con todas las letras.
    """
    ruta = fichero_agenda(cfg)
    try:
        with open(ruta, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        log.warning("No he podido leer la agenda %s: %s", ruta, e)
        return {}


def grupos(cfg: dict | None = None) -> dict[str, str]:
    """Los grupos apuntados: nombre hablado → identificador de WhatsApp.

    Sección `[grupos]` del mismo fichero, y aparte de los contactos porque lo que
    llevan no son teléfonos: un grupo no tiene número, tiene un identificador
    (`120363...@g.us`) que **solo se ve desde dentro de la sesión**. De ahí que
    haya que apuntarlos a mano, y de ahí `maripepis-wa grupos <parte del nombre>`,
    que es lo que te los enseña para copiarlos.

    Y a mano de verdad, tres o cuatro: esta cuenta está en 269 grupos y la lista
    de nombres viaja al modelo en cada frase. Lo que se apunta es a lo que se
    escribe, no todo a lo que se pertenece.
    """
    seccion = _leer_agenda(cfg).get("grupos")
    if not isinstance(seccion, dict):
        return {}

    apuntados: dict[str, str] = {}
    for nombre, valor in seccion.items():
        if not isinstance(valor, str):
            continue
        jid = " ".join(valor.split())
        if es_grupo(jid):
            apuntados[str(nombre)] = jid
        else:
            log.warning("El grupo «%s» no tiene un identificador que entienda (%r); "
                        "tiene que acabar en @g.us. Lo salto.", nombre, valor)
    return apuntados


def buscar_en_la_libreta(quien: str, cfg: dict | None = None) -> list[tuple[str, str]]:
    """Todo lo que encaja con lo que se ha dicho: personas y grupos.

    Se buscan en los dos sitios porque al hablar no se distinguen —«mándale un
    wasap a Marta» y «...al grupo de la familia» son la misma frase—, y se
    devuelven **juntos y sin elegir**. Eso es lo importante: si «familia» es a la
    vez una persona apuntada y un grupo, hay dos y quien llama pregunta. Juntarlos
    en un diccionario habría dejado uno solo, y el que se pierde silenciosamente
    es un mensaje que acaba en el sitio equivocado — y de un grupo lo leen doce.
    """
    return buscar(quien, contactos(cfg)) + buscar(quien, grupos(cfg))


def contactos(cfg: dict | None = None) -> dict[str, str]:
    """La agenda: nombre hablado → teléfono ya normalizado.

    El fichero es un TOML a mano, y se admite de las dos formas en que a uno se
    le ocurre escribirlo: con una sección `[contactos]` o con las líneas sueltas
    en la raíz. Varios nombres pueden apuntar al mismo número («marta» y «mi
    hermana»), que es justo lo que hace falta para que valga cualquiera de los
    dos al hablar.

    Se lee en cada llamada, no al arrancar: así apuntar un contacto nuevo no
    obliga a reiniciar el servicio. Lo que sí se queda con lo que hubiera al
    arrancar es la lista de nombres que ve el modelo (`descripcion`).
    """
    datos = _leer_agenda(cfg)
    seccion = datos.get("contactos")
    crudo = seccion if isinstance(seccion, dict) else datos
    prefijo = str((cfg or {}).get("prefijo") or PREFIJO)

    agenda: dict[str, str] = {}
    for nombre, valor in crudo.items():
        if not isinstance(valor, str):
            continue
        telefono = numero(valor, prefijo)
        if telefono:
            agenda[str(nombre)] = telefono
        else:
            log.warning("El contacto «%s» tiene un número que no entiendo (%r); lo salto.",
                        nombre, valor)
    return agenda


def buscar(quien: str, agenda: dict[str, str]) -> list[tuple[str, str]]:
    """Los contactos que encajan con lo que se ha dicho, ``[]`` si ninguno.

    Devuelve más de uno cuando de verdad hay duda (dos Martas apuntadas), y
    entonces quien llama **pregunta** en vez de elegir por su cuenta: mandarle el
    mensaje a la persona equivocada no se arregla luego.

    Se compara por palabras enteras, nunca por trozos: así «Ana» no encaja con
    «Juana», y «Marta» sí con «Marta García». Y un nombre exacto gana a cualquier
    parecido, que si no tener apuntadas «marta» y «marta garcía» convertiría
    «Marta» en una duda que no tiene.
    """
    clave = _clave(quien)
    if not clave:
        return []

    exactos = [(n, t) for n, t in agenda.items() if _clave(n) == clave]
    if not exactos:
        pedidas = set(clave.split())
        exactos = [
            (n, t) for n, t in agenda.items()
            if (suyas := set(_clave(n).split())) and (suyas <= pedidas or pedidas <= suyas)
        ]

    # Dos apuntes con el mismo teléfono («marta» y «mi hermana») son la misma
    # persona, no una duda.
    vistos: set[str] = set()
    unicos: list[tuple[str, str]] = []
    for nombre, telefono in exactos:
        if telefono not in vistos:
            vistos.add(telefono)
            unicos.append((nombre, telefono))
    return unicos


def zapzap_abierto() -> bool:
    """¿Hay una instancia de ZapZap escuchando enlaces?

    Se comprueba conectándose a su socket, que es exactamente lo que hace ZapZap
    consigo mismo. No vale mirar si el fichero existe —un cierre a lo bruto deja
    el socket huérfano— ni buscar el proceso, que puede estar todavía arrancando
    y sin nadie al otro lado.

    Conectarse tiene un efecto: ZapZap saca su ventana al frente en cuanto alguien
    llama a la puerta (`_onNewConnection` → `activateWindow`). Aquí no molesta,
    porque lo siguiente que se hace es abrir un chat en esa misma ventana.
    """
    for base in (os.environ.get("XDG_RUNTIME_DIR"), "/tmp"):
        if not base:
            continue
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(str(Path(base) / SOCKET))
            return True
        except OSError:
            continue
    return False


def enlace(telefono: str, texto: str) -> str:
    """El `whatsapp://` que abre ese chat con ese mensaje escrito.

    `safe=""` no es celo de más: ZapZap mete esta URL **tal cual** dentro de una
    cadena de JavaScript (`a.href="<url>"`, en `PageController.xdg_open_chat`),
    así que una comilla sin codificar en el texto no sería una comilla, sería
    código ejecutándose dentro de tu sesión de WhatsApp Web. Y el texto lo escribe
    un LLM a partir de lo que ha entendido un micrófono.
    """
    return (
        "whatsapp://send?phone=" + urllib.parse.quote(telefono, safe="")
        + "&text=" + urllib.parse.quote(texto, safe="")
    )


# --- El mensaje que espera un «sí» -----------------------------------------
#
# Esto es lo que sustituye al Enter cuando el modo es `envio`. La herramienta que
# redacta no manda nada: deja el mensaje aquí y contesta con lo que hay que leerle
# al usuario. Solo una segunda llamada, ya con el «sí» oído, lo suelta.
#
# Lo que guarda es el mensaje **ya resuelto** —el teléfono, no el nombre que se
# dijo—, y por eso la agenda puede cambiar entre los dos pasos sin que cambie a
# quién le llega: se manda lo que se leyó, no lo que se vuelva a buscar.


def fichero_pendiente() -> Path:
    """Dónde espera el mensaje preparado.

    En `$XDG_RUNTIME_DIR`, que es el sitio de las cosas que no sobreviven a la
    sesión: un directorio en tmpfs, con permisos 0700, que systemd borra al
    salir. La ruta la comparten el demonio de maripepis y cualquier orden que
    lance Claude Code, que es justo lo que hace falta para que los dos pasos se
    encuentren.
    """
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base) / PENDIENTE


def guardar_pendiente(nombre: str, destino: str, texto: str) -> Path:
    """Deja el mensaje esperando, apuntando de qué turno viene.

    El turno es lo que impide que el modelo se conteste a sí mismo: sin él, un
    7B que lee «léeselo y espera» encadena las dos llamadas en la misma vuelta y
    la confirmación se queda en un adorno. Con él, la segunda llamada tiene que
    llegar de un turno distinto, y entre dos turnos solo se pasa hablando.

    El fichero sale en 0600 desde el `open`, no con un `chmod` después: es el
    mensaje que acaba de dictar el usuario, y entre las dos cosas hay un hueco.
    """
    datos = {
        "nombre": nombre,
        "destino": destino,
        "texto": texto,
        "turno": turno_actual(),
        "creado": time.time(),
    }
    ruta = fichero_pendiente()
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)
    return ruta


def leer_pendiente() -> dict | None:
    """El mensaje que espera, o ``None`` si no hay ninguno que valga.

    Un fichero a medio escribir, de otra versión o con la basura de un reinicio
    es lo mismo que no tener nada: aquí no se adivina. Devolver ``None`` no manda
    nada, que es el lado del que hay que equivocarse.
    """
    try:
        with open(fichero_pendiente(), encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(datos, dict):
        return None
    if not all(isinstance(datos.get(c), str) and datos.get(c)
               for c in ("nombre", "destino", "texto", "turno")):
        return None
    if not isinstance(datos.get("creado"), (int, float)):
        return None
    return datos


def olvidar_pendiente() -> None:
    """Se acabó la espera: ni se manda ni se guarda."""
    try:
        fichero_pendiente().unlink()
    except OSError:
        pass


def caducado(pendiente: dict) -> bool:
    """¿Ha pasado ya el minuto?

    Una edad negativa también cuenta como caducada: significa que el reloj ha
    saltado hacia atrás entre los dos pasos, y de un pendiente con la fecha en el
    futuro no se puede decir cuándo se preparó.
    """
    edad = time.time() - float(pendiente.get("creado") or 0)
    return not 0 <= edad <= CADUCA


def _desencaja(args: dict, pendiente: dict) -> str:
    """Lo que el modelo ha metido en la confirmación y no era lo preparado.

    La herramienta de confirmar no lleva argumentos, pero un modelo pequeño se
    los inventa igual, y ahí hay dos casos muy distintos. Si repite lo que ya
    había, es ruido y se ignora. Si trae otra cosa —otro nombre, otro texto—, o
    se ha liado o el usuario acaba de cambiar el mensaje, y entonces mandar lo
    que hay guardado sería mandar una cosa mientras se anuncia otra: el usuario
    oiría «enviado a Marta» con el mensaje camino de Edu.

    Devuelve ``""`` si no hay conflicto, o qué es lo que no cuadra.
    """
    quien = str(args.get("contacto") or args.get("nombre") or "").strip()
    # Vale el nombre y vale el teléfono, y el teléfono en cualquiera de las formas
    # en que se escribe: quien dictó «escríbele al 600 11 22 33» puede oír luego
    # «al 600112233» y no es otra persona.
    suyos = {_clave(pendiente["nombre"]), pendiente["destino"]}
    if quien and _clave(quien) not in suyos and numero(quien) != pendiente["destino"]:
        return (f"me hablas de «{quien}» y lo que tengo preparado va "
                f"{_para_quien(pendiente['nombre'], pendiente['destino'])}")
    texto = " ".join(str(args.get("texto") or args.get("mensaje") or "").split())
    if texto and normalize(texto) != normalize(pendiente["texto"]):
        return f"me pasas otro texto, y lo que tengo preparado dice «{pendiente['texto']}»"
    return ""


def _sin_agenda(ruta: Path, nada: str = "NO he escrito nada") -> str:
    """Por qué no hay a quién escribir. Que el fichero exista lo cambia todo.

    «Créate una agenda» delante de una agenda que existe es de las respuestas que
    dejan a uno mirando la pantalla sin saber qué hacer, así que se distinguen los
    tres casos: no está, está y no se lee, o está vacía. El de en medio es el que
    más pasa, y casi siempre por lo mismo: un nombre con espacios sin comillas, que
    en TOML no es un error a medias — tumba el fichero entero y deja la agenda a
    cero. Volver a abrir el fichero aquí no cuesta nada: esto es la rama del fallo.
    """
    if not ruta.exists():
        return (
            f"{nada}: todavía no hay agenda de WhatsApp, así que no sé el "
            f"teléfono de nadie. Dile al usuario que cree {ruta} con una línea por "
            'contacto, tal cual: marta = "+34600112233".'
        )
    try:
        with open(ruta, "rb") as f:
            tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return (
            f"{nada}: {ruta} existe, pero no hay quien lo lea. Lo más "
            "probable es que le falten las comillas a algún nombre con espacios: se "
            'escribe "mi hermana" = "+34600112233". Díselo al usuario.'
        )
    return (
        f"{nada}: {ruta} está, pero no tiene ningún contacto apuntado. "
        'Dile al usuario que añada una línea por persona: marta = "+34600112233". '
        "Y que los grupos van aparte, en una sección [grupos], con el "
        "identificador que le dé `maripepis-wa grupos <parte del nombre>`."
    )


def _no_esta(quien: str, agenda: dict[str, str], nada: str = "NO he escrito nada") -> str:
    nombres = ", ".join(sorted(agenda)[:MAX_EN_DESCRIPCION])
    return (
        f"{nada}: «{quien}» no está en la agenda de WhatsApp, y no me "
        f"invento teléfonos. Los que hay apuntados son: {nombres}. Dile al usuario "
        "a quién tiene, por si se refería a alguno de ellos."
    )


def _como_se_llama(nombre: str, destino: str) -> str:
    """Cómo se nombra un destino al leerlo en voz alta.

    «el grupo Familia» y no «Familia» a secas, y esto no es un adorno: es la
    diferencia entre un mensaje que lee una persona y uno que leen doce. Quien
    escucha la confirmación tiene que poder notarlo sin ver la pantalla, que es
    justo cuando peor se nota.
    """
    return f"el grupo {nombre}" if es_grupo(destino) else nombre


def _para_quien(nombre: str, destino: str) -> str:
    """Lo mismo con la preposición pegada: «al grupo Familia», «a Marta».

    Existe por «va a el grupo familia», que es lo que sale al juntar las dos
    piezas por separado. Suena a chapuza dicho en voz alta, y esta frase es
    justo la que el usuario tiene que escuchar con atención.
    """
    return f"al grupo {nombre}" if es_grupo(destino) else f"a {nombre}"


def a_quien_y_que(args: dict, cfg: dict, nada: str) -> tuple[str, str, str] | str:
    """(nombre, destino, texto), o lo que hay que contestar en vez de escribir.

    El destino es un teléfono con prefijo o el identificador de un grupo
    (`…@g.us`); quien llama distingue uno de otro con `es_grupo`.

    Todo lo que decide **a quién** le llega el mensaje vive aquí, y lo comparten
    los dos modos: da igual que el mensaje se quede escrito en el chat o que
    salga de verdad, equivocarse de persona es el mismo error. Lo que cambia
    entre modos es solo el destino final, y por eso está separado.

    `nada` es cómo empieza cada negativa —«NO he escrito nada» o «NO he enviado
    nada»— porque esa primera frase es lo que hace que `es_fallo` la reconozca, y
    decirle al usuario que no se ha escrito algo que en realidad no se ha enviado
    es de las confusiones que dejan a uno sin saber qué pasó.
    """
    quien = str(args.get("contacto") or args.get("nombre") or args.get("numero") or "").strip()
    texto = " ".join(str(args.get("texto") or args.get("mensaje") or "").split())

    if not quien:
        return "¿A quién quieres que le escriba?"
    if not texto:
        return "¿Qué le digo?"
    if len(texto) > MAX_TEXTO:
        return (
            f"{nada}: el mensaje ocupa {len(texto)} caracteres y no le caben "
            f"más de {MAX_TEXTO}. Resúmelo en lo que quería decir y vuelve a llamarme."
        )

    if _parece_telefono(quien):
        # Un número dictado. Vale sin agenda —«escríbele al 600 11 22 33»— pero si
        # no cuadra se para aquí: abrirle el chat a un número a medias es abrírselo
        # a un desconocido.
        telefono = numero(quien, str(cfg.get("prefijo") or PREFIJO))
        if not telefono:
            return (
                f"{nada}: «{quien}» no me cuadra como número de teléfono. "
                "Pregúntale al usuario el número entero, con prefijo, o el nombre con "
                "el que lo tiene apuntado."
            )
        return quien, telefono, texto

    agenda = contactos(cfg)
    apuntados = grupos(cfg)
    if not agenda and not apuntados:
        return _sin_agenda(fichero_agenda(cfg), nada)
    encajan = buscar_en_la_libreta(quien, cfg)
    if not encajan:
        return _no_esta(quien, agenda | apuntados, nada)
    if len(encajan) > 1:
        # Y aquí caben las dudas nuevas: dos Martas, o una Marta y un grupo que
        # también se llama así. La segunda es peor —el mensaje lo leerían doce
        # personas— y se trata igual, que es lo que hay que hacer: preguntar.
        cuales = ", ".join(_como_se_llama(n, t) for n, t in encajan)
        return (
            f"{nada}: en la agenda hay varios que encajan con «{quien}» "
            f"({cuales}), y no elijo yo a quién le llega el mensaje. Pregúntale al "
            "usuario a cuál de ellos se refiere."
        )
    nombre, destino = encajan[0]
    return nombre, destino, texto


def preparar_mensaje(args: dict, cfg: dict | None = None) -> str:
    """Abre el chat del contacto con el mensaje escrito, sin enviarlo."""
    cfg = cfg or {}
    comprobado = a_quien_y_que(args, cfg, "NO he escrito nada")
    if isinstance(comprobado, str):
        return comprobado
    nombre, destino, texto = comprobado

    if es_grupo(destino):
        # Un enlace `whatsapp://send` lleva un teléfono, y un grupo no tiene. No
        # es que ZapZap no quiera: es que no hay nada que poner ahí. Se dice
        # entero, porque la salida existe y está a una línea de config.
        return (
            f"NO he escrito nada: «{nombre}» es un grupo, y a un grupo no puedo "
            "abrirle el chat: el enlace que abre WhatsApp lleva un teléfono, y un "
            "grupo no tiene. Dile al usuario que a los grupos solo se les escribe "
            'con la sesión propia, poniendo modo = "envio" en [tools.whatsapp].'
        )

    telefono = destino
    cliente = shutil.which(str(cfg.get("cliente") or CLIENTE))
    if cliente is None:
        return (
            f"NO he escrito nada: no encuentro `{cfg.get('cliente') or CLIENTE}` en este "
            "equipo, y es el que abre WhatsApp. Díselo al usuario; sin él no puedo."
        )

    if not zapzap_abierto():
        # El enlace a una app cerrada no se pierde «un poco»: ZapZap arranca y lo
        # tira, así que anunciar el mensaje aquí sería mentir con todas las letras.
        lanzar([cliente])
        return (
            "NO he escrito el mensaje: WhatsApp estaba cerrado, y con la aplicación "
            "cerrada el chat no se abre. Te lo he abierto yo; en cuanto cargue, "
            "pídemelo otra vez." + MARCA_MODELO + " Dile exactamente eso y no des el "
            "mensaje por escrito ni por enviado. No vuelvas a llamarme en este turno: "
            "WhatsApp tarda unos segundos en cargar."
        )

    log.info("Abro el chat de %s (%s) con %d caracteres.", nombre, telefono, len(texto))
    lanzar([cliente, enlace(telefono, texto)])
    return (
        f"Hecho: he abierto el chat de {nombre} en WhatsApp y he dejado escrito "
        f"«{texto}». NO está enviado."
        + MARCA_MODELO + " Cuéntaselo así: que se lo has dejado escrito en el chat y "
        "que le dé a Enter para enviarlo. NO digas que lo has enviado, que ya está "
        "mandado ni que le ha llegado — no es verdad, y quien te oiga se quedará "
        "esperando una respuesta a un mensaje que nunca salió."
    )


def preparar_envio(args: dict, cfg: dict | None = None) -> str:
    """Redacta el mensaje y lo deja esperando un «sí». No sale nada todavía.

    El primero de los dos pasos del modo `envio`. Hace todo lo caro y todo lo
    delicado —resolver a quién, comprobar el texto— y se para justo antes de la
    única línea que no se puede deshacer.

    Que pare aquí es lo que devuelve al usuario lo que le quitó el envío directo:
    oír a quién va y qué pone **antes**, no enterarse después. Si el micrófono
    entendió «Marcos» donde dijo «Marta», esto se nota al oírlo, y lo único que
    hay que hacer es no decir que sí.

    No habla con el demonio: si la sesión no está, se sabrá al confirmar. Es a
    propósito —preparar tiene que costar poco y no fallar por nada— y tiene su
    precio, que es enterarse tarde de que no había con quién enviar.
    """
    cfg = cfg or {}
    comprobado = a_quien_y_que(args, cfg, "NO he preparado nada")
    if isinstance(comprobado, str):
        return comprobado
    nombre, destino, texto = comprobado

    guardar_pendiente(nombre, destino, texto)
    log.info("Preparado para %s (%s): %d caracteres. Falta el «sí».",
             nombre, destino, len(texto))
    return (
        f"Preparado y SIN ENVIAR: va {_para_quien(nombre, destino)} y dice «{texto}»."
        + MARCA_MODELO + " Léeselo al usuario tal cual —a quién va y qué pone, con "
        "esas mismas palabras— y pregúntale si lo mando. NO digas que está enviado, "
        "ni que ya ha salido, ni que le ha llegado: no ha salido nada todavía. Y no "
        "llames ahora a enviar_mensaje_whatsapp: eso es del turno siguiente, y solo "
        "si el usuario contesta que sí. Si dice que no, o te cambia el mensaje, "
        "vuelve a llamarme a mí con lo nuevo. Tiene un minuto para contestar."
    )


def enviar_mensaje(args: dict, cfg: dict | None = None) -> str:
    """Suelta el mensaje que estaba preparado. De verdad y sin marcha atrás.

    El segundo paso, y **no lleva argumentos**. Eso no es una comodidad: es la
    herramienta entera. Sin destinatario y sin texto, lo único que el modelo
    puede hacer con esto es confirmar lo que él mismo acaba de redactar y el
    usuario acaba de oír; no hay hueco por donde se le cuele un mensaje inventado
    ni una Marta que nadie ha nombrado.

    Y se niega en cuatro casos, que son los cuatro modos de que un «sí» no sea un
    sí: que no haya nada preparado, que lo preparado haya caducado, que venga del
    mismo turno —o sea, que el usuario no haya llegado a contestar— y que el
    modelo traiga en la llamada algo distinto de lo que hay guardado.
    """
    cfg = dict(cfg or {})
    args = args if isinstance(args, dict) else {}

    pendiente = leer_pendiente()
    if pendiente is None:
        return (
            "NO he enviado nada: no tengo ningún mensaje preparado, y esta "
            "herramienta no redacta, solo confirma." + MARCA_MODELO + " Si el usuario "
            "quiere mandar un wasap, llama primero a preparar_mensaje_whatsapp con el "
            "contacto y el texto, léeselo, y confirma cuando te diga que sí."
        )
    if caducado(pendiente):
        olvidar_pendiente()
        return (
            f"NO he enviado nada: el mensaje que tenía preparado para "
            f"{_como_se_llama(pendiente['nombre'], pendiente['destino'])} ha caducado, "
            "hace más de un minuto que lo redacté."
            + MARCA_MODELO + " Díselo y pregúntale si lo prepara otra vez; si dice que "
            "sí, llama a preparar_mensaje_whatsapp con el mismo texto."
        )
    if pendiente["turno"] == turno_actual():
        # Lo más importante de todo el fichero. El modelo lee «léeselo y espera» y
        # a veces encadena las dos llamadas sin dejar hablar a nadie; si eso
        # colara, la confirmación sería un adorno y esto volvería a ser el envío
        # directo con un paso de más.
        return (
            "NO he enviado nada: acabo de prepararlo en este mismo turno y el usuario "
            "todavía no ha dicho que sí." + MARCA_MODELO + " Léele ahora a quién va y "
            "qué pone, pregúntale si lo mando, y termina el turno ahí. Vuelve a "
            "llamarme SOLO cuando te conteste que sí."
        )
    if (choca := _desencaja(args, pendiente)):
        return (
            f"NO he enviado nada: {choca}, y yo mando lo que tengo, no lo que me "
            "digas aquí." + MARCA_MODELO + " Esta herramienta no lleva argumentos. Si "
            "el usuario ha cambiado el mensaje o el destinatario, llama otra vez a "
            "preparar_mensaje_whatsapp con lo nuevo, léeselo, y confirma después."
        )

    nombre = pendiente["nombre"]
    destino = pendiente["destino"]
    texto = pendiente["texto"]

    # Se olvida ANTES de mandarlo, y no después. Un pendiente que sigue ahí es un
    # mensaje que se puede volver a soltar, y entre las dos formas de fallar —que
    # no salga y haya que dictarlo otra vez, o que salga dos veces— solo una tiene
    # arreglo. Si el envío falla, el usuario repite; el mensaje duplicado, no.
    olvidar_pendiente()

    resp = _enviar_por_la_sesion(destino, texto, path=cfg.get("socket"))
    if resp is None:
        # Ni siquiera hay con quién hablar. Es un fallo de instalación, no del
        # mensaje, y se dice así para que nadie ande buscando el wasap perdido.
        return (
            "NO he enviado nada: no hay sesión de WhatsApp escuchando en este equipo. "
            "Dile al usuario que arranque el servicio: systemctl --user start "
            "maripepis-whatsapp." + MARCA_MODELO + " No des el mensaje por enviado ni "
            "por escrito: no ha salido nada, y el que tenía preparado ya no está."
        )
    if not resp.get("ok"):
        return (
            f"NO he enviado nada: {resp.get('error') or 'WhatsApp no lo ha aceptado'}."
            + MARCA_MODELO + " Cuéntale al usuario ese motivo tal cual y no des el "
            "mensaje por enviado. Si quiere reintentarlo, hay que prepararlo de nuevo."
        )

    log.info("Enviado a %s (%s): %d caracteres.", nombre, destino, len(texto))
    return (
        f"Hecho: he ENVIADO {_para_quien(nombre, destino)} el mensaje «{texto}». "
        "Ya está mandado."
        + MARCA_MODELO + " Díselo en pasado y sin adornos: que se lo has enviado, y a "
        "quién. NO digas que lo ha leído ni que ha contestado — eso no lo sabes. Si "
        "el usuario se arrepiente, todavía puede pedirte que lo borres."
    )


def borrar_mensaje(args: dict, cfg: dict | None = None) -> str:
    """El «no, espera». Retira lo último, esté donde esté.

    Tiene dos trabajos porque el usuario dice lo mismo en dos situaciones que
    desde fuera no distingue, y no tiene por qué:

    - **Si hay un mensaje preparado**, todavía no ha salido nada: se tira el
      pendiente y ya está. Es el caso bueno, y no hace falta ni molestar a
      WhatsApp.
    - **Si ya salió**, se le pide al demonio el «eliminar para todos». WhatsApp
      lo permite un rato nada más, y pasado ese rato dice que no: eso no es un
      fallo nuestro, es la respuesta, y se cuenta tal cual.

    Y aquí no hay confirmación que valga, al revés que en el envío: retirar es el
    lado seguro de los dos. Lo peor que puede pasar por hacerle caso de más es
    tener que volver a mandar un mensaje; lo peor por no hacerle caso es que se
    quede puesto uno que no querías. Tampoco lleva argumentos —siempre es lo
    último—, que es lo que impide que el modelo se dedique a borrar mensajes por
    su cuenta.
    """
    cfg = dict(cfg or {})

    if (pendiente := leer_pendiente()) is not None and not caducado(pendiente):
        olvidar_pendiente()
        quien = _como_se_llama(pendiente["nombre"], pendiente["destino"])
        log.info("Descartado el mensaje que esperaba para %s.", pendiente["nombre"])
        return (
            f"Hecho: he descartado el mensaje que tenía preparado para {quien}. NO "
            "había salido, así que no hay nada que retirar."
            + MARCA_MODELO + " Díselo así: que no se ha llegado a enviar. No digas "
            "que lo has borrado de WhatsApp, porque nunca estuvo allí."
        )

    resp = _revocar_por_la_sesion(path=cfg.get("socket"))
    if resp is None:
        return (
            "NO he borrado nada: no hay sesión de WhatsApp escuchando en este equipo. "
            "Dile al usuario que arranque el servicio: systemctl --user start "
            "maripepis-whatsapp." + MARCA_MODELO + " Y que si el mensaje llegó a "
            "salir, sigue puesto: puede borrarlo desde el móvil."
        )
    if not resp.get("ok"):
        # El motivo importa y son dos muy distintos: «no he mandado nada» (y
        # entonces no hay nada que borrar) o «WhatsApp ya no me deja» (y entonces
        # el mensaje sigue ahí y hay que decirlo, no dejarlo en el aire).
        return (
            f"NO he borrado nada: {resp.get('error') or 'WhatsApp no me ha dejado'}."
            + MARCA_MODELO + " Cuéntale ese motivo tal cual. Si el mensaje llegó a "
            "salir, sigue puesto y solo puede quitarlo él desde el móvil."
        )

    log.info("Retirado el último mensaje enviado.")
    return (
        "Hecho: he BORRADO el último mensaje que envié. En WhatsApp sale ahora como "
        "«se eliminó este mensaje»."
        + MARCA_MODELO + " Díselo así, y sin prometer de más: el otro puede haberlo "
        "leído antes de que lo retiraras, y eso no lo sabes."
    )


def _parece_telefono(texto: str) -> bool:
    """¿Esto es un número dictado y no el nombre de alguien?"""
    limpio = (texto or "").strip()
    return bool(re.fullmatch(r"[+()\d\s.-]{6,}", limpio)) and len(re.sub(r"\D", "", limpio)) >= 6


def descripcion(cfg: dict | None = None) -> str:
    """La agenda de este equipo, para la descripción de la herramienta.

    Van los **nombres, nunca los teléfonos ni los identificadores de grupo**:
    esta descripción viaja en cada petición al modelo, y con el backend de Claude
    eso es la nube. El modelo no necesita el destino —lo resuelve
    `buscar_en_la_libreta`—, solo saber a quién puede escribir para no inventarse
    a nadie.

    Los grupos solo se nombran cuando se les puede escribir, o sea en modo envío:
    con el enlace de ZapZap no hay forma de abrir uno, y nombrar un destino
    imposible es invitar a un turno que acaba en «no he podido».
    """
    nombres = sorted(contactos(cfg))
    suyos = sorted(grupos(cfg)) if modo_de(cfg) == "envio" else []
    if not nombres and not suyos:
        return (
            "Este equipo todavía no tiene agenda de WhatsApp. Si te piden mandar un "
            "mensaje, llámame igual: te diré yo cómo se crea, con lo que hay que "
            "escribir y dónde."
        )

    partes = []
    if nombres:
        lista = ", ".join(nombres[:MAX_EN_DESCRIPCION])
        partes.append(f"Los contactos de su agenda son: {lista}"
                      + ("..." if len(nombres) > MAX_EN_DESCRIPCION else ""))
    if suyos:
        lista = ", ".join(suyos[:MAX_EN_DESCRIPCION])
        partes.append(f"Y estos son grupos, con varias personas dentro: {lista}"
                      + ("..." if len(suyos) > MAX_EN_DESCRIPCION else ""))
    return (
        ". ".join(partes) + ". Usa el nombre tal cual, y si te piden escribir a "
        "alguien que no está en esas listas, dilo: no te inventes números de "
        "teléfono ni des por hecho que un grupo está apuntado."
    )


#: Los nombres que ve el modelo. Que sean dos y no uno no es cosmética:
#: `veracidad.desmiente_envio` mira **cuál** se llamó —y cuáles existen— para
#: saber si un «lo he enviado» es verdad o es mentira, y con un solo nombre esa
#: pregunta no se podría contestar. De paso, el modelo lee un verbo que coincide
#: con lo que va a pasar de verdad.
#:
#: En borrador solo existe `preparar_mensaje_whatsapp`, y prepararlo es dejarlo
#: escrito en ZapZap. En envío existen las tres: preparar redacta, enviar suelta
#: y borrar se arrepiente. La última tampoco está en borrador, y por lo mismo que
#: las otras — allí no hay nada enviado de lo que arrepentirse: el mensaje sigue
#: en el cuadro de texto, y quitarlo es borrarlo con el teclado.
PREPARAR = "preparar_mensaje_whatsapp"
ENVIAR = "enviar_mensaje_whatsapp"
BORRAR = "borrar_mensaje_whatsapp"

_PARAMETROS = {
    "type": "object",
    "properties": {
        "contacto": {
            "type": "string",
            "description": (
                "A quién: el nombre tal y como está en su agenda ('Marta', "
                "'hermana'). También vale un teléfono con prefijo "
                "('+34600112233') si el usuario lo dicta entero."
            ),
        },
        "texto": {
            "type": "string",
            "description": "El mensaje, con las palabras del usuario.",
        },
    },
    "required": ["contacto", "texto"],
}

#: La de confirmar no lleva nada, y se le dice de las tres maneras que entiende
#: un esquema: sin propiedades, sin obligatorias y con la puerta cerrada a
#: inventarse alguna. Es la mitad de lo que hace segura a esta herramienta.
_SIN_PARAMETROS = {"type": "object", "properties": {}, "required": [],
                   "additionalProperties": False}

_COMUN = (
    "El texto va con las palabras del usuario: no lo adornes, no lo firmes y no "
    "le añadas nada que no haya dicho, que lo va a leer otra persona tal cual. "
)

_DESCRIPCION = {
    "borrador": (
        "Deja un mensaje ESCRITO en el chat de WhatsApp de un contacto, listo para "
        "que el usuario le dé a enviar. Úsala cuando te pidan mandarle un wasap, un "
        "whatsapp o un mensaje a alguien. "
        "OJO, y esto se lo dices siempre: NO lo envía. Lo deja redactado en el chat, "
        "abierto en pantalla, y el enviar lo da el usuario, que es quien tiene que "
        "ver a quién va y qué pone. Nunca digas que lo has enviado, que ya está "
        "mandado ni que le ha llegado. " + _COMUN
    ),
    "envio": (
        "PREPARA un mensaje de WhatsApp para un contacto. Úsala cuando te pidan "
        "mandarle un wasap, un whatsapp o un mensaje a alguien: es siempre la "
        "primera de las dos, y la única que lleva el contacto y el texto. "
        "NO envía nada: deja el mensaje redactado y esperando. Lo que tienes que "
        "hacer con lo que te conteste es leérselo al usuario —a quién va y qué "
        "pone— y preguntarle si lo mandas; solo si te dice que sí, y ya en el turno "
        "siguiente, llamas a " + ENVIAR + ". " + _COMUN
    ),
    "confirmar": (
        "ENVÍA el mensaje de WhatsApp que acabas de preparar y leerle al usuario. "
        "Llámala SOLO cuando él te haya contestado que sí. "
        "No lleva argumentos, y no es un descuido: manda exactamente lo que "
        "preparaste, ni otro texto ni otra persona. Si el usuario ha cambiado algo, "
        "no la llames — vuelve a llamar a " + PREPARAR + " con lo nuevo. "
        "Sale de verdad y al momento, así que una sola vez: si te contesto que está "
        "enviado, está enviado."
    ),
    "borrar": (
        "El «no, espera» del usuario: retira el ÚLTIMO mensaje de WhatsApp. Úsala "
        "cuando diga que lo borres, que lo quites, que no lo mande, que se ha "
        "equivocado o que da igual. "
        "Sirve para las dos situaciones y tú no tienes que distinguirlas: si el "
        "mensaje solo estaba preparado, lo descarta; si ya había salido, le pide a "
        "WhatsApp el «eliminar para todos», que solo se puede durante un rato. "
        "No lleva argumentos: siempre es el último. Llámala en cuanto te lo pida y "
        "sin preguntar nada — aquí no hay que confirmar, quitar de más es un mensaje "
        "que se vuelve a mandar. Y cuenta lo que te devuelva sin adornarlo."
    ),
}


def build_whatsapp_tools(cfg: dict | None = None) -> list[Tool]:
    """Las herramientas de WhatsApp del modo configurado, con su `[tools.whatsapp]`.

    Una en borrador y tres en envío, y el número es lo que cuenta la historia:
    sin Enter que dar, hace falta un paso antes (leerlo y esperar el «sí») y uno
    después (retirarlo si aun así no era). El modelo no tiene que enterarse de
    que existe un modo; solo de lo que pasa cuando llama a lo que tiene delante.
    """
    cfg = dict(cfg or {})
    agenda = descripcion(cfg)
    if modo_de(cfg) != "envio":
        return [Tool(
            name=PREPARAR,
            description=_DESCRIPCION["borrador"] + agenda,
            parameters=_PARAMETROS,
            handler=lambda args: preparar_mensaje(args, cfg),
        )]
    return [
        Tool(
            name=PREPARAR,
            description=_DESCRIPCION["envio"] + agenda,
            parameters=_PARAMETROS,
            handler=lambda args: preparar_envio(args, cfg),
        ),
        Tool(
            name=ENVIAR,
            description=_DESCRIPCION["confirmar"],
            parameters=_SIN_PARAMETROS,
            handler=lambda args: enviar_mensaje(args, cfg),
        ),
        Tool(
            name=BORRAR,
            description=_DESCRIPCION["borrar"],
            parameters=_SIN_PARAMETROS,
            handler=lambda args: borrar_mensaje(args, cfg),
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    """`python -m maripepis.tools.whatsapp <contacto> <texto>` — y `--enviar`.

    Existe por los proveedores que traen sus propias herramientas y no aceptan
    las nuestras (`claude_code_provider.accepts_tools = False`): la única forma
    de que usen esta es que la llamen por la shell.

    Y llamarla es justo lo que se quiere. La alternativa —contarle al modelo por
    el *system prompt* que ZapZap entiende enlaces `whatsapp://`— acaba en un
    enlace montado a mano con un teléfono inventado, sin agenda, sin el «ante la
    duda pregunta» y sin el «NO está enviado». Por aquí sigue todo puesto, y lo
    que el modelo lee es exactamente lo que leería como herramienta.

    Los pasos del modo envío también: se prepara con contacto y texto, se
    confirma con `--enviar` a secas y se retira con `--borrar`. Que el pendiente viva en un fichero es lo
    que lo hace posible aquí, donde cada paso es un proceso distinto.

    Y una condición que hay que tener presente si algún día ejecuta esto otro
    backend: **el freno de los dos turnos depende de `MARIPEPIS_TURNO`**. Sin esa
    marca en el entorno, dos procesos son siempre dos turnos y el «sí» se lo
    puede dar el modelo a sí mismo ejecutando las dos órdenes seguidas. Quien
    lance esta orden desde un turno de conversación tiene que ponerla, como hace
    `claude_code_provider._env()`. Sin ella queda el caso de la terminal, donde
    los dos turnos son de verdad dos: quien escribe la segunda orden es alguien
    que ha leído la primera.

    Devuelve 1 si el mensaje NO ha salido (ni escrito, ni preparado, ni enviado),
    para que un `Bash` que solo mire el código de salida se entere igual.
    """
    parser = argparse.ArgumentParser(
        prog="python -m maripepis.tools.whatsapp",
        description=("Le escribe a un contacto por WhatsApp. Según `[tools.whatsapp] "
                     "modo`, deja el mensaje escrito en el chat (borrador) o lo "
                     "prepara para enviarlo con --enviar (envio)."),
    )
    parser.add_argument("contacto", nargs="?",
                        help="Nombre en la agenda, o un teléfono con prefijo.")
    parser.add_argument("texto", nargs="?", help="El mensaje, con las palabras del usuario.")
    parser.add_argument("--enviar", action="store_true",
                        help=("Envía el mensaje que ya estaba preparado, y nada más: "
                              "no lleva ni contacto ni texto a propósito."))
    parser.add_argument("--borrar", action="store_true",
                        help=("Retira lo último: descarta el mensaje preparado, o pide "
                              "el «eliminar para todos» del que ya salió."))
    parser.add_argument("--config", default=None, help="Ruta de config.toml.")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config).get("tools", {}).get("whatsapp", {})
    except FileNotFoundError:
        cfg = {}          # sin config.toml valen los valores por defecto

    # El modo manda también por aquí: si no, un proveedor con shell propia se
    # quedaría siempre en borrador aunque el usuario haya pedido lo contrario.
    modo = modo_de(cfg)

    if args.enviar and args.borrar:
        parser.error("o se manda o se borra, pero no las dos cosas")

    if args.enviar or args.borrar:
        # Y ninguna de las dos acepta un argumento más. Aceptarlos y no usarlos
        # sería peor que rechazarlos: quien los escribe cree que está mandando (o
        # borrando) eso, y lo que pasaría es lo que hubiera preparado antes.
        cual = "--enviar" if args.enviar else "--borrar"
        if args.contacto or args.texto:
            parser.error(f"{cual} va solo: actúa sobre lo último, no sobre lo que le digas")
        if modo != "envio":
            parser.error(f"{cual} es del modo «envio»; en «borrador» el mensaje se "
                         "queda escrito en el chat, y lo mandas —o lo quitas— tú")
        resultado = enviar_mensaje({}, cfg) if args.enviar else borrar_mensaje({}, cfg)
    else:
        if not args.contacto or not args.texto:
            parser.error("hacen falta el contacto y el texto")
        accion = preparar_envio if modo == "envio" else preparar_mensaje
        resultado = accion({"contacto": args.contacto, "texto": args.texto}, cfg)

    print(resultado)
    return 1 if es_fallo(resultado) else 0


if __name__ == "__main__":
    raise SystemExit(main())
