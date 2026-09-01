# Maripepis — Asistente de voz para Linux

Asistente de voz para Linux: captura audio del micrófono, lo transcribe a texto
(STT), genera una respuesta con un LLM y la reproduce por voz (TTS). El "cerebro"
es un **proveedor intercambiable**, aunque hoy los dos que hay son el mismo
modelo por dos caminos que se pagan distinto: **Claude con tu suscripción** (vía
el CLI de Claude Code, por defecto) y **Claude por API**, cambiando una sola
línea de config. La captura y la síntesis de voz son siempre locales; el texto
no.

> Hubo un tercer proveedor, **Ollama**, que hacía todo esto sin salir del equipo.
> Se quitó, y con él la única forma de usar maripepis sin nube. Lo que queda de
> aquello son las cicatrices que se señalan por aquí abajo: las listas de frases
> de `veracidad.py`, medidas contra modelos de 7B, y `[tools]`, que existe porque
> un modelo pequeño necesitaba que se lo dieran todo hecho.

---

## 1. Visión general del flujo

```
┌──────────┐   ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ Micrófono│──▶│   VAD    │──▶│    STT    │──▶│  Claude  │──▶│   TTS    │──▶│ Altavoz  │
│ (arecord)│   │ (silencio│   │ (Whisper) │   │  (LLM)   │   │ (Piper)  │   │ (aplay)  │
│          │   │  detect) │   │  audio→txt│   │ txt→txt  │   │ txt→audio│   │          │
└──────────┘   └──────────┘   └───────────┘   └──────────┘   └──────────┘   └──────────┘
     1              2               3               4              5              6
```

1. **Captura**: se graba del micro hasta detectar fin de habla.
2. **VAD** (Voice Activity Detection): recorta silencios y decide cuándo cortar.
3. **STT** (Speech-to-Text): Whisper convierte el audio en texto.
4. **LLM**: Claude recibe el texto + historial y genera la respuesta.
5. **TTS** (Text-to-Speech): Piper convierte la respuesta en audio.
6. **Reproducción**: se reproduce por el altavoz.

Bucle continuo con opción de **palabra de activación** ("wake word") opcional.

Quién dispara el paso 1 depende del modo:

| Modo | Disparo | Corte |
|---|---|---|
| Texto | escribes | Enter |
| Push-to-talk (REPL) | Enter | Enter |
| Manos libres (`--handsfree`) | el VAD detecta que hablas | silencio |
| **Tecla de hablar (`--daemon`)** | **mantienes ALT+Z** | **sueltas** (o silencio/tope) |

---

## 2. Elección de tecnologías

| Etapa            | Opción recomendada            | Alternativas                              | Por qué |
|------------------|-------------------------------|-------------------------------------------|---------|
| Lenguaje         | **Python 3.11+**              | Rust, Go                                  | Ecosistema de audio/IA maduro |
| Captura audio    | **sounddevice** (PortAudio)   | `arecord`/`pyaudio`                        | API limpia, arrays NumPy |
| VAD              | **webrtcvad** o **silero-vad**| energía RMS simple                        | Corta cuando dejas de hablar |
| STT              | **faster-whisper**            | `whisper.cpp`, `openai-whisper`, `vosk`   | Rápido en CPU/GPU, buen español |
| LLM              | **Claude Code** (suscripción) o **Claude** (API) | otros modelos Claude | Proveedor intercambiable — ver §5 |
| TTS              | **Piper**                     | Coqui TTS, espeak-ng, Kokoro              | Voz natural, rápido, offline |
| Reproducción     | **sounddevice** / `aplay`     | `ffplay`, `paplay`                        | Consistente con la captura |
| Config           | **TOML** (`tomllib`)          | YAML, `.env`                              | Nativo en 3.11+ |

> Con GPU NVIDIA, `faster-whisper` usa CUDA automáticamente. En CPU pura
> funciona igual, solo más lento.

---

## 3. Estructura de directorios

```
maripepis/
├── ARQUITECTURA.md            # Este documento
├── README.md                  # Instalación y uso
├── pyproject.toml             # Dependencias y metadatos (o requirements.txt)
├── config.toml                # Configuración del usuario
├── .gitignore
│
├── maripepis/                 # Paquete principal
│   ├── __init__.py
│   ├── __main__.py            # Entry point: python -m maripepis
│   ├── cli.py                 # Parseo de argumentos y arranque
│   ├── config.py              # Carga y validación de config.toml
│   ├── turn.py                # Un turno de respuesta (LLM→TTS), compartido
│   │
│   ├── audio/
│   │   ├── __init__.py
│   │   ├── recorder.py        # Captura de micrófono (push-to-talk por Enter)
│   │   ├── vad.py             # Detección de actividad de voz (manos libres)
│   │   ├── stream.py          # Captura arrancable/parable (tecla de hablar)
│   │   ├── speech.py          # Worker de voz: sintetiza y reproduce en orden
│   │   └── player.py          # Reproducción de audio
│   │
│   ├── hotkey/                # Tecla de hablar global (ALT+Z en Hyprland)
│   │   ├── __init__.py
│   │   ├── protocol.py        # Órdenes JSON por socket unix
│   │   ├── client.py          # Cliente instantáneo que lanza el compositor
│   │   ├── daemon.py          # Demonio: máquina de estados + socket
│   │   ├── notify.py          # Avisos de escritorio (notify-send)
│   │   ├── window.py          # Arranque de la ventana de chat (otro Python)
│   │   └── clipboard.py       # Portapapeles de Wayland (wl-copy)
│   │
│   ├── whatsapp/              # Sesión propia de WhatsApp (solo con modo = "envio")
│   │   ├── __init__.py
│   │   ├── protocol.py        # Órdenes JSON por socket unix; valida el destino
│   │   ├── daemon.py          # Demonio: sostiene la sesión (neonize/whatsmeow)
│   │   └── cliente.py         # pedir(): tres líneas y biblioteca estándar
│   │
│   ├── hogar/                 # La casa: luces (Philips Hue por su API local)
│   │   ├── __init__.py
│   │   ├── hue.py             # Descubrir el puente, vincularse y hablarle
│   │   └── cliente.py         # maripepis-hue: vincular (con el botón) y listar
│   │
│   ├── ui/                    # Ventana de chat (proceso aparte, GTK4)
│   │   ├── __init__.py
│   │   └── chat.py            # Visor: se suscribe al socket y pinta el turno
│   │
│   ├── stt/
│   │   ├── __init__.py
│   │   └── whisper_engine.py  # Transcripción con faster-whisper
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py            # Interfaz LLMProvider (contrato común)
│   │   ├── claude_provider.py # Implementación nube (API Anthropic)
│   │   ├── claude_code_provider.py # Implementación por suscripción (CLI Claude Code)
│   │   ├── factory.py         # Elige proveedor según config.toml
│   │   └── conversation.py    # Historial y system prompt (agnóstico)
│   │
│   ├── tts/
│   │   ├── __init__.py
│   │   └── piper_engine.py    # Síntesis de voz con Piper
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logging.py         # Logging con niveles
│       └── turnos.py          # La marca del turno: qué llamadas son de la misma vuelta
│
├── models/                    # Modelos descargados (git-ignored)
│   ├── whisper/
│   └── piper/                 # p.ej. es_ES-sharvard-medium.onnx
│
├── scripts/
│   ├── setup.sh               # Instala deps del sistema y modelos
│   └── download_models.sh     # Descarga modelos Piper/Whisper
│
└── tests/
    ├── test_vad.py
    └── test_pipeline.py
```

---

## 4. Responsabilidad de cada módulo

### `audio/recorder.py`
- Abre el stream del micro a 16 kHz mono (formato que espera Whisper).
- Expone `record_until_silence()` que graba mientras hay voz.

### `audio/vad.py`
- Envuelve `webrtcvad` o `silero-vad`.
- Decide, frame a frame (10–30 ms), si hay voz.
- Corta la grabación tras N ms de silencio.

### `audio/player.py`
- Reproduce arrays de audio o WAV.
- Permite interrumpir la reproducción (barge-in) si el usuario habla.

### `stt/whisper_engine.py`
- Carga el modelo una sola vez (coste alto).
- `transcribe(audio) -> str`. Configurable: idioma (`es`), tamaño (`base`, `small`, `medium`).

### `llm/base.py`, `llm/claude_provider.py`, `llm/claude_code_provider.py`, `llm/factory.py`
- Definen y seleccionan el proveedor de LLM. Todos hablan el **mismo contrato**
  (`stream_reply`) para que el resto del programa no sepa cuál está activo.
- **Streaming** en todos: empiezan a devolver texto antes de terminar, para ir
  enviándolo al TTS y reducir la latencia percibida.
- Detalle completo en **§5 (Estrategia de proveedores intercambiables)**.

### `llm/conversation.py`
- Mantiene el historial (lista de mensajes `role`/`content`).
- Inyecta el *system prompt* (personalidad de "Maripepis").
- Trunca el contexto cuando crece demasiado.

### `tts/piper_engine.py`
- Invoca Piper (proceso o binding Python).
- `synthesize(text) -> audio`. Voz configurable por modelo `.onnx`.

### `turn.py`
- `reply_turn()`: un turno completo de respuesta — añade el texto al historial,
  pide la respuesta al LLM (con herramientas, cayendo a texto normal si el modelo
  no las soporta), la va hablando frase a frase y la guarda.
- Es el punto común entre la REPL (`cli.run_chat`) y el demonio de la tecla: lo
  que cambia entre modos es **cómo se pide el turno**, no cómo se responde.

### `memory.py`
- Memoria **permanente**: un Markdown (`memoria.md`) que se añade al system
  prompt al arrancar, con quién es el usuario y qué equipo tiene.
- Separado del `system_prompt` de `config.toml` a propósito: aquel define *cómo*
  habla; este, *qué sabe*. Y separado del historial, que se recorta con
  `max_history` y caduca con `context_timeout_s`.
- Degrada a `""` ante cualquier problema (no existe, ilegible, vacío): la memoria
  es un extra, nunca un motivo para no arrancar.
- Recorta a `max_chars` porque **viaja en cada petición** al LLM, y descarta los
  comentarios HTML: son notas para quien edita el fichero, no para el modelo.

### `audio/stream.py`
- `StreamRecorder`: graba desde `start()` hasta `request_stop()`, para la tecla
  de hablar. Hereda de `VADRecorder` y reutiliza su trato con `arecord` y su VAD.
- El VAD aquí **no dispara** la grabación (eso lo hace la tecla): solo la corta si
  te callas, como red de seguridad.

### `hotkey/`
- `protocol.py` — órdenes en una línea JSON: `start` (con modo), `stop`,
  `cancel`, `status`, `ping`.
- `client.py` — lo lanza el compositor en cada pulsación; solo biblioteca
  estándar, ~20 ms de ida y vuelta.
- `daemon.py` — máquina de estados y socket unix; mantiene Whisper cargado.
- `notify.py` / `clipboard.py` — la interfaz cuando no hay terminal.

---

## 5. Estrategia de proveedores intercambiables

El objetivo: **cambiar de motor con una sola línea de configuración**, sin tocar
el resto del programa. Se resuelve con el patrón **Strategy + Factory**.

Que hoy los dos proveedores sean Claude no hace inútil el contrato: son dos
puertas distintas al mismo modelo —una API con SDK y un CLI del que se lee la
salida— y no se parecen en nada por dentro. Una trae herramientas propias y la
otra no; una se cobra por token y la otra va con la suscripción. El contrato es
lo que hace que el resto del programa no tenga que enterarse de nada de eso.

### Idea central

Todo el programa habla con un **contrato común** (`LLMProvider`). Hay una
implementación por puerta y una *factory* que instancia la correcta leyendo
`config.toml`. El `pipeline` ni se entera de cuál está activa.

```
                            ┌───────────────────────┐
           config.toml ────▶│   factory.build()     │
    backend = "claude-code" └──────────┬────────────┘
                                       │ devuelve un LLMProvider
                 ┌─────────────────────┴─────────────┐
                 ▼                                    ▼
      ┌──────────────────────┐              ┌──────────────────┐
      │ ClaudeCodeProvider   │              │  ClaudeProvider  │
      │  CLI `claude`,       │              │  API Anthropic   │
      │  con la suscripción  │              │  (por token)     │
      └──────────────────────┘              └──────────────────┘
                 └──────────────┬───────────────────┘
                                ▼
                   stream_reply(system, messages) -> Iterator[str]
                       (mismo contrato para ambos)
```

### El contrato (`llm/base.py`)

```python
from abc import ABC, abstractmethod
from collections.abc import Iterator

class LLMProvider(ABC):
    """Recibe un historial neutro y devuelve la respuesta token a token."""

    @abstractmethod
    def stream_reply(self, system: str, messages: list[dict]) -> Iterator[str]:
        ...
```

> **Formato neutro de mensajes:** `messages` es una lista de
> `{"role": "user" | "assistant", "content": str}`, empezando por `user` y
> alternando. El `system` va **aparte** (no como un mensaje), que es como lo
> quiere Claude y como lo aceptaría cualquier otro que se añadiera.

### Implementación Claude (`llm/claude_provider.py`)

```python
import anthropic
from .base import LLMProvider

class ClaudeProvider(LLMProvider):
    def __init__(self, model, max_tokens):
        self.client = anthropic.Anthropic()   # lee ANTHROPIC_API_KEY del entorno
        self.model, self.max_tokens = model, max_tokens

    def stream_reply(self, system, messages):
        with self.client.messages.stream(
            model=self.model,          # p.ej. "claude-opus-4-8"
            max_tokens=self.max_tokens,
            system=system,             # ← Claude lleva el system FUERA de messages
            messages=messages,         # deben alternar y empezar por "user"
        ) as stream:
            yield from stream.text_stream
```

### Implementación Claude Code (`llm/claude_code_provider.py`)

Mismo modelo, otra puerta y otra factura: en vez del SDK + `ANTHROPIC_API_KEY`,
lanza el binario `claude` en modo `--print`, que ya está autenticado con la
**suscripción** del usuario. El CLI escupe `stream-json` por stdout y de ahí
salen los `text_delta`.

```python
class ClaudeCodeProvider(LLMProvider):
    accepts_tools = False              # trae las suyas; las de maripepis no le llegan

    def stream_reply(self, system, messages):
        prompt, con_historial = self.build_prompt(messages)   # historial aplanado
        proc = subprocess.Popen(self.build_args(system, con_historial), ...)
        proc.stdin.write(prompt); proc.stdin.close()
        for linea in proc.stdout:                             # una línea, un evento JSON
            ...                                               # yield de los text_delta
```

Dos costuras propias de hablar con un CLI y no con una API:

- **No hay historial estructurado:** la conversación viaja aplanada dentro del
  prompt (`Conversación previa:` + `Mensaje actual del usuario:`), con un
  `--append-system-prompt` que aclara que ese bloque es contexto. Así el
  proveedor sigue siendo una función pura de `(system, messages)`, como los
  otros, y `reset()` o el olvido por tiempo del demonio siguen valiendo.
- **No admite herramientas nuestras:** de ahí `accepts_tools = False` en
  `base.py`. `turn.py` lo mira antes de llamar a `run_tools_turn`, así que con
  este backend se va por la vía normal (que además va en streaming) y `cli.py`
  no añade al system prompt instrucciones sobre herramientas que no existen.

### La factory (`llm/factory.py`)

```python
from .claude_provider import ClaudeProvider
from .claude_code_provider import ClaudeCodeProvider

def build_provider(cfg) -> "LLMProvider":
    backend = cfg["llm"]["backend"]
    if backend == "claude":
        c = cfg["llm"]["claude"]
        return ClaudeProvider(c["model"], c["max_tokens"])
    if backend == "claude-code":
        c = cfg["llm"].get("claude_code", {})
        return ClaudeCodeProvider(**c)
    raise ValueError(f"backend de LLM desconocido: {backend!r}")
```

### La diferencia que hay que normalizar

| Aspecto | Claude (API) | Claude Code (suscripción) |
|---------|--------------|---------------------------|
| `system` prompt | parámetro `system=` aparte | `--system-prompt` |
| Orden de `messages` | **debe empezar por `user` y alternar** | no los acepta: van aplanados en el prompt |
| Streaming | `client.messages.stream().text_stream` | `stream-json` por stdout del CLI |
| Dónde corre | nube de Anthropic | nube de Anthropic (proceso local de por medio) |
| Coste | por tokens | tu cuota de suscripción |
| Privacidad | el texto transcrito **sale a la nube** | el texto transcrito **sale a la nube** |
| Credencial | `ANTHROPIC_API_KEY` en el entorno | login de Claude Code (`/login`) |
| Herramientas | las de maripepis | **las suyas** (`accepts_tools = False`) |

El `conversation.py` mantiene el historial en el formato neutro y ya alterna
`user`/`assistant`, así que ambos proveedores lo consumen sin cambios.

> ⚠️ **Aviso de privacidad:** con los dos backends, el audio (ya transcrito a
> texto) se envía a un servicio externo. La promesa de «100 % offline» del diseño
> base se rompió el día que se quitó el motor local, y hoy ya no hay forma de
> cumplirla. La etapa de voz (Whisper/Piper) sigue siendo local; el "cerebro" no.
> Queda dicho en el README, en la primera pantalla.

---

## ⚙️ Acciones (herramientas / tool-calling)

El asistente puede **ejecutar acciones** (abrir apps, buscar en internet, lanzar
comandos) usando el *tool-calling* del LLM: se le describen unas herramientas y
él decide cuándo llamarlas a partir de la petición en lenguaje natural.

```
maripepis/tools/
├── base.py      # Tool: nombre, descripción, esquema, handler; to_claude()
│                # + es_fallo(): el contrato de «esto NO se ha hecho»
├── system.py    # abrir_navegador, buscar_en_internet, abrir_aplicacion + build_default_tools()
├── carpetas.py  # las carpetas del usuario (XDG) y los nombres con que se piden
├── ficheros.py  # escribir_fichero: crear/añadir texto sin pasar por la shell
├── shell.py     # ejecutar_comando: zsh -lc, con veto + timeout + recorte de salida
├── whatsapp.py  # borrador: deja el mensaje escrito; envío: lo prepara, lo manda
│                # cuando el usuario dice que sí, y lo retira si se arrepiente
│                # (tres herramientas; personas y grupos)
├── hogar.py     # controlar_luces, estado_de_las_luces: traduce «apaga el salón»
│                # a lo que entiende el puente (ver maripepis/hogar/)
├── lanzador.py  # lanzar(): proceso desligado, en su propio scope de systemd
└── runner.py    # Acciones: ejecuta por nombre, registra y recuerda si algo falló
```

- **Neutralidad de proveedor:** cada `Tool` se convierte al formato del que las
  acepte (`to_claude()`). El bucle agéntico (llamar → ejecutar → repetir → texto
  final) vive **dentro** de `provider.run_tools_turn(...)`, encapsulando el hilo
  de mensajes específico de cada proveedor. El historial neutro sigue siendo solo
  texto.
- **Ejecución local:** los handlers lanzan procesos desligados (`xdg-open`,
  `gtk-launch`, comando directo). Sin `shell=True` (argv como lista, sin inyección).
- **Fallback:** si el modelo no soporta herramientas, se responde en texto normal.
- **Ampliar:** añade un `Tool` en `system.py` y aparece disponible automáticamente.
- **De dónde viene tanta desconfianza:** casi todo lo que rodea a las
  herramientas —el registro de `Acciones`, los desmentidos de `veracidad.py`, las
  descripciones que repiten «no digas que lo has hecho si no lo has hecho»— se
  midió contra modelos locales de 7B, que narraban el éxito de acciones que no
  habían ejecutado. Ese motor ya no está y con Claude pasa mucho menos, pero se
  queda: quien escucha sigue sin ver la pantalla, y ahí una mentira no se
  distingue de que funcione.
- **Actuar, no explicar:** el *system prompt* de las herramientas ordena usarlas y
  contar el resultado. Sin esa frase, ante «créame una carpeta» el modelo contesta
  con un `mkdir` **para que lo escriba el usuario** — inútil si lo estás pidiendo
  hablando. La orden va en `cli.py`, junto a la lista de herramientas, no en
  `config.toml`: ahí solo vive el tono.

### `carpetas.py`: dónde está «descargas» de verdad

Un modelo pequeño escribe `~/Downloads`, `~/Desktop` y `~/Documents` porque es lo
que ha visto un millón de veces. En un sistema en español son carpetas nuevas y
vacías al lado de las de verdad: el fichero existe, pero donde no lo busca nadie,
que para quien lo pidió hablando es igual de inútil que no haberlo hecho.

- `carpetas()` lee `~/.config/user-dirs.dirs` (XDG, lo mismo que ve el gestor de
  archivos), con los nombres ingleses como último recurso.
- `resolver()` / `resolver_ruta()` entienden el nombre hablado («descargas», «el
  escritorio»), las variables que `Path.expanduser()` no toca (`$HOME/x`) y las
  rutas relativas, que cuelgan del *home* y no del `cwd` — como demonio, el
  directorio actual lo pone systemd y no significa nada para quien habla.
- `traducir_rutas()` corrige el comando, pero **solo lo inventado**: si esa
  carpeta inglesa existe y tiene algo dentro, es del usuario y no se toca.
- `descripcion()` mete las rutas reales en la descripción de las herramientas: es
  más barato decírselo al modelo que corregirle cada comando.

### `ficheros.py`: escribir sin pasar por la shell

`escribir_fichero` existe porque `echo … > fichero` es un campo de minas de
comillas, acentos y saltos de línea — y el modelo, viéndolo venir, se escaqueaba:
en vez de escribir abría un editor y te contaba lo que tenías que teclear tú. Con
una herramienta propia el contenido viaja como un argumento más. No sobrescribe
sin permiso: basta con que el micrófono entienda «notas» donde dijiste «notitas».

### `runner.py`: lo que pasó de verdad

Un 7B lee «NO he ejecutado nada» y remata el turno con un «ya lo tienes». Por
escrito cantaría; dicho en voz alta, y sin ver la pantalla, no hay forma de
distinguir esa mentira de que funcione. `Acciones` guarda el resultado real de la
última herramienta y `turn.py` añade el desmentido si la respuesta no lo
reconoce. El prompt ya lo pedía; con un modelo pequeño, pedirlo no basta.

Ese mismo sitio es el que sabe lo que se ha ejecutado, así que de ahí sale
también lo que se enseña: `Acciones.on_call(nombre, args, resultado)` es un
espectador opcional al que se avisa de cada llamada. Lo engancha quien vaya a
pintarlo —la REPL (`cli.py`) y el demonio, que lo difunde como evento `tool` a
la ventana de chat—, y `resumen_de_la_llamada()` deja la línea legible: la orden
(`ejecutar_comando · mkdir -p ~/fotos`), no el JSON de la llamada. Mira, no
toca: lo que devuelva no cambia nada y, si revienta, la acción sigue su curso
(una ventana caída no puede tumbar un turno).

### `shell.py`: la herramienta con más alcance

`ejecutar_comando` lanza `zsh -lc <orden>` desde el *home* (o desde `directorio`,
si existe: un directorio inventado **degrada al home en vez de cancelar** el
comando, que suele traer rutas absolutas y habría funcionado igual)
y devuelve la salida resumida. El comando lo decide el LLM a partir de una
transcripción de voz, así que lleva tres redes, configurables en `[tools.shell]`:

- **Veto** (`guard`): expresiones regulares contra lo irreversible — `rm` de `/`
  o del *home*, `mkfs`, `dd of=/dev/…`, `curl | sh`. Deja pasar lo cotidiano
  (borrar *una* carpeta tuya, apagar el equipo): es una red para catástrofes, no
  una lista blanca. `veto()` es pura y se prueba sola, sin ejecutar nada.
- **Tiempo** (`timeout_s`): `start_new_session=True` + `killpg` en el `TimeoutExpired`.
  `communicate(timeout=…)` solo mata al proceso directo, no a sus hijos.
- **Salida** (`max_output_chars`): recorte antes de que viaje al LLM — y de que
  acabe leída en voz alta.

Además: `stdin` a `/dev/null` (lo interactivo falla rápido en vez de robarle el
teclado a la REPL) y `-l` sin `-i`, que da el `PATH` de `~/.zprofile` pero no
`~/.zshrc`, con su prompt y sus alias. Como `abrir_aplicacion`, **nunca devuelve
éxito sin comprobarlo**: el código de salida va en la respuesta.

### `whatsapp.py`: llegar hasta el borde y parar

`preparar_mensaje_whatsapp` abre el chat de un contacto en ZapZap con el mensaje
**escrito en el cuadro de texto**. No lo envía, y esa es la decisión de diseño,
no una limitación: se podría (un Enter sintético con `hl.dsp.send_shortcut` sobre
la ventana, o el puerto de depuración del WebEngine). Es la única acción de todas
las de Maripepis que **sale del equipo y le llega a otra persona**, y la única que
no se deshace. Un fallo del micrófono en `ejecutar_comando` te crea una carpeta
rara; aquí te manda un mensaje a quien no era. El Enter lo da quien está delante,
que además es el momento en que mira la pantalla.

Por dentro es la puerta oficial de ZapZap, no automatización de ventanas:

```
maripepis ──► zapzap "whatsapp://send?phone=…&text=…"
                 │
                 └─ SingleApplication: ¿hay instancia? ──► socket ──► la instancia viva
                                                                        │
                       MainWindow.xdgOpenChat ──► <a href="…">.click() ─┘
                                                   dentro de WhatsApp Web
```

Tres cosas que no son evidentes y que se ven en el código de ZapZap:

- **Con ZapZap cerrado el enlace se pierde.** Su `SingleApplication` solo mira
  `argv` en la rama de «ya hay otra instancia»; arrancando de cero lo ignora sin
  decir nada. Por eso `zapzap_abierto()` se conecta a su socket antes —igual que
  hace ZapZap consigo mismo— y, si no hay nadie, se abre la aplicación y se dice
  que el mensaje **no** se ha escrito, en vez de darlo por hecho.
- **La URL acaba dentro de una cadena de JavaScript** (`a.href="<url>"`, en
  `PageController.xdg_open_chat`). El texto lo escribe un LLM a partir de lo que
  ha entendido un micrófono, así que va con `quote(safe="")`: una comilla sin
  codificar no sería una comilla, sería código en la sesión de WhatsApp del
  usuario.
- **La agenda tiene que ser nuestra.** La libreta de WhatsApp vive dentro de la
  sesión del navegador empotrado y desde fuera no se lee. Los nombres salen de
  `~/.config/maripepis/contactos.toml` —fuera del repositorio: son teléfonos de
  otra gente— y al modelo se le pasan **los nombres, nunca los números**, porque
  esa descripción viaja en cada petición y con el backend de Claude eso es la nube.

Ante la duda, pregunta: dos contactos que encajan con «Marta» no se resuelven por
orden alfabético, se devuelven los dos para que el asistente pregunte. Y la
comparación es por palabras enteras, para que «Ana» no encaje nunca con «Juana».

**Y una segunda entrada, por la shell.** Los proveedores que traen sus propias
herramientas (`claude_code_provider.accepts_tools = False`) no reciben ninguna de
las nuestras, así que WhatsApp les llega como una orden: `maripepis-whatsapp`
(`tools/whatsapp.py:main`, declarada en `[project.scripts]`), que `cli.py` les mete
en el *system prompt* si tienen `Bash`. Se les da **la orden, no la receta**: con
la receta —«ZapZap entiende enlaces `whatsapp://`»— el modelo se monta el enlace a
mano con un teléfono inventado y se salta la agenda, la desambiguación y el «NO
está enviado». Llamando a la orden lee exactamente lo mismo que leería como
herramienta, y el código de salida (1 si no se ha escrito nada) sirve para un
`Bash` que solo mire eso.

El desmentido tiene aquí una rama propia (`veracidad.desmiente_envio`), y lo que
mira es **qué herramienta se llamó**: con `preparar_mensaje_whatsapp`, «ya se lo he
mandado» es mentira y se desmiente en voz alta; con `enviar_mensaje_whatsapp` es
verdad y hay que callarse. Por eso son dos nombres y no uno con un interruptor:
esa pregunta no se podría contestar con un solo nombre, y equivocarse duele en las
dos direcciones — quien oye la mentira se queda esperando una respuesta a un
mensaje que sigue en el cuadro de texto, y quien oye el desmentido de más manda el
wasap dos veces. Va fuera de `lo_que_no_ha_hecho` porque no es el mismo caso:
aquella habla de herramientas que hacían falta y no se llamaron, y aquí la
herramienta se llamó y salió bien — lo que falla es lo que el modelo cuenta.

Y el desmentido dice además **dónde está el mensaje**, que en cada modo es un sitio
distinto y le cambia al usuario lo que tiene que hacer: «te lo he dejado escrito en
el chat, dale a enviar» en borrador, «lo tengo preparado, dime que sí» en envío.
Eso se sabe sin que la configuración llegue a `veracidad`: mira qué herramientas
hay puestas (`execute.nombres`), y `enviar_mensaje_whatsapp` solo existe en envío.

#### Y cuando sí envía: `modo = "envio"`

El modo por defecto es el de arriba y lo seguirá siendo. Pero la biblioteca que
habla el protocolo de WhatsApp (`neonize`, bindings de *whatsmeow*) permite tener
sesión propia, y con ella el mensaje sale de verdad — y los grupos, que un enlace
`whatsapp://` nunca pudo abrir porque no tienen teléfono, solo un identificador
que se ve desde dentro de la sesión.

Eso obliga a un proceso aparte, y no por comodidad: **`connect()` bloquea el hilo
y no vuelve nunca**, ni cerrando la conexión desde sus propios callbacks. No
existe el «me conecto, mando y cierro». De ahí `maripepis/whatsapp/`, con el mismo
reparto que `hotkey/` y por el mismo motivo: algo caro de arrancar que conviene
tener siempre puesto.

```
tools/whatsapp.py ──► socket unix ──► whatsapp/daemon.py ──► WhatsApp
  (agenda, «ante la           │         │
   duda pregunta»,            │         ├─ hilo principal: accept() y señales
   tope de texto)             │         └─ hilo aparte:    la sesión, bloqueada
                              │                            para siempre
                     $XDG_RUNTIME_DIR/maripepis-whatsapp.sock
```

Los hilos van así de propósito. Si la sesión se quedara con el principal, Python
no volvería a mirar una señal, y todo `systemctl stop` acabaría en SIGKILL tras el
`TimeoutStopSec`. Con el socket en el principal, el `accept()` se interrumpe y el
cierre es limpio; al final se sale con `os._exit` porque al hilo de la sesión, ya
dentro de código Go, no hay quien lo despierte.

De la herramienta cambia sorprendentemente poco: **las barandillas son las
mismas**. La agenda, el «ante la duda pregunta», la comparación por palabras
enteras y el tope de texto viven en `a_quien_y_que()`, que comparten los dos
modos; lo único que cambia es el destino final. Equivocarse de persona es el mismo
error se envíe o se deje escrito.

Lo que sí hay que reponer es el Enter. Sin pantalla que mirar, el freno se muda a
la conversación y son **dos herramientas y dos turnos**:

```
turno 1  preparar_mensaje_whatsapp(contacto, texto) ──► $XDG_RUNTIME_DIR/…-pendiente.json
           └─ «Preparado y SIN ENVIAR: va a Edu y dice …»      {nombre, teléfono,
              → el modelo se lo lee y pregunta                   texto, turno, creado}
         ─────────────  el usuario dice que sí  ─────────────
turno 2  enviar_mensaje_whatsapp()  ──► olvidar el pendiente ──► el demonio ──► WhatsApp
           (sin argumentos: solo puede soltar lo que ya estaba)

luego    borrar_mensaje_whatsapp()  ──► ¿hay pendiente? ──► se tira, no sale nada
           («bórralo», sin confirmar)      si no        ──► el demonio: revocar
```

Tres decisiones sostienen eso, y ninguna es cosmética:

- **La de confirmar no lleva argumentos.** Ni destinatario ni texto: `properties`
  vacío y `additionalProperties: false`. Es lo que impide que el modelo se invente
  un envío — solo puede soltar lo que él mismo redactó y el usuario acaba de oír—,
  y si aun así mete argumentos y no cuadran con lo guardado, se niega en vez de
  mandar una cosa mientras anuncia otra.
- **Los dos pasos tienen que venir de turnos distintos.** Un 7B lee «léeselo y
  espera» y encadena las dos llamadas en la misma vuelta; si eso colara, la
  confirmación sería un adorno. La marca del turno vive en `utils/turnos.py`, la
  estrena `Acciones.reset()` —que es donde ya estaba escrito qué es un turno: lo
  que se olvida— y viaja a la shell de Claude Code en `MARIPEPIS_TURNO`, que
  hereda todo lo que lance con su `Bash`. Entre dos turnos solo se pasa hablando.
- **El pendiente es un fichero, no una variable.** Porque los dos caminos que
  llegan aquí no comparten proceso: las herramientas viven dentro del demonio de
  maripepis, pero Claude Code ejecuta la orden con su `Bash`, y ahí cada paso es
  un proceso nuevo. Va en `$XDG_RUNTIME_DIR` (tmpfs, 0700, se lo lleva la sesión),
  en 0600 desde el `open`, y caduca al minuto: un pendiente viejo es un mensaje
  que ya no sabes si es el que te leyeron.

Y se olvida **antes** de mandarlo, no después: entre las dos formas de fallar —que
no salga y haya que dictarlo otra vez, o que salga dos veces— solo una tiene
arreglo.

Y obliga a distinguir un turno que en realidad ha ido bien: uno que acaba en «le
mando esto a Edu, ¿te parece?» tiene la forma exacta del turno dejado a medias, de
la que desconfía todo `veracidad.py`. Dos funciones marcan la diferencia —
`espera_confirmacion` (hay un mensaje redactado esperando) y
`confirmacion_prematura` (se intentó confirmar y se dijo que no)—, y con ellas el
turno no se da por averiado: si no, el usuario oiría su propia pregunta seguida de
un «en realidad no ha funcionado» que le haría pensar que el wasap se ha perdido.

Al otro lado del envío hay una tercera herramienta, `borrar_mensaje_whatsapp`,
que es el «no, espera». Tampoco lleva argumentos —siempre es lo último— y hace
dos cosas que el usuario no tiene por qué distinguir: si hay un pendiente vivo lo
tira y no llega a salir nada; si no, le pide al demonio el «eliminar para todos»
(`accion: "revocar"`), que WhatsApp solo permite durante un rato y que cuando
dice que no, se cuenta tal cual. **Aquí no hay confirmación de dos turnos, y es a
propósito:** retirar es el lado seguro de los dos, hacerle caso de más cuesta
volver a dictar un mensaje y hacerle caso de menos deja puesto uno que no querías.
El único freno que se conserva es el de siempre —sin argumentos, el modelo no
puede ponerse a retirar mensajes que no ha mandado él—.

Eso obliga a un apunte más en `veracidad.py`: «he borrado» exigía
`ejecutar_comando`, así que un borrado correcto se desmentía solo con un «no he
ejecutado ningún comando». Y un turno que retira habla de un mensaje enviado («he
borrado el mensaje enviado a Edu») sin estar afirmando que acabe de enviarlo, así
que `desmiente_envio` se calla cuando el borrado salió bien.

##### Grupos

Un grupo no tiene teléfono: tiene un identificador (`120363…@g.us`) que **solo se
ve desde dentro de la sesión**. De ahí salen las tres decisiones:

- **Se apuntan a mano**, en una sección `[grupos]` del mismo `contactos.toml`, y
  se validan con `protocol.es_grupo` en vez de con `numero()`.
- **No se listan solos.** `maripepis-wa grupos <filtro>` los busca desde la
  terminal para copiar el identificador, y el filtro es obligatorio: esta cuenta
  está en 269 grupos, y esa lista acabaría en la descripción de la herramienta,
  o sea viajando al modelo en cada frase. No hay ninguna herramienta que le deje
  al modelo descubrir grupos: solo escribe a lo que esté apuntado.
- **Personas y grupos se buscan juntos pero no se mezclan.**
  `buscar_en_la_libreta` concatena las dos búsquedas en vez de fundir los dos
  diccionarios, porque fundirlos dejaría uno solo cuando coinciden los nombres —
  y el que se pierde en silencio es un mensaje que acaba en el sitio equivocado,
  con la diferencia de que de un grupo lo leen doce. Coincidir es una duda, y una
  duda se pregunta.

En modo borrador no existen: un enlace `whatsapp://send` lleva un teléfono y no
hay ninguno que poner, así que ni se le nombran al modelo (`descripcion` los
omite) y, si aun así lo intenta, se le dice qué es lo que falta y dónde está la
salida. Y en todo lo que se lee en voz alta, un grupo se nombra como tal —«va
**al grupo** Familia»—, que sin ver la pantalla es la única forma de notar la
diferencia.

Tres detalles del demonio que costaron medirlos:

- **La tabla `whatsmeow_device` con cero filas es «no vinculado»**, y se consulta
  por SQLite sin tocar la red. Sin eso, arrancar sin sesión deja al servicio
  esperando un QR que nadie va a escanear.
- **El fichero de sesión lo crea la parte Go en 644**, a mitad de `connect()`,
  cuando ya no hay dónde meter un `chmod`. Solo llega a tiempo `umask(0o077)`, y
  ese fichero es el WhatsApp entero del usuario.
- **`grupos` sin filtro devuelve el recuento y nada más**, por lo que se explica
  arriba. El filtro no es una comodidad de la orden: es lo que impide que exista
  la lista entera en algún sitio del que pueda acabar viajando al modelo.

---

### `hogar.py`: hablarle a la bombilla, no a la nube

La petición fue *"conéctate a mi cuenta de Google y controla el Google Home"*.
No se puede, y merece quedar escrito para que no se vuelva a intentar:

| Vía de Google | Estado |
|---|---|
| SDK de Google Assistant | Cerrada en 2023. Era lo único que dejaba mandar «apaga la luz» por código |
| Smart Device Management | Viva, pero solo llega a los Nest de la propia Google, y con registro de pago |
| Home APIs (2024-25) | SDKs de Android y de iOS, con verificación de marca |
| Home Graph API | Para fabricantes de dispositivos |

Ninguna sirve para un proceso Python en Linux. La alternativa no es un apaño: es
mejor que lo que se pedía. La API local del puente Hue no sale a internet, no
tiene token que caduque y responde en milisegundos — y eso último no es un
detalle de rendimiento, es la diferencia entre que la luz se apague mientras
acabas la frase o tres segundos después, con alguien mirando la lámpara.

El reparto es el mismo que en WhatsApp, y por el mismo motivo: `hogar/hue.py`
sabe de recursos, uuids y coordenadas CIE; `tools/hogar.py` solo traduce lo que
se dice hablando. Entre los dos hay una frontera limpia, que es la que permitirá
meter altavoces Cast o enchufes al lado de `hue.py` sin tocar la traducción.

Tres decisiones que no se ven en el código y sí en el uso:

- **Los grupos ganan a las bombillas.** Con una habitación «Salón» y una bombilla
  «Salón», *"apaga el salón"* apaga la habitación. Acertar en lo que la persona
  está mirando importa más que ser coherente con el orden de búsqueda.
- **Pedir brillo enciende.** Atenuar una luz apagada no se ve. Y *"ponlo a cero"*
  apaga de verdad, aunque en el puente brillo 0 sea «al mínimo, pero dada».
- **Dos caminos, un solo texto.** Con `claude-code` las herramientas no llegan al
  modelo, así que las luces van por `maripepis-hue luz ...` con su Bash. Esa orden
  no reimplementa nada: llama a `controlar_luces` y escribe su resultado tal cual,
  de modo que el modelo lee lo mismo por los dos caminos —incluida la lista de
  sitios cuando se inventa uno— y solo hay un sitio donde equivocarse.
- **Aquí no se confirma nada**, al revés que WhatsApp. Encender una luz se
  deshace apagándola, se ve desde donde estás y no le llega a nadie más. La
  confirmación hablada es cara —cuesta un turno entero— y hay que gastarla solo
  donde no hay vuelta atrás.

## ⌨️ Tecla de hablar (push-to-talk global)

Mantienes **ALT+Z**, hablas, sueltas. **ALT+SHIFT+Z** dicta al portapapeles.

```
ALT+Z ▼ ──► maripepis-hotkey start assistant ──┐
ALT+Z ▲ ──► maripepis-hotkey stop ─────────────┤  $XDG_RUNTIME_DIR/maripepis.sock
              (~20 ms, solo stdlib)            ▼
                    ┌──────────────────────────────────────────────┐
                    │  HotkeyDaemon (systemd --user)               │
                    │  Whisper ya cargado en la GPU (~2 GB)        │
                    │  StreamRecorder → stt → turn.reply_turn      │
                    │                        └► dictado: wl-copy   │
                    └──────────────────────────────────────────────┘
```

### Por qué dos procesos

Cargar `large-v3-turbo` cuesta segundos y ~2 GB de VRAM. Hacerlo en cada
pulsación es inviable, así que el modelo vive en un demonio permanente y la tecla
solo manda un mensaje. El cliente no importa `config`, ni Whisper, ni `httpx`:
ese es todo su presupuesto de latencia.

### Socket unix, no PID + señales

| | Socket unix | PID file + señales |
|---|---|---|
| Verbos | los que quieras, con parámetros | ~2 bits (`SIGUSR1`/`SIGUSR2`) |
| Modo (asistente/dictado) | va en el mensaje | haría falta un fichero aparte → carrera |
| Respuesta | sí: el cliente sabe si estaba ocupado o muerto | no |
| Rastro rancio | inerte (`ECONNREFUSED`) | **peligroso**: el PID puede estar reciclado |
| Depuración | `socat - UNIX-CONNECT:…` | — |

### Máquina de estados

```
LOADING ──▶ IDLE ⇄ RECORDING ──▶ PROCESSING ──▶ SPEAKING ──▶ IDLE
              ▲         │ cancel        │ error        │ ALT+Z (barge-in)
              └─────────┴───────────────┴──────────────┘
```

Separar `PROCESSING` de `SPEAKING` da dos cosas: pulsar mientras responde la
calla y empieza a escuchar, y la voz de Piper no se cuela en la grabación (no hay
cancelación de eco).

El bucle que atiende el socket **nunca trabaja**: bajo un cerrojo cambia de
estado y contesta. Todo lo lento va en un único hilo de turno, marcado con un
número de generación para que un turno superado no pise el estado del nuevo.

```json
→ {"cmd": "start", "mode": "assistant"}
← {"ok": true, "state": "recording"}
→ {"cmd": "stop"}
← {"ok": true, "state": "recording"}
```

`stop` **no lleva modo**: lo recuerda el demonio. Así, soltar ALT+SHIFT+Z en dos
tiempos (primero SHIFT) no confunde un dictado con una pregunta.

### Red de seguridad (tres capas)

Con un atajo con modificador, soltar ALT antes que Z puede hacer que Hyprland no
dispare el bind de release y el `stop` no llegue nunca. Por eso:

1. corte por silencio (`silence_ms`, 2,5 s — más largo que en manos libres,
   porque con la tecla pulsada la gente hace pausas);
2. tope duro (`max_ms`, 1 min);
3. `stop` en reposo es un **no-op silencioso**, así el caso se autorepara.

> No usar `{ ignore_mods = true }` en el bind de soltar: haría que teclear una
> "z" en cualquier aplicación lanzara un proceso Python.

### Palabra de activación y frases de salida: aquí no

Pulsar la tecla ya es dirigirse a ella. Con `[app].wake_word` puesto, aplicarla
descartaría en silencio cada frase (una tecla muerta imposible de diagnosticar), y
`is_exit` haría que decir "hasta luego" matara el demonio. El historial **sí** se
mantiene entre pulsaciones, con reinicio por inactividad (`context_timeout_s`).

---

## 6. Configuración (`config.toml`)

```toml
[audio]
sample_rate = 16000
input_device = "default"      # o índice numérico
output_device = "default"

[vad]
backend = "webrtc"            # webrtc | silero | energy
aggressiveness = 2            # 0-3 (solo webrtc)
silence_ms = 800             # silencio para cortar

[stt]
model = "small"              # tiny | base | small | medium | large-v3
language = "es"
device = "auto"              # auto | cpu | cuda
compute_type = "int8"        # int8 | float16 | float32

[llm]
backend = "claude-code"      # claude-code | claude  ← cambia SOLO esta línea
stream = true                # imprescindible para hablar mientras se genera
max_history = 10
system_prompt = "Eres Maripepis, un asistente de voz breve, cercano y en español."

[llm.claude]
model = "claude-opus-4-8"    # o "claude-haiku-4-5" para menor latencia/coste en respuestas cortas
max_tokens = 1024            # las respuestas de voz son breves
# La clave NO va aquí: se lee de la variable de entorno ANTHROPIC_API_KEY

[tts]
engine = "piper"
voice = "models/piper/es_ES-sharvard-medium.onnx"
# speed: opcional. Sin esta clave manda DEFAULT_SPEED (tts/piper_engine.py)

[memory]                     # datos fijos del usuario/equipo (ver memory.py)
enabled = true
path = ""                    # vacío = ~/.config/maripepis/memoria.md, o memoria.md
                             # junto al config.toml (las relativas van contra él)
max_chars = 4000             # tope: la memoria viaja en CADA petición al LLM

[hotkey]                     # tecla de hablar global (--daemon + ALT+Z)
socket = ""                  # vacío = $XDG_RUNTIME_DIR/maripepis.sock
speak = true                 # el modo asistente responde por voz
silence_ms = 2500            # red de seguridad si se pierde el "soltar"
max_ms = 60000               # tope duro de duración
min_speech_ms = 300          # voz real mínima; por debajo se descarta
aggressiveness = 2           # 0-3 del VAD
context_timeout_s = 300      # olvida la conversación tras 5 min (0 = nunca)
notify = true                # avisos de escritorio (notify-send)
notify_chars = 240           # recorte del texto en los avisos
auto_paste = false           # pegar tras dictar (ver gotchas)
paste_delay_ms = 250

[app]
wake_word = ""               # vacío = siempre escuchando
exit_phrases = ["adiós maripepis", "hasta luego"]
log_level = "INFO"
```

---

## 7. Dependencias

### Sistema (paquetes de la distro)
```bash
# Arch / CachyOS
sudo pacman -S python portaudio ffmpeg alsa-utils

# Debian / Ubuntu
sudo apt install python3 python3-venv portaudio19-dev ffmpeg alsa-utils
```

### Python (`pyproject.toml` / `requirements.txt`)
```
sounddevice        # captura y reproducción
numpy              # buffers de audio
webrtcvad          # VAD (o silero-vad + torch)
faster-whisper     # STT
httpx              # cliente HTTP (búsquedas, el tiempo)
anthropic          # cliente oficial de Claude (solo si usas backend = "claude";
                   # backend = "claude-code" no necesita nada: usa el CLI)
piper-tts          # TTS (o invocar el binario)
```

### Servicios externos
- **Backend Claude Code** (por defecto): el binario `claude` instalado y con la
  sesión iniciada (`claude`, y dentro `/login`). Ni clave ni crédito de API.
- **Backend Claude** (API): exporta tu clave → `set -x ANTHROPIC_API_KEY sk-ant-...`
  (en fish) o `export ANTHROPIC_API_KEY=sk-ant-...` (bash).
- Modelo de voz Piper descargado en `models/piper/` (común a ambos backends)

---

## 8. Puesta en marcha

```bash
# 1. Tener Claude Code instalado y con sesión (o una ANTHROPIC_API_KEY, si vas
#    por la API)
claude          # y dentro: /login

# 2. Crear entorno e instalar dependencias
python -m venv .venv
source .venv/bin/activate            # (fish: source .venv/bin/activate.fish)
pip install -e .

# 3. Descargar modelos de voz (Piper) y STT (Whisper)
./scripts/download_models.sh

# 4. Ejecutar
python -m maripepis
```

---

## 9. Roadmap de implementación (por fases)

**Fase 1 — Esqueleto (MVP en modo texto)** ✅
- [x] Estructura de paquete y `config.py`.
- [x] `base.py` + `factory.py`: el contrato y el primer proveedor (fue
      `ollama_provider.py`, que ya no está: ver la nota de la cabecera).
- [x] Bucle mínimo teclado→LLM→consola.
- [x] `claude_provider.py`: `backend = "claude"` funciona **sin tocar el bucle**.
- [x] `claude_code_provider.py`: `backend = "claude-code"`, Claude por suscripción.

**Fase 2 — Voz de salida (TTS)** ✅
- [x] `tts/piper_engine.py` + `audio/player.py` (ALSA/`aplay`).
- [x] La respuesta del LLM se reproduce por voz, con degradación a modo texto.

**Fase 3 — Voz de entrada (STT)** ✅
- [x] `audio/recorder.py` (ALSA/`arecord`) + `stt/whisper_engine.py` (faster-whisper).
- [x] Grabas con Enter, transcribe y responde por voz. *Ciclo completo manual.*

**Fase 4 — Manos libres (VAD)** ✅
- [x] `audio/vad.py`: corta la grabación automáticamente al callar (webrtcvad).
- [x] Bucle continuo sin pulsar teclas (`--handsfree`).

**Fase 5 — Pulido** ✅
- [x] Streaming del LLM al TTS (habla mientras genera) — `audio/speech.py` +
      `utils/sentences.py`, worker en segundo plano.
- [x] Wake word y frases de salida — `utils/phrases.py` (por transcripción).
- [x] Barge-in por teclado (Ctrl-C) + mecanismo `stop()` interrumpible.
      *Barge-in acústico → requiere cancelación de eco del sistema (documentado).*
- [x] Empaquetado (`pipx`, entry point) y servicio `packaging/maripepis.service`.

**Fase 6 — Tecla de hablar global (Hyprland)** ✅
- [x] `audio/stream.py`: grabación arrancable y parable por orden, con corte por
      silencio y tope de duración como red de seguridad.
- [x] `turn.py`: turno de respuesta compartido por la REPL y el demonio.
- [x] `hotkey/`: protocolo, cliente instantáneo y demonio con socket unix.
- [x] Avisos de escritorio (`notify-send`) y dictado al portapapeles (`wl-copy`).
- [x] Servicio systemd atado a `graphical-session.target` y binds ALT+Z /
      ALT+SHIFT+Z en `~/.config/hypr/bindings.lua`.

**Fase 7 — Ventana de chat (Hyprland, monitor secundario)** ✅
- [x] Canal de eventos en el protocolo: `subscribe` deja la conexión abierta y el
      demonio empuja una línea JSON por evento (`hotkey/protocol.py`).
- [x] Difusión desde el demonio, sin frenar el turno: visor que no traga, visor
      que se cae. La bienvenida lleva estado e historial.
- [x] `ui/chat.py`: ventana GTK4 en otro proceso y otro Python (el del sistema,
      el único que ve `python-gobject`), que se reengancha sola.
- [x] La abre la propia tecla (`hotkey/window.py`, con `uwsm-app`) y la coloca la
      regla `^org.maripepis.Chat$` de `~/.config/hypr/hyprland.lua`.
- [x] Las acciones se ven: evento `tool` (`Acciones.on_call` → `broadcast`) con la
      orden ejecutada y si salió, en la ventana y en la REPL.

---

## 10. Decisiones clave a considerar

| Decisión | Opciones | Recomendación inicial |
|----------|----------|-----------------------|
| ¿Motor LLM? | Claude Code (suscripción) vs Claude (API, por token) | Claude Code por defecto; la API vía `config.toml` |
| ¿STT en CPU o GPU? | `int8` CPU vs `float16` CUDA | Empieza en CPU con `small` |
| ¿Streaming LLM→TTS? | Simple (esperar todo) vs streaming por frases | Fase 1 simple, luego streaming |
| ¿Wake word? | Siempre escuchando vs "Oye Maripepis" | Siempre escuchando (más simple) |
| ¿Idioma? | Solo ES vs multiidioma | Fijar `es` al principio |
| ¿Interfaz? | CLI vs bandeja/GUI | CLI primero |

---

## 11. Puntos de atención (gotchas)

- **Formato de audio**: Whisper quiere **16 kHz, mono, PCM**. Reamostrea si tu
  micro entrega 44.1/48 kHz.
- **Carga de modelos**: Whisper y Piper tardan en cargar; hazlo **una vez** al
  arrancar, no por petición.
- **Latencia**: el mayor coste suele ser el LLM. Usa modelos 7–8B y streaming.
- **Realimentación acústica**: si el micro capta el altavoz, entra en bucle.
  Usa auriculares o implementa barge-in / silenciado durante la reproducción.
- **Permisos de audio**: verifica que el usuario está en el grupo `audio` y que
  PipeWire/PulseAudio expone el dispositivo correcto (`arecord -l`, `aplay -l`).
- **webrtcvad en Python 3.13+**: importa `pkg_resources`, que `setuptools ≥ 81`
  ya no incluye. El extra `[audio]`/`[voice]` fija `setuptools<81` para evitarlo.

Específicos de la tecla de hablar (Fase 6):

- **Soltar el modificador antes que la tecla**: Hyprland puede no disparar el
  bind de release y el `stop` no llega. Cubierto con corte por silencio y tope de
  duración; una tecla sin modificadores (F9) no tiene el problema.
- **Arranque en frío de `arecord`**: 50-150 ms hasta el primer frame, así que se
  pierden las primeras sílabas si hablas al instante. El aviso "🎙️ Grabando…"
  hace de señal de "ya puedes". Se descartó un buffer permanente: encendería el
  indicador de micro en uso y gastaría un proceso 24/7.
- **Markup de Pango en `notify-send`**: mako lo interpreta, y la transcripción es
  texto del usuario. Hay que escapar `& < >` o el aviso sale roto.
- **`PartOf=graphical-session.target`** en el unit: sin él, el demonio sobrevive
  al cierre de sesión con un `HYPRLAND_INSTANCE_SIGNATURE` caduco.
- **Orden con `ensure_cuda_libs()`**: hace `os.execv`; si el socket se enlazara
  antes, la imagen nueva heredaría el fd y creería que ya hay otro demonio. Por
  eso el `bind` vive dentro de `serve()`, después de la reejecución.
- **`wl-copy` se queda de fondo** como dueño de la selección (así funciona
  Wayland) y dentro del cgroup del servicio: parar el servicio vacía el
  portapapeles. En la práctica el gestor de portapapeles ya lo ha guardado.
- **Lo que abren las herramientas hereda el cgroup del servicio.**
  `start_new_session=True` cambia la sesión, no el cgroup, así que cualquier app
  abierta por voz moriría con `systemctl --user restart maripepis`. Por eso
  `tools/system.py::_launch()` antepone `uwsm-app --` cuando está disponible: la
  app va a su propio *scope* en `app-graphical.slice` y sobrevive.
- **Una herramienta no debe devolver éxito sin comprobarlo.** `abrir_aplicacion`
  caía en `gtk-launch` para cualquier nombre y contestaba "He intentado abrir X"
  aunque no existiera; el LLM se lo creía y le decía al usuario que ya estaba
  abierto. Ahora verifica el binario o el `.desktop` **antes** de lanzar, y si no
  está, lo dice claro para que el modelo no se lo invente.
Específicos de la ventana de chat (Fase 7):

- **GTK4 no está en el `.venv`.** `python-gobject` es un paquete del sistema y el
  entorno virtual no lo ve, así que la ventana la lanza `python3` (el del
  sistema) **por ruta**, no con `-m`: así no depende de que PYTHONPATH sobreviva
  al `uwsm-app`. De ahí que `ui/chat.py` no importe nada de `maripepis`.
- **El `app_id` de Wayland sale del *application id* de GTK**, no del `prgname`,
  y tiene que ser un nombre válido de D-Bus (con puntos): por eso la regla de
  Hyprland se ata a `org.maripepis.Chat` y no a algo como `maripepis-chat`.
- **Cerrar la ventana no se nota en la primera escritura.** El socket se queda
  con el evento en el buffer y solo falla el siguiente. Sin comprobarlo aparte
  (`select` sobre los suscriptores, donde lo único que puede llegar es el EOF),
  la pulsación siguiente creería que hay alguien mirando y no abriría nada.
- **Abrir una ventana en el otro monitor mueve el monitor activo**, aunque la
  regla lleve `no_initial_focus`: el teclado se queda sin ventana enfocada. El
  visor apunta qué monitor estaba enfocado *antes* de existir y le devuelve el
  foco al aparecer (dos intentos: `map` es «GTK ya la entregó», no «el compositor
  ya la colocó»).
- **Las herramientas del backend `claude-code` no se ven.** El evento `tool` sale
  de `Acciones`, que es quien las ejecuta; con `accepts_tools = False` las corre
  el CLI de Claude Code por su cuenta y aquí solo llega el texto. Con `[llm.claude_code]
  tools = ""` (lo de serie) no hay ninguna, así que no falta nada; si se le
  activan, habría que sacarlas de los `tool_use` del `stream-json`.
- **Los `delta` solo llegan si el turno va en streaming.** El turno de
  herramientas (backend `claude`) no va en streaming, así que ahí la respuesta
  llega entera de una vez; la ventana lo aguanta porque el `reply` final siempre
  manda.

- **Rutas relativas y `cwd`**: `load_config` mira primero el directorio actual y
  `[tts].voice` se resuelve contra él. El unit fija `WorkingDirectory` **y** pasa
  `--config` absoluto; el demonio registra ambos al arrancar.
