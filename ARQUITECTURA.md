# Maripepis — Asistente de voz local con Ollama

Asistente de voz para Linux: captura audio del micrófono, lo transcribe a texto
(STT), genera una respuesta con un LLM y la reproduce por voz (TTS). El "cerebro"
es un **proveedor intercambiable**: **Ollama** (local, 100 % offline, por
defecto), **Claude por API** o **Claude con tu suscripción** (vía el CLI de
Claude Code), cambiando una sola línea de config.
La captura y la síntesis de voz son siempre locales.

---

## 1. Visión general del flujo

```
┌──────────┐   ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ Micrófono│──▶│   VAD    │──▶│    STT    │──▶│  Ollama  │──▶│   TTS    │──▶│ Altavoz  │
│ (arecord)│   │ (silencio│   │ (Whisper) │   │  (LLM)   │   │ (Piper)  │   │ (aplay)  │
│          │   │  detect) │   │  audio→txt│   │ txt→txt  │   │ txt→audio│   │          │
└──────────┘   └──────────┘   └───────────┘   └──────────┘   └──────────┘   └──────────┘
     1              2               3               4              5              6
```

1. **Captura**: se graba del micro hasta detectar fin de habla.
2. **VAD** (Voice Activity Detection): recorta silencios y decide cuándo cortar.
3. **STT** (Speech-to-Text): Whisper convierte el audio en texto.
4. **LLM**: Ollama recibe el texto + historial y genera la respuesta.
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

## 2. Elección de tecnologías (todo local en Linux)

| Etapa            | Opción recomendada            | Alternativas                              | Por qué |
|------------------|-------------------------------|-------------------------------------------|---------|
| Lenguaje         | **Python 3.11+**              | Rust, Go                                  | Ecosistema de audio/IA maduro |
| Captura audio    | **sounddevice** (PortAudio)   | `arecord`/`pyaudio`                        | API limpia, arrays NumPy |
| VAD              | **webrtcvad** o **silero-vad**| energía RMS simple                        | Corta cuando dejas de hablar |
| STT              | **faster-whisper**            | `whisper.cpp`, `openai-whisper`, `vosk`   | Rápido en CPU/GPU, buen español |
| LLM              | **Ollama** (local), **Claude** (API) o **Claude Code** (suscripción) | `gemma2`, `mistral`; otros modelos Claude | Proveedor intercambiable — ver §5 |
| TTS              | **Piper**                     | Coqui TTS, espeak-ng, Kokoro              | Voz natural, rápido, offline |
| Reproducción     | **sounddevice** / `aplay`     | `ffplay`, `paplay`                        | Consistente con la captura |
| Config           | **TOML** (`tomllib`)          | YAML, `.env`                              | Nativo en 3.11+ |

> Con GPU NVIDIA, `faster-whisper` y Ollama usan CUDA automáticamente. En CPU
> pura funciona igual, solo más lento.

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
│   │   ├── ollama_provider.py # Implementación local (HTTP :11434)
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
│       └── logging.py         # Logging con niveles
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
    ├── test_ollama_client.py
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

### `llm/base.py`, `llm/ollama_provider.py`, `llm/claude_provider.py`, `llm/factory.py`
- Definen y seleccionan el proveedor de LLM. Ambos hablan el **mismo contrato**
  (`stream_reply`) para que el resto del programa no sepa cuál está activo.
- **Streaming** en ambos: empiezan a devolver texto antes de terminar, para ir
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

## 5. Estrategia de proveedores intercambiables (Ollama ↔ Claude)

El objetivo: **cambiar de motor con una sola línea de configuración**, sin tocar
el resto del programa. Se resuelve con el patrón **Strategy + Factory**.

### Idea central

Todo el programa habla con un **contrato común** (`LLMProvider`). Existen dos
implementaciones —una para Ollama, otra para Claude— y una *factory* que
instancia la correcta leyendo `config.toml`. El `pipeline` ni se entera de cuál
está activa.

```
                         ┌───────────────────────┐
        config.toml ────▶│   factory.build()     │
     backend = "ollama"  └──────────┬────────────┘
                                    │ devuelve un LLMProvider
                 ┌──────────────────┴──────────────────┐
                 ▼                                      ▼
        ┌─────────────────┐                   ┌──────────────────┐
        │ OllamaProvider  │                   │  ClaudeProvider  │
        │  HTTP :11434    │                   │  API Anthropic   │
        └─────────────────┘                   └──────────────────┘
                 └──────────────┬───────────────────────┘
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
> alternando. El `system` va **aparte** (no como un mensaje). Este formato es el
> mínimo común denominador de ambos proveedores.

### Implementación Ollama (`llm/ollama_provider.py`)

```python
import json, httpx
from .base import LLMProvider

class OllamaProvider(LLMProvider):
    def __init__(self, host, model, temperature):
        self.host, self.model, self.temperature = host, model, temperature

    def stream_reply(self, system, messages):
        payload = {
            "model": self.model,
            # Ollama sí acepta el system como un mensaje con role "system"
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            "options": {"temperature": self.temperature},
        }
        with httpx.stream("POST", f"{self.host}/api/chat", json=payload) as r:
            for line in r.iter_lines():
                if line:
                    yield json.loads(line)["message"]["content"]
```

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
from .ollama_provider import OllamaProvider
from .claude_provider import ClaudeProvider

def build_provider(cfg) -> "LLMProvider":
    backend = cfg["llm"]["backend"]
    if backend == "ollama":
        o = cfg["llm"]["ollama"]
        return OllamaProvider(o["host"], o["model"], o["temperature"])
    if backend == "claude":
        c = cfg["llm"]["claude"]
        return ClaudeProvider(c["model"], c["max_tokens"])
    if backend == "claude-code":
        c = cfg["llm"].get("claude_code", {})
        return ClaudeCodeProvider(**c)
    raise ValueError(f"backend de LLM desconocido: {backend!r}")
```

### La diferencia que hay que normalizar

| Aspecto | Ollama | Claude (API) | Claude Code (suscripción) |
|---------|--------|--------------|---------------------------|
| `system` prompt | mensaje con `role: "system"` | parámetro `system=` aparte | `--system-prompt` |
| Orden de `messages` | flexible | **debe empezar por `user` y alternar** | no los acepta: van aplanados en el prompt |
| Streaming | líneas JSON (`/api/chat`) | `client.messages.stream().text_stream` | `stream-json` por stdout del CLI |
| Dónde corre | tu máquina | nube de Anthropic | nube de Anthropic (proceso local de por medio) |
| Coste | gratis (tu hardware) | por tokens | tu cuota de suscripción |
| Privacidad | **total, offline** | el texto transcrito **sale a la nube** | el texto transcrito **sale a la nube** |
| Credencial | host + modelo | `ANTHROPIC_API_KEY` en el entorno | login de Claude Code (`/login`) |
| Herramientas | las de maripepis | las de maripepis | **las suyas** (`accepts_tools = False`) |

El `conversation.py` mantiene el historial en el formato neutro y ya alterna
`user`/`assistant`, así que ambos proveedores lo consumen sin cambios.

> ⚠️ **Aviso de privacidad:** con `backend = "claude"` el audio (ya transcrito
> a texto) se envía a un servicio externo. Esto rompe la promesa de "100 %
> offline" del diseño base. La etapa de voz (Whisper/Piper) sigue siendo local;
> solo el "cerebro" cambia. Deja esto claro al usuario en el README.

---

## ⚙️ Acciones (herramientas / tool-calling)

El asistente puede **ejecutar acciones** (abrir apps, buscar en internet, lanzar
comandos) usando el *tool-calling* del LLM: se le describen unas herramientas y
él decide cuándo llamarlas a partir de la petición en lenguaje natural.

```
maripepis/tools/
├── base.py      # Tool: nombre, descripción, esquema, handler; to_ollama()/to_claude()
│                # + es_fallo(): el contrato de «esto NO se ha hecho»
├── system.py    # abrir_navegador, buscar_en_internet, abrir_aplicacion + build_default_tools()
├── carpetas.py  # las carpetas del usuario (XDG) y los nombres con que se piden
├── ficheros.py  # escribir_fichero: crear/añadir texto sin pasar por la shell
├── shell.py     # ejecutar_comando: zsh -lc, con veto + timeout + recorte de salida
└── runner.py    # Acciones: ejecuta por nombre, registra y recuerda si algo falló
```

- **Neutralidad de proveedor:** cada `Tool` se convierte al formato de Ollama o
  Claude. El bucle agéntico (llamar → ejecutar → repetir → texto final) vive
  **dentro** de `provider.run_tools_turn(...)`, encapsulando el hilo de mensajes
  específico de cada proveedor. El historial neutro sigue siendo solo texto.
- **Ejecución local:** los handlers lanzan procesos desligados (`xdg-open`,
  `gtk-launch`, comando directo). Sin `shell=True` (argv como lista, sin inyección).
- **Fallback:** si el modelo no soporta herramientas, se responde en texto normal.
- **Modelo:** el tool-calling exige un LLM capaz. **`qwen2.5:7b`** discrimina bien;
  `llama3.1:8b` sobre-dispara. La temperatura del turno con herramientas se baja
  (≤0.3) para decisiones más fiables.
- **Ampliar:** añade un `Tool` en `system.py` y aparece disponible automáticamente.
- **Contexto:** `[llm.ollama] context` (8192) va explícito en cada petición. El
  servidor de Ollama da 4096 a todo el mundo y el prompt con memoria y
  herramientas ya ronda los 2500: al pasarse, el contexto se recorta por el
  principio, la conversación pierde la forma que el modelo espera y responde con
  la llamada **escrita en el texto** —que acaba dicha en voz alta— o con una
  palabra suelta. Es el fallo que más se parecía a «no hace nada».
- **Llamadas rescatadas:** aun así, un 7B escribe a veces la llamada en el texto y
  Ollama la deja pasar. `rescatar_llamadas()` la saca de ahí (decodificando el
  JSON de verdad, que lleva llaves anidadas) y la ejecuta en vez de leerla.
- **Turnos mudos:** Ollama devuelve de vez en cuando contenido vacío y sin
  llamadas, y maripepis se quedaba callada. Se reintenta subiendo un poco la
  temperatura, porque repetir la misma petición igual da el mismo silencio.
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

---

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
backend = "ollama"           # ollama | claude | claude-code  ← cambia SOLO esta línea
stream = true                # imprescindible para hablar mientras se genera
max_history = 10
system_prompt = "Eres Maripepis, un asistente de voz breve, cercano y en español."

[llm.ollama]
host = "http://localhost:11434"
model = "llama3.1:8b"
temperature = 0.7

[llm.claude]
model = "claude-opus-4-8"    # o "claude-haiku-4-5" para menor latencia/coste en respuestas cortas
max_tokens = 1024            # las respuestas de voz son breves
# La clave NO va aquí: se lee de la variable de entorno ANTHROPIC_API_KEY

[tts]
engine = "piper"
voice = "models/piper/es_ES-sharvard-medium.onnx"
speed = 1.0

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
httpx              # cliente HTTP para Ollama (o el paquete `ollama`)
anthropic          # cliente oficial de Claude (solo si usas backend = "claude";
                   # backend = "claude-code" no necesita nada: usa el CLI)
piper-tts          # TTS (o invocar el binario)
```

### Servicios externos
- **Backend Ollama** (local): `ollama serve` + `ollama pull llama3.1:8b`
- **Backend Claude** (nube): exporta tu clave → `set -x ANTHROPIC_API_KEY sk-ant-...`
  (en fish) o `export ANTHROPIC_API_KEY=sk-ant-...` (bash). No hace falta Ollama.
- Modelo de voz Piper descargado en `models/piper/` (común a ambos backends)

---

## 8. Puesta en marcha

```bash
# 1. Instalar Ollama y descargar un modelo
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b

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
- [x] `base.py` + `ollama_provider.py` + `factory.py`: contrato y primer proveedor.
- [x] Bucle mínimo teclado→LLM→consola. *Valida que Ollama responde.*
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
| ¿Motor LLM? | Ollama (local, privado) vs Claude (nube, potente) | Ollama por defecto; Claude opcional vía `config.toml` |
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
  herramientas de Ollama va con `stream: false`, así que ahí la respuesta llega
  entera de una vez; la ventana lo aguanta porque el `reply` final siempre manda.

- **Rutas relativas y `cwd`**: `load_config` mira primero el directorio actual y
  `[tts].voice` se resuelve contra él. El unit fija `WorkingDirectory` **y** pasa
  `--config` absoluto; el demonio registra ambos al arrancar.
