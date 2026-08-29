"""Arranque y bucle de conversación (Fase 5: pulido).

Novedades de la Fase 5:
  - Habla mientras genera: cada frase se reproduce en cuanto está lista
    (streaming LLM → TTS con un worker de voz en segundo plano).
  - Palabra de activación y frases de salida (por transcripción).
  - Barge-in por teclado: Ctrl-C corta la respuesta hablada.

Modos de entrada (con degradación en cada nivel): manos libres (VAD) →
push-to-talk (Enter) → texto.
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .llm.conversation import Conversation
from .llm.factory import build_provider
from .memory import load_memory
from .tools import es_fallo, resumen_de_la_llamada
from .turn import reply_turn
from .utils.logging import get_logger
from .utils.phrases import is_exit, normalize, strip_wake_word

_DEFAULT_EXITS = {"salir", "exit", "quit", "adiós", "adios", "chao"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="maripepis",
        description="Asistente de voz local (Fase 5: pulido).",
    )
    p.add_argument("--config", help="Ruta a config.toml", default=None)
    p.add_argument(
        "--backend",
        choices=["ollama", "claude", "claude-code"],
        default=None,
        help="Fuerza el motor LLM (sobrescribe config.toml)",
    )
    p.add_argument("--speak", action="store_true", help="Fuerza la voz de salida")
    p.add_argument("--no-speak", action="store_true", help="Fuerza solo texto de salida")
    p.add_argument("--listen", action="store_true", help="Escucha por micrófono (push-to-talk)")
    p.add_argument("--no-listen", action="store_true", help="Fuerza solo texto de entrada")
    p.add_argument("--handsfree", action="store_true", help="Manos libres (VAD, sin pulsar Enter)")
    p.add_argument("--no-handsfree", action="store_true", help="Desactiva el manos libres")
    p.add_argument(
        "--daemon",
        action="store_true",
        help="Demonio de la tecla de hablar (push-to-talk global; sin terminal)",
    )
    return p.parse_args(argv)


def _resolve(flag_on: bool, flag_off: bool, cfg_default) -> bool:
    if flag_off:
        return False
    if flag_on:
        return True
    return bool(cfg_default)


def _setup_voice(cfg: dict, logger):
    from .audio.player import AudioPlayer
    from .tts.factory import build_tts

    try:
        tts = build_tts(cfg)
        tts.check()
        player = AudioPlayer(device=cfg.get("audio", {}).get("output_device"))
        if not player.is_available():
            raise RuntimeError("`aplay` (alsa-utils) no está disponible")
        return tts, player
    except Exception as e:  # noqa: BLE001
        logger.warning("Voz de salida desactivada (%s). Sigo en texto.", e)
        return None, None


def _setup_listen(cfg: dict, logger):
    from .audio.recorder import AudioRecorder
    from .stt.factory import build_stt

    try:
        stt = build_stt(cfg)
        stt.check()
        audio = cfg.get("audio", {})
        recorder = AudioRecorder(
            sample_rate=audio.get("sample_rate", 16000),
            device=audio.get("input_device"),
        )
        if not recorder.is_available():
            raise RuntimeError("`arecord` (alsa-utils) no está disponible")
        logger.info("Cargando modelo Whisper '%s' (puede descargar la 1ª vez)...", stt.label)
        stt.load()
        return stt, recorder
    except Exception as e:  # noqa: BLE001
        logger.warning("Escucha por voz desactivada (%s). Escribe tu mensaje.", e)
        return None, None


def _setup_vad(cfg: dict, logger):
    from .audio.vad import VADRecorder

    v = cfg.get("vad", {})
    backend = v.get("backend", "webrtc")
    if backend != "webrtc":
        logger.warning("VAD backend %r no soportado; manos libres desactivado.", backend)
        return None
    try:
        audio = cfg.get("audio", {})
        rec = VADRecorder(
            sample_rate=audio.get("sample_rate", 16000),
            device=audio.get("input_device"),
            aggressiveness=v.get("aggressiveness", 2),
            silence_ms=v.get("silence_ms", 800),
            max_utterance_ms=v.get("max_utterance_ms", 15000),
            min_speech_ms=v.get("min_speech_ms", 300),
        )
        rec.check()
        return rec
    except Exception as e:  # noqa: BLE001
        logger.warning("Manos libres desactivado (%s). Uso Enter para grabar.", e)
        return None


def _setup_stream(cfg: dict, logger):
    """Grabadora por pulsación (tecla de hablar). None si no se puede usar."""
    from .audio.stream import StreamRecorder

    hk = cfg.get("hotkey", {})
    v = cfg.get("vad", {})
    try:
        audio = cfg.get("audio", {})
        rec = StreamRecorder(
            sample_rate=audio.get("sample_rate", 16000),
            device=audio.get("input_device"),
            aggressiveness=hk.get("aggressiveness", v.get("aggressiveness", 2)),
            silence_ms=hk.get("silence_ms", 2500),
            max_ms=hk.get("max_ms", 60000),
            min_speech_ms=hk.get("min_speech_ms", 300),
        )
        rec.check()
        return rec
    except Exception as e:  # noqa: BLE001
        logger.error("No puedo grabar por la tecla (%s).", e)
        return None


def _voice_turn(recorder_call, stt, logger, wake_word, exit_set) -> str | None:
    try:
        wav = recorder_call()
    except KeyboardInterrupt:
        return None
    except Exception as e:  # noqa: BLE001
        logger.error("Fallo en la captura: %s", e)
        return ""
    if wav is None:
        return ""
    try:
        text = stt.transcribe(wav)
    except Exception as e:  # noqa: BLE001
        logger.error("Fallo transcribiendo: %s", e)
        return ""
    if not text:
        print("   (no te he entendido, inténtalo de nuevo)")
        return ""
    if is_exit(text, exit_set):
        return None
    matched, command = strip_wake_word(text, wake_word)
    if not matched:
        return ""  # no me hablaban a mí
    if not command:
        print("   (¿sí? dime)")
        return ""
    print(f"🗣️  (voz) > {command}")
    return command


def _get_user_turn(*, stt, recorder, vad_recorder, wake_word, exit_set, logger) -> str | None:
    if vad_recorder and stt:
        print("🎧 Escuchando...")
        return _voice_turn(vad_recorder.record_utterance, stt, logger, wake_word, exit_set)

    prompt = (
        "\n⏎ Enter para hablar · o escribe texto · 'salir' > "
        if (stt and recorder)
        else "\n🗣️  tú > "
    )
    try:
        raw = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if is_exit(raw, exit_set):
        return None
    if raw:
        return raw
    if not (stt and recorder):
        return ""

    print("🎙️  Grabando... pulsa Enter para parar.")
    return _voice_turn(recorder.record_until_enter, stt, logger, wake_word, exit_set)


def run_chat(provider, conversation: Conversation, logger, *,
             speech=None, stt=None, recorder=None, vad_recorder=None,
             wake_word="", exit_set=None, tools=None, execute=None):
    exit_set = exit_set or {normalize(w) for w in _DEFAULT_EXITS}

    if vad_recorder and stt:
        escucha = f"manos libres · {stt.label}"
        footer = "Di 'salir' o pulsa Ctrl-C para terminar."
    elif stt and recorder:
        escucha = f"Enter (push-to-talk) · {stt.label}"
        footer = "Escribe/di 'salir' para terminar."
    else:
        escucha = "teclado"
        footer = "Escribe 'salir' para terminar."
    voz = f"activada · {speech.label}" if speech else "solo texto"
    wake = f" · palabra: '{wake_word}'" if wake_word else ""

    if tools:
        acciones = ", ".join(t.name for t in tools)
    elif not getattr(provider, "accepts_tools", True):
        acciones = "las de Claude Code"
    else:
        acciones = "ninguna"

    print("─" * 46)
    print(" 🐙 Maripepis")
    print(f" Motor LLM: {provider.label}")
    print(f" Escucha:   {escucha}{wake}")
    print(f" Voz:       {voz}")
    print(f" Acciones:  {acciones}")
    print(f" {footer}")
    print("─" * 46)

    handsfree = bool(vad_recorder and stt)

    while True:
        user_text = _get_user_turn(
            stt=stt, recorder=recorder, vad_recorder=vad_recorder,
            wake_word=wake_word, exit_set=exit_set, logger=logger,
        )
        if user_text is None:
            print("👋 ¡Hasta luego!")
            break
        if not user_text:
            continue

        # La ruta de herramientas devuelve la respuesta de golpe; la normal, token
        # a token. Las banderas distinguen una de otra para imprimir igual que
        # siempre, y para que los comandos no se metan en medio de una línea.
        printed = False      # ¿ha salido ya texto de la respuesta?
        abierto = False      # ¿está el «🤖 maripepis > » esperando en su línea?

        def _prompt() -> None:
            nonlocal abierto
            if not abierto:
                print("🤖 maripepis > ", end="", flush=True)
                abierto = True

        def _emit(tok: str) -> None:
            nonlocal printed
            _prompt()
            printed = True
            print(tok, end="", flush=True)

        def _borrar_prompt() -> None:
            """Se quita el prompt vacío: el turno se ha ido por las herramientas.

            Con herramientas, la respuesta no llega hasta el final, así que el
            prompt lleva ahí un rato sin nada detrás. Dejarlo sería una línea
            huérfana encima de los comandos.
            """
            nonlocal abierto
            if not abierto:
                return
            if sys.stdout.isatty():
                print("\r\x1b[2K", end="", flush=True)  # la línea entera, fuera
            else:
                print()                                 # a un fichero, salto y ya
            abierto = False

        def _accion(nombre: str, args: dict, resultado: str) -> None:
            """Lo que ejecuta, según lo ejecuta: si no, solo queda en el log."""
            if not printed:
                _borrar_prompt()
            print(f"   {'⚙️' if not es_fallo(resultado) else '⚠️'} "
                  f"{resumen_de_la_llamada(nombre, args)}")

        if execute is not None and hasattr(execute, "on_call"):
            execute.on_call = _accion

        _prompt()   # se enseña ya, antes de pensar: hace de «estoy en ello»
        reply = reply_turn(
            provider, conversation, user_text, logger,
            speech=speech, tools=tools, execute=execute, on_token=_emit,
        )
        if not printed and reply:
            _prompt()
            print(reply, end="")
        if abierto or printed:
            print()
        if reply is None:
            continue

        if speech:
            try:
                speech.wait()  # espera a terminar de hablar antes del siguiente turno
            except KeyboardInterrupt:
                speech.stop()  # barge-in por teclado
                if handsfree:
                    print("\n👋 ¡Hasta luego!")
                    break
                print("\n   (interrumpido)")


def _run_daemon(cfg, provider, conversation, logger, *, stt, speech, tools, execute) -> int:
    """Arranca el demonio de la tecla de hablar (no vuelve hasta que lo paras)."""
    from .hotkey.daemon import HotkeyDaemon

    if stt is None:
        logger.error("Sin motor de transcripción no hay tecla de hablar.")
        return 1

    recorder = _setup_stream(cfg, logger)
    if recorder is None:
        return 1

    daemon = HotkeyDaemon(
        cfg, provider, conversation, logger,
        stt=stt, recorder=recorder, speech=speech, tools=tools, execute=execute,
    )
    try:
        return daemon.serve()
    finally:
        if speech:
            speech.close()


def instrucciones_de_herramientas(nombres: set[str]) -> str:
    """Lo que se le añade al system prompt cuando hay acciones disponibles.

    Aparte por dos motivos: se puede probar sin arrancar nada, y así el texto
    no se queda desfasado respecto a las herramientas que hay de verdad.
    """
    instrucciones = " Tienes herramientas para abrir aplicaciones y buscar en internet."
    if "ejecutar_comando" in nombres:
        instrucciones += (
            " También puedes ejecutar comandos de zsh en el equipo del usuario"
            " (ejecutar_comando): crear, mover y borrar carpetas y ficheros,"
            " consultar el estado del sistema, git..."
        )
    if "escribir_fichero" in nombres:
        instrucciones += (
            " Y escribir texto dentro de un fichero (escribir_fichero): notas, listas,"
            " documentos. Esa, y no un `echo` ni un editor, es la de «guárdame esto»."
        )
    if "leer_fichero" in nombres:
        instrucciones += (
            " Para mirar lo que hay dentro de un fichero tienes leer_fichero, y es"
            " OBLIGATORIA antes de cambiar, corregir o ampliar uno que ya existe:"
            " léelo, y vuelve a escribirlo entero con escribir_fichero en modo"
            " «sobrescribir». No des por sabido lo que pone porque lo escribieras tú"
            " antes: en el historial está lo que dijiste, no lo que quedó en el disco."
        )
    if "consultar_tiempo" in nombres:
        instrucciones += (
            " Para el tiempo (el de hoy y hasta 3 días) usa consultar_tiempo, que trae"
            " los datos de verdad. Si te piden apuntar una previsión en un fichero,"
            " consúltala PRIMERO y escribe después: un documento con los días puestos"
            " y vacíos no vale de nada."
        )
    # Lo importante: que actúe. Sin esta orden, ante «créame una carpeta» el
    # modelo contesta con un `mkdir` para que lo escriba el usuario, que es
    # justo lo que no sirve cuando se lo estás pidiendo hablando.
    instrucciones += (
        " Si el usuario te pide algo que puedas hacer con una herramienta, HAZLO con"
        " ella y cuéntale el resultado en una frase: nunca le expliques cómo hacerlo"
        " ni le dictes comandos para que los escriba él."
        " Para preguntas de conocimiento, cálculos o charla, responde tú directamente."
        # Con el historial de por medio, un modelo pequeño deja de llamar a las
        # herramientas y se pone a narrar éxitos que no han ocurrido.
        " NUNCA digas que has hecho algo si no acabas de hacerlo con una herramienta"
        " en este mismo turno: si no la has llamado, no está hecho, y decir que sí"
        " es mentirle."
        " Si una herramienta falla o responde que NO ha hecho nada, corrígelo y"
        " vuelve a llamarla; solo si sigue fallando, díselo tal cual."
        " Si te piden otra vez algo que ya hiciste antes, vuelve a hacerlo llamando a"
        " la herramienta: que ya lo hicieras en un turno anterior no vale para este."
    )
    return instrucciones


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config)
    if args.backend:
        cfg["llm"]["backend"] = args.backend

    logger = get_logger(cfg.get("app", {}).get("log_level", "INFO"))

    try:
        provider = build_provider(cfg)
    except Exception as e:  # noqa: BLE001
        logger.error("No pude inicializar el motor LLM: %s", e)
        return 1

    speak = _resolve(args.speak, args.no_speak, cfg.get("tts", {}).get("enabled", False))
    handsfree = _resolve(args.handsfree, args.no_handsfree, cfg.get("vad", {}).get("enabled", False))
    listen = _resolve(args.listen, args.no_listen, cfg.get("stt", {}).get("enabled", False)) or handsfree

    if args.daemon:
        listen = True        # sin STT no hay tecla de hablar
        handsfree = False    # el micro solo se abre mientras pulsas
        speak = _resolve(args.speak, args.no_speak, cfg.get("hotkey", {}).get("speak", True))

    # Si la escucha va por GPU, prepara las librerías CUDA (puede reejecutar una vez).
    if listen and str(cfg.get("stt", {}).get("device", "")).startswith("cuda"):
        from .utils.cuda import ensure_cuda_libs

        ensure_cuda_libs()

    tts, player = _setup_voice(cfg, logger) if speak else (None, None)
    stt, recorder = _setup_listen(cfg, logger) if listen else (None, None)
    vad_recorder = _setup_vad(cfg, logger) if (handsfree and stt) else None

    speech = None
    if tts and player:
        from .audio.speech import SpeechWorker

        speech = SpeechWorker(tts, player, logger)

    app = cfg.get("app", {})
    wake_word = app.get("wake_word", "")
    exit_set = {normalize(w) for w in _DEFAULT_EXITS}
    exit_set |= {normalize(p) for p in app.get("exit_phrases", [])}

    # Herramientas (acciones): abrir apps, buscar en internet, etc.
    tools = None
    execute = None
    acciones_on = cfg.get("tools", {}).get("enabled", True)
    if acciones_on and not provider.accepts_tools:
        logger.info(
            "%s trae sus propias herramientas: las de maripepis quedan fuera "
            "(configúralas en [llm.claude_code] tools).", provider.label,
        )
    elif acciones_on:
        from .tools.runner import Acciones
        from .tools.system import build_default_tools

        tool_list = build_default_tools(cfg.get("tools", {}))
        execute = Acciones(tool_list, logger)

        tools = tool_list
        cfg["llm"]["system_prompt"] += instrucciones_de_herramientas(execute.nombres)

    # Memoria permanente: quién es el usuario y qué equipo tiene. Va al final del
    # system prompt, después de las herramientas, porque son datos y no órdenes.
    cfg["llm"]["system_prompt"] += load_memory(cfg, logger)

    conversation = Conversation(
        system_prompt=cfg["llm"]["system_prompt"],
        max_history=cfg["llm"].get("max_history", 10),
    )

    if args.daemon:
        return _run_daemon(
            cfg, provider, conversation, logger,
            stt=stt, speech=speech, tools=tools, execute=execute,
        )

    try:
        run_chat(
            provider, conversation, logger,
            speech=speech, stt=stt, recorder=recorder, vad_recorder=vad_recorder,
            wake_word=wake_word, exit_set=exit_set, tools=tools, execute=execute,
        )
    finally:
        if speech:
            speech.close()
    return 0
