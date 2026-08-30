"""¿La respuesta dice haber hecho algo que no ha hecho?

Un modelo narra el éxito con una seguridad que no se corresponde con nada:
consulta el tiempo, no escribe el fichero y remata con «te lo he guardado en
documentos». Por escrito cantaría; dicho en voz alta, y sin ver la pantalla, no
hay forma humana de distinguirlo de que funcione.

Las listas de frases de aquí abajo salieron de medir modelos locales de 7B, que
es donde esto pasaba a diario. Ese motor ya no está, y con Claude pasa mucho
menos — pero «mucho menos» no es «nunca», y quien escucha sigue sin ver la
pantalla. Lo que se conserva es el último recurso: `turn` lo usa **después** de
la respuesta, cuando ya no hay nada que arreglar, para desmentirla en voz alta.

Lo que sí es de todos los tamaños es el WhatsApp: ahí no hace falta que el modelo
mienta, basta con que diga «enviado» de un mensaje que solo está preparado, y
quien lo oye no dice el «sí» que lo mandaba.
"""

from __future__ import annotations

from .utils.phrases import normalize

# Pistas de que la respuesta afirma haber HECHO algo, no de haberlo contado.
# «No sé qué tiempo hará» es una respuesta honrada y no se toca; «he actualizado
# el archivo» sin haber llamado a nada, no.
CANTA_VICTORIA = (
    "he creado", "he escrito", "he anadido", "he agregado", "he actualizado",
    "he guardado", "he apuntado", "he modificado", "he cambiado", "he corregido",
    "he rellenado", "he completado", "he borrado", "he movido", "he copiado",
    "he abierto", "he ejecutado", "he buscado", "he puesto", "he dejado",
    "he sobrescrito", "ya lo tienes", "ya esta hecho", "ya esta listo",
    "queda hecho", "lo tienes en", "te lo he dejado", "te lo he guardado",
    "esta actualizado", "ha quedado actualizado", "he asegurado", "he incluido",
    "queda escrito", "queda guardado", "ahora contiene", "ya contiene",
    "he modificado",
    # En pasiva, que es como lo dice llama3.1:8b: «el archivo se ha modificado».
    "se ha modificado", "se ha guardado", "se ha escrito", "se ha actualizado",
    "se ha creado", "ha sido modificado", "ha sido guardado", "ha sido creado",
    "ha sido actualizado", "queda modificado",
)

# Lo que dice haber hecho → la herramienta que tendría que haberlo hecho, y cómo
# se desmiente. Contar llamadas a secas no basta: el modelo mira el tiempo y
# remata con «te lo he guardado». Llamó a *algo*, así que un contador se queda
# tan ancho; lo que importa es si llamó a **la que hacía falta**.
#
# Las dos de WhatsApp están aquí porque cualquiera de ellas justifica un «le he
# escrito»; cuáles existen de verdad depende de `[tools.whatsapp] modo`, y en
# borrador solo hay la primera.
EXIGEN_HERRAMIENTA = (
    (("he guardado", "he escrito", "he actualizado", "he sobrescrito", "he anadido",
      "he agregado", "he creado el archivo", "he creado un archivo", "he rellenado",
      "he creado el fichero", "he creado un fichero", "he completado", "he corregido",
      "te lo he dejado", "te lo he guardado", "esta guardado", "queda guardado",
      "ha quedado actualizado", "esta actualizado", "he asegurado", "he incluido",
      "ahora contiene", "ya contiene", "he modificado",
      "se ha modificado", "se ha guardado", "se ha escrito", "se ha actualizado",
      "se ha creado", "ha sido modificado", "ha sido guardado", "ha sido creado",
      "ha sido actualizado", "queda modificado"),
     # WhatsApp entra aquí porque comparte las palabras: quien deja un mensaje
     # escrito en un chat dice «le he escrito» y «se lo he dejado escrito» igual
     # que quien guarda un fichero. Sin esto, un turno de WhatsApp que ha ido
     # perfecto se desmentiría a sí mismo con un «no he llegado a escribir el
     # fichero» que no viene a cuento.
     ("escribir_fichero", "preparar_mensaje_whatsapp", "enviar_mensaje_whatsapp"),
     "no he llegado a escribir el fichero"),
    (("he abierto", "te he abierto"),
     ("abrir_aplicacion", "abrir_navegador", "buscar_en_internet"),
     "no he abierto nada"),
    (("he creado la carpeta", "he ejecutado", "he borrado", "he movido", "he copiado"),
     # `borrar_mensaje_whatsapp` entra por «he borrado», que es lo que dice quien
     # acaba de retirar un wasap igual que quien borra un fichero. Sin esto, un
     # «ya lo he borrado» correcto se desmentiría con un «no he ejecutado ningún
     # comando» que no viene a cuento de nada.
     ("ejecutar_comando", "borrar_mensaje_whatsapp"),
     "no he ejecutado ningún comando"),
)

# Dar por enviado un mensaje de WhatsApp. Esta afirmación era falsa SIEMPRE, y por
# eso se comprobaba aparte; desde que existe el modo `envio` ya no lo es, y lo que
# decide es **qué herramienta** se llamó:
#
# - con `preparar_mensaje_whatsapp` sigue siendo falsa, pero por dos motivos
#   distintos según el modo, y al usuario le cambia lo que tiene que hacer: en
#   borrador el mensaje está escrito en el chat y le toca darle a enviar; en envío
#   está preparado y esperando, y le toca decir que sí;
# - con `enviar_mensaje_whatsapp` es verdad, y desmentirla sería la misma mentira
#   del revés — el usuario se pondría a buscar un borrador que no existe y a
#   reenviar un mensaje que ya salió.
#
# Sigue siendo de las caras, en las dos direcciones: quien la oye de más se queda
# tranquilo y no le da a enviar; quien la oye desmentida de más manda el wasap dos
# veces.
DICE_HABER_ENVIADO = (
    "he enviado", "he mandado", "se lo he enviado", "se lo he mandado",
    "te lo he enviado", "te lo he mandado", "mensaje enviado", "esta enviado",
    "se ha enviado", "ha sido enviado", "queda enviado", "ya se lo he",
    "le ha llegado", "ya lo ha recibido", "lo ha recibido",
)

# Lo que, delante de esas frases, las convierte en lo contrario. Va aquí porque
# «no lo he enviado» es justo lo que se le ha pedido al modelo que diga: sin esto,
# el desmentido saltaría precisamente cuando acierta.
_NEGACIONES = frozenset({"no", "sin", "tampoco", "nunca", "jamas", "ni"})


def canta_victoria(texto: str) -> bool:
    """¿La respuesta afirma haber hecho algo (y no solo haberlo explicado)?"""
    limpio = f" {normalize(texto)} "
    return any(pista in limpio for pista in CANTA_VICTORIA)


def dice_haber_enviado(texto: str) -> bool:
    """¿La respuesta da un mensaje por enviado (y no por lo contrario)?

    Mira las tres palabras de delante en busca de un «no», que es donde cabe:
    «no lo he enviado», «todavía no se lo he mandado». Si la negación queda más
    lejos, no se desmiente — que es el lado seguro de los dos: se escapa alguna
    mentira, pero no se desmiente ningún acierto.
    """
    limpio = f" {normalize(texto)} "
    for pista in DICE_HABER_ENVIADO:
        desde = 0
        while (i := limpio.find(pista, desde)) != -1:
            if not set(limpio[:i].split()[-3:]) & _NEGACIONES:
                return True
            desde = i + 1
    return False


def _en_dos_pasos(execute) -> bool:  # noqa: ANN001
    """¿El WhatsApp de este equipo va con confirmación hablada (modo envío)?

    Se sabe por las herramientas que hay puestas, no por la configuración, que
    aquí no llega: `enviar_mensaje_whatsapp` solo existe en modo envío, y ahí
    preparar un mensaje significa dejarlo esperando un «sí». En borrador la
    misma llamada significa otra cosa muy distinta —dejarlo escrito en el chat—,
    y al usuario le cambia lo que tiene que hacer a continuación.
    """
    return "enviar_mensaje_whatsapp" in getattr(execute, "nombres", set())


def espera_confirmacion(execute) -> bool:  # noqa: ANN001
    """¿El turno acaba con un mensaje preparado esperando un «sí»?

    Ese turno termina bien anunciando lo que va a pasar («le mando esto a Edu,
    ¿te parece?»), que en cualquier otro sería el turno dejado a medias. Hay que
    poder distinguirlo, porque todo lo demás de este módulo está montado para
    desconfiar exactamente de esa forma de acabar.
    """
    usadas = getattr(execute, "herramientas_ok", None)
    if not callable(usadas):
        return False
    return "preparar_mensaje_whatsapp" in usadas() and _en_dos_pasos(execute)


def confirmacion_prematura(execute) -> bool:  # noqa: ANN001
    """¿El turno ha intentado confirmar un mensaje que nadie ha aprobado todavía?

    Pasa con los modelos pequeños: leen «léeselo y espera» y llaman igual a
    enviar en la misma vuelta. La herramienta se niega, y esa negativa queda
    apuntada como el último fallo del turno — pero el turno no ha fallado: el
    mensaje está preparado y el modelo está preguntando, que es exactamente lo
    que tenía que pasar. Sin esto, el usuario oye su pregunta y detrás un «en
    realidad no ha funcionado» que no viene a cuento y que le hace pensar que el
    wasap se ha perdido.

    Se reconoce sin mirar ningún texto: se llamó a la de confirmar, no salió
    bien, y hay un mensaje recién preparado esperando.
    """
    llamadas = {n for n, _a, _r in getattr(execute, "registro", ())}
    if "enviar_mensaje_whatsapp" not in llamadas:
        return False
    usadas = getattr(execute, "herramientas_ok", None)
    hechas = usadas() if callable(usadas) else set()
    return "enviar_mensaje_whatsapp" not in hechas and espera_confirmacion(execute)


def desmiente_envio(reply: str, execute) -> str:  # noqa: ANN001
    """El desmentido si la respuesta da el mensaje por enviado. ``""`` si no.

    Si `enviar_mensaje_whatsapp` salió bien no hay nada que desmentir: el mensaje
    está mandado y decirlo es lo correcto. El desmentido queda para cuando solo
    se preparó —y entonces dice **dónde está** el mensaje, que es lo único que le
    sirve a quien no ve la pantalla— y para cuando no se llamó a ninguna.

    Vive fuera de `lo_que_no_ha_hecho` porque no es el mismo caso ni de lejos.
    Aquella habla de herramientas que hacían falta y no se llamaron; aquí la
    herramienta se llamó y salió bien, y lo que falla es lo que el modelo cuenta
    de ella. No hay nada que arreglar en el turno: solo que se diga la verdad al
    final de él.
    """
    if not dice_haber_enviado(reply):
        return ""
    usadas = getattr(execute, "herramientas_ok", None)
    hechas = usadas() if callable(usadas) else set()
    if "enviar_mensaje_whatsapp" in hechas:
        return ""          # esta vez es verdad: salió por la sesión propia
    if "borrar_mensaje_whatsapp" in hechas:
        # Un turno de retirar habla de un mensaje enviado —«he borrado el mensaje
        # enviado a Edu»— sin estar afirmando que lo acabe de enviar él, y ese
        # envío es de un turno anterior, que aquí no se ve. Callar deja escapar
        # alguna mentira rara; desmentir rompería el caso corriente, y de los dos
        # errores este módulo elige siempre el primero.
        return ""
    if "preparar_mensaje_whatsapp" in hechas:
        if _en_dos_pasos(execute):
            return ("no lo he enviado todavía: lo tengo preparado, dime que sí y te "
                    "lo mando")
        return ("no lo he enviado: te lo he dejado escrito en el chat, y darle a "
                "enviar te toca a ti")
    return "no he enviado ningún mensaje"


def _se_delata_nombrando(limpio: str, hechas: set[str]) -> str:
    """«He llamado a escribir_fichero para crear el archivo...» y no la llamó.

    Esta se pilla sola, sin listas de frases: dice el nombre de la herramienta.
    `normalize` convierte el guion bajo en espacio, de ahí la traducción.
    """
    for nombre in {n for _p, hacen_falta, _m in EXIGEN_HERRAMIENTA for n in hacen_falta}:
        if nombre in hechas:
            continue
        dicho = nombre.replace("_", " ")
        if f" he llamado a {dicho} " in limpio or f" llame a {dicho} " in limpio:
            return f"no he llamado a {nombre}"
    return ""


def lo_que_no_ha_hecho(reply: str, execute) -> str:  # noqa: ANN001
    """El motivo del desmentido, o ``""`` si no hay nada que desmentir.

    `execute` solo tiene que saber contestar a `herramientas_ok()` (cuáles
    salieron bien) o a `llamadas` (cuántas se hicieron). Si no sabe ni eso, no se
    afirma nada: sobre un invocable pelado no hay forma de saberlo.

    Esto es una heurística, y se nota: la lista de frases se ha ensanchado dos
    veces con ejemplos medidos y nunca estará completa. Lo que sí es firme es el
    lado seguro — si no hay pista, no se desmiente—, así que lo que se escapa es
    una mentira que pasa, nunca un acierto que se desmiente por error.
    """
    limpio = f" {normalize(reply)} "

    usadas = getattr(execute, "herramientas_ok", None)
    if callable(usadas):
        hechas = usadas()
        if (delatado := _se_delata_nombrando(limpio, hechas)):
            return delatado
        for pistas, hacen_falta, motivo in EXIGEN_HERRAMIENTA:
            if any(p in limpio for p in pistas) and not (hechas & set(hacen_falta)):
                return motivo

    # Y el caso general: presume de algo y no llamó absolutamente a nada.
    if getattr(execute, "llamadas", None) == 0 and canta_victoria(reply):
        return "no he hecho nada, no he llegado a usar ninguna herramienta"
    return ""
