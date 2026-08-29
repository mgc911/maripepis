# Maripepis 🐙

Asistente de voz local para Linux con **proveedor de LLM intercambiable**:
[Ollama](https://ollama.com) (local, offline), **Claude por API** (nube, de pago
por token) o **Claude con tu suscripción** (vía Claude Code), cambiando una sola
línea de `config.toml`. Diseño completo en [`ARQUITECTURA.md`](ARQUITECTURA.md).

> **Estado:** completo + **acciones** + **tecla de hablar**. Habla mientras
> genera, palabra de activación, frases de salida, barge-in, servicio systemd, y
> **abre apps / busca en internet** por voz (tool-calling del LLM).
> Modos: **ALT+Z global (Hyprland)** · manos libres · push-to-talk · texto.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate            # fish: source .venv/bin/activate.fish

pip install -e .                     # backend Ollama (por defecto)
pip install -e ".[claude]"           # además, backend Claude por API
                                     # (el backend claude-code no necesita nada:
                                     #  usa el CLI de Claude Code)
pip install -e ".[dev]"              # además, pytest
```

## Uso (Fase 1 — chat de texto)

### Con Ollama (local, por defecto)

```bash
ollama serve                         # en otra terminal
ollama pull qwen2.5:7b               # buen tool-calling y español (por defecto)
python -m maripepis
```

### Con Claude (nube)

```bash
set -x ANTHROPIC_API_KEY sk-ant-...   # fish (bash: export ANTHROPIC_API_KEY=...)
python -m maripepis --backend claude  # o pon backend = "claude" en config.toml
```

> ⚠️ Con Claude, el texto de tus mensajes se envía a la nube de Anthropic. El
> backend Ollama es 100 % offline.

### Con tu suscripción de Claude (sin clave de API)

Si ya pagas Claude Pro/Max, no hace falta clave ni crédito de API: este backend
habla con el binario de [Claude Code](https://claude.com/claude-code), que ya
está autenticado con tu cuenta.

```bash
npm install -g @anthropic-ai/claude-code   # si no lo tienes
claude                                     # y dentro: /login  (una sola vez)

python -m maripepis --backend claude-code  # o backend = "claude-code" en config.toml
```

No necesita `pip install -e ".[claude]"`: no usa el SDK, lanza el CLI. Si tienes
`ANTHROPIC_API_KEY` en el entorno, **se ignora a propósito** — si no, el turno se
cobraría por token sin que te enteres. Para eso ya está el backend `claude`.

Se configura en `[llm.claude_code]`:

| Clave | Por defecto | Para qué |
|---|---|---|
| `cli` | `"claude"` | ejecutable de Claude Code (o ruta completa) |
| `model` | `""` | `""` = el suyo; `"sonnet"`, `"haiku"`, `"opus"`... |
| `tools` | `""` | herramientas **de Claude Code**: `""`, `"Bash,WebSearch"` o `"default"` |
| `permission_mode` | `""` | obligatorio si activas `tools`: `"bypassPermissions"`, `"acceptEdits"`, `"dontAsk"` |
| `safe_mode` | `true` | ignora CLAUDE.md, plugins, hooks y MCP del sistema |
| `timeout_s` | `120` | corta el turno si el CLI se atasca |
| `cwd` | `""` | directorio de trabajo del CLI |

> **Las acciones son otras.** Claude Code trae sus propias herramientas, y las de
> `[tools]` (abrir apps, `ejecutar_comando`) **no le llegan**: con este backend se
> desactivan y el banner pone *"Acciones: las de Claude Code"*. Si quieres que
> actúe, dale las suyas con `tools = "Bash"` + `permission_mode = "bypassPermissions"`
> — pero ojo: su `Bash` **no** pasa por el `guard` de `[tools.shell]`, así que no
> hay veto de lo irreversible. Sin herramientas (por defecto) es un asistente
> conversacional a secas, que es lo que quieres para hablar.
>
> Otros detalles: cada turno es una invocación nueva del CLI (~1-2 s de arranque)
> y el historial va aplanado dentro del prompt, no en una sesión de Claude Code.
> `safe_mode = true` importa: sin él, cada turno arrastraría el CLAUDE.md, los
> plugins y los MCP de tu sistema (decenas de miles de tokens de tu cuota).

## Cambiar de motor

Edita `config.toml`:

```toml
[llm]
backend = "ollama"   # ollama | claude | claude-code
```

O al vuelo con `--backend {ollama,claude,claude-code}`. El resto del programa no
cambia: los tres proveedores cumplen el mismo contrato (`llm/base.py`).

> **`[llm.ollama] context`** (por defecto `8192`) no es un ajuste de relleno. El
> servidor de Ollama da **4096 tokens a todo el mundo**, y aquí no llegan: solo
> el system prompt con la memoria y las descripciones de las herramientas ya
> ronda los 2500, y encima van el historial y lo que devuelven las herramientas.
> Al pasarse, el contexto se recorta por el principio, la conversación deja de
> tener la forma que el modelo espera y empieza a **decirte el comando en voz
> alta en lugar de ejecutarlo** —o a soltar una palabra suelta sin sentido—. Con
> VRAM de sobra puedes subirlo a `16384`; `0` deja lo que diga el servidor.

## Voz de salida (Fase 2 — Piper)

La reproducción usa `aplay` (ALSA). En Arch/CachyOS: `sudo pacman -S alsa-utils`.

```bash
pip install -e ".[tts]"              # instala Piper
./scripts/download_models.sh          # descarga una voz en español a models/piper/
python -m maripepis --speak           # responde por voz (o pon [tts].enabled=true)
```

Si falta Piper o el modelo, Maripepis **avisa y sigue en modo texto** (no rompe).
Desactiva la voz puntualmente con `--no-speak`.

## Voz de entrada (Fase 3 — Whisper)

La captura usa `arecord` (ALSA, del sistema). Necesitas `faster-whisper`:

```bash
pip install -e ".[stt]"               # instala faster-whisper
python -m maripepis --listen --speak  # ciclo de voz completo
```

En el bucle: **pulsa Enter para grabar**, habla, **pulsa Enter para parar** →
se transcribe y responde. También puedes escribir texto en cualquier momento.
El primer arranque descarga el modelo Whisper (`small` por defecto).

Instala todo lo de voz de una vez con `pip install -e ".[voice]"`.

## Manos libres (Fase 4 — VAD)

Con VAD (`webrtcvad`), no pulsas Enter: Maripepis escucha en continuo y corta
sola cuando dejas de hablar.

```bash
pip install -e ".[voice]"             # incluye webrtcvad + whisper + piper
python -m maripepis --handsfree --speak
```

Habla y calla; tras ~0.8 s de silencio transcribe y responde. Di **"salir"** o
pulsa **Ctrl-C** para terminar. Ajusta la sensibilidad en `config.toml`:
`[vad] aggressiveness` (0-3) y `silence_ms`.

> El ciclo es secuencial (escucha → responde → escucha), así el micro no capta
> la voz del asistente. Interrumpir hablando (*barge-in*) llegaría en una Fase 5.

## Acciones: abrir apps, buscar en internet y ejecutar comandos

Con `[tools] enabled = true` (por defecto), el asistente puede **ejecutar
acciones** cuando se lo pides — el LLM decide cuándo (tool-calling):

- *"Abre el navegador"* → abre el navegador (`xdg-open`).
- *"Abre Firefox"* / *"Abre el gestor de archivos"* → lanza la aplicación.
- *"Abre una terminal en Documentos"* → la abre **en esa carpeta**.
- *"Busca el tiempo en Madrid"* / *"Mira recetas de tortilla"* → abre la búsqueda.
- *"Créame una carpeta fotos"* / *"¿cuánto espacio me queda?"* → lo **hace** con zsh.
- *"Guárdame una nota con la lista de la compra"* → **escribe el fichero**, con lo
  que le hayas dictado dentro.
- *"¿Cuánto es 7×8?"* / *"capital de Italia"* → responde directo, sin abrir nada.

Hace las cosas en vez de explicarte cómo hacerlas: si te pide algo que puede
hacer con una herramienta, la usa y te cuenta el resultado; no te dicta comandos
para que los escribas tú.

**Tus carpetas se llaman como se llaman.** Un modelo pequeño escribe
`~/Downloads` y `~/Desktop` porque es lo que ha visto un millón de veces; en un
sistema en español eso son dos carpetas nuevas y vacías al lado de las de verdad,
y el fichero que pediste acaba donde no lo busca nadie. Maripepis lee tus
carpetas reales de `~/.config/user-dirs.dirs` (las mismas que ve el gestor de
archivos), se las cuenta al modelo y corrige las rutas que se inventa —salvo que
esa carpeta inglesa la uses de verdad y tenga algo dentro, en cuyo caso no toca
nada. Si tu escritorio es el propio *home* (`XDG_DESKTOP_DIR="$HOME/"`), lo que
pidas «en el escritorio» va ahí.

**Y ves lo que ejecuta.** Cada llamada sale escrita según pasa —en la terminal y
en la ventana de chat— con la orden tal cual y un ⚙️ o un ⚠️ según haya salido:

```
   ⚙️ ejecutar_comando · mkdir -p ~/fotos/2026
   ⚙️ escribir_fichero · compra.txt
   ⚠️ ejecutar_comando · cp a b
🤖 maripepis > Te he creado la carpeta y la lista, pero la copia ha fallado.
```

Lo dicho en voz alta es un resumen, y de un resumen no se puede auditar nada: si
la carpeta acabó donde no era, esta línea es la que lo enseña. (Del contenido de
un fichero solo sale la ruta: el documento entero no cabe en una línea.)

**Y si algo falla, te lo dice.** Un modelo de 7B lee que la herramienta ha
fallado y remata igualmente con un «ya lo tienes»: por escrito cantaría, pero
hablando, y sin ver la pantalla, no hay forma de distinguirlo. Maripepis se queda
con lo que la herramienta hizo de verdad y añade un aviso cuando la respuesta no
lo reconoce.

> **Requiere un LLM con tool-calling.** Por defecto usa **`qwen2.5:7b`** (fiable
> y bueno en español). `llama3.1:8b` también lo hace, pero **sobre-dispara**
> (abre el navegador para cualquier pregunta). Con Claude también funciona.
> Añade tus propias acciones en `maripepis/tools/system.py`.

Solo abre **aplicaciones instaladas**: si le pides una que no tienes, te lo dice
en vez de dar por hecho que la ha abierto (comprueba el binario en el `PATH` o su
`.desktop` antes de lanzar nada). Y las lanza con `uwsm-app` cuando está
disponible, para que vivan en su propio *scope* de systemd y **no se cierren al
reiniciar Maripepis**.

Entiende los nombres genéricos que se usan hablando —*"una terminal"*, *"el gestor
de archivos"*, *"el editor"*— y los resuelve con lo que tengas configurado
(`$TERMINAL`, `xdg-terminal-exec`, `$EDITOR`…), no con un programa concreto. La
ventana sale en el directorio que pidas, o en tu carpeta personal: nunca en el
directorio del servicio.

> Dónde aparece la ventana lo decide Hyprland: en el monitor que tenga el foco.
> Con `follow_mouse = 1` eso es **donde esté el ratón**, que no siempre es donde
> estás mirando. Si te sale en la otra pantalla, es eso.

### Escribir ficheros

`escribir_fichero` crea un fichero de texto con lo que le dictes, o le añade
texto al final. Existe porque hacerlo con `echo … > fichero` es un campo de minas
de comillas, acentos y saltos de línea, y el modelo, antes que arriesgarse, te
abría un editor para que lo escribieras tú.

- Entiende dónde: *"guárdalo en descargas"*, *"una nota en documentos"*.
- **No pisa nada sin permiso**: si el fichero ya existe, te pregunta si añadir al
  final o sobrescribir. Basta con que el micrófono entienda *notas* donde dijiste
  *notitas* para perder algo.
- Va con `[tools.shell] enabled`: las dos tocan tus ficheros, las dos se quitan
  juntas.

### Comandos de zsh

`ejecutar_comando` lanza la orden con `zsh -lc` **desde tu carpeta personal** (o
desde donde le digas) y te resume la salida. Es la herramienta con más alcance,
así que tiene tres redes debajo, configurables en `[tools.shell]`:

| Red | Qué hace |
|---|---|
| `guard = true` | Veta lo irreversible: `rm` de `/` o del *home*, `mkfs`, `dd of=/dev/…`, `curl \| sh`… Te lo dice y no ejecuta nada. |
| `timeout_s = 20` | Corta lo que tarde de más y **mata también a los hijos** (sesión propia). |
| `max_output_chars = 2000` | Recorta la salida antes de mandarla al LLM (y de que acabe dicha en voz alta). |

Detalles que conviene saber:

- Es una shell de **login**: coge el `PATH` de `/etc/profile` y `~/.zprofile`
  (mise, `~/.local/bin`…), pero **no lee `~/.zshrc`** — hay comandos, no alias.
- **No hay teclado detrás** (`stdin` a `/dev/null`): `sudo`, editores o cualquier
  cosa que pida datos fallan o se cortan por tiempo, en vez de colgarse.
- Cada comando queda en el registro: `journalctl --user -u maripepis | grep Ejecuto`.
- El `directorio` es una comodidad: si el modelo se inventa uno que no existe, el
  comando se ejecuta igual desde tu carpeta personal en vez de cancelarse. Antes
  se perdía la petición entera por un detalle que no habías pedido tú.
- Si no lo quieres, `[tools.shell] enabled = false` y sigues teniendo el resto.

> ⚠️ Lo que ejecuta lo decide el LLM a partir de lo que ha entendido el
> micrófono. El veto para las catástrofes; para lo demás, sigue siendo tu equipo.

> ⚠️ **Python 3.14:** `webrtcvad` importa `pkg_resources`, eliminado de
> `setuptools ≥ 81`. El extra ya fija `setuptools<81`; si lo instalaste a mano,
> ejecuta `pip install "setuptools<81"`.

## Memoria permanente

Maripepis sabe siempre quién eres y qué equipo tienes, sin que se lo repitas.
Esos datos viven en **`memoria.md`** (Markdown normal) y se añaden al system
prompt en cada arranque, así que **sobreviven a reinicios** y al olvido del
historial (`context_timeout_s`).

```markdown
## Usuario
- Se llama **Manu**. Habla español; háblale de tú.

## Su ordenador
- Sobremesa con Ryzen 7 7800X3D, RTX 5070 y 32 GB de RAM.
```

- *"¿Cómo me llamo?"* → **"Te llamas Manu."**
- *"¿Tengo Photoshop?"* → **"No, pero tienes GIMP y Pinta."**

Dónde lo busca, en este orden: `[memory].path` → `~/.config/maripepis/memoria.md`
→ `memoria.md` junto al `config.toml`. Las rutas relativas se resuelven contra el
`config.toml`, **no** contra el directorio actual (el demonio arranca desde
systemd). Tras editarlo: `systemctl --user restart maripepis`.

| Clave (`[memory]`) | Por defecto | Para qué |
|---|---|---|
| `enabled` | `true` | desactivarla sin borrar el fichero |
| `path` | `""` | ruta propia; vacío = búsqueda automática |
| `max_chars` | `4000` | recorte de seguridad (corta por línea entera) |

> **Sé breve:** la memoria viaja en **cada** petición al LLM — alarga la latencia
> y, con los backends Claude, gasta dinero o cuota de suscripción. Lo que pongas entre `<!-- -->` no se
> le manda al modelo: son notas para ti.
>
> `memoria.md` está en `.gitignore` porque son datos personales. Y con
> `backend = "claude"` o `"claude-code"` viajan a la nube en cada turno; con
> Ollama no salen del PC.

## Pulido (Fase 5)

- **Habla mientras genera:** cada frase suena en cuanto está lista, mientras el
  LLM sigue escribiendo las siguientes → menos latencia percibida.
- **Palabra de activación:** pon `[app].wake_word = "oye maripepis"`; en modo voz
  solo responde si empieza por ahí (y quita esas palabras antes de mandarlas al LLM).
- **Frases de salida:** `[app].exit_phrases`, además de "salir/adiós/chao".
- **Barge-in por teclado:** pulsa **Ctrl-C** mientras responde para cortar la voz
  (en manos libres, Ctrl-C termina). El *barge-in acústico* (interrumpir hablando)
  necesita cancelación de eco a nivel de sistema — activa el módulo `echo-cancel`
  de PipeWire/PulseAudio y usa esa fuente como `input_device`.

## Tecla de hablar (Fase 6 — push-to-talk global con Hyprland)

Mantén **ALT+Z**, habla, suelta: Maripepis transcribe y responde por voz, desde
cualquier ventana y sin terminal. Con **ALT+SHIFT+Z** dicta al portapapeles (sin
LLM ni voz), para pegar donde estabas escribiendo.

Son **dos piezas**, porque cargar Whisper tarda demasiado para hacerlo en cada
pulsación:

- un **demonio** (`--daemon`) que arranca con la sesión y mantiene el modelo
  caliente en la GPU;
- un **cliente** (`maripepis-hotkey`) que Hyprland lanza al pulsar y al soltar, y
  que solo manda una orden por un socket unix (~20 ms, no carga nada).

### Instalación

```bash
pip install -e ".[voice]"                      # genera .venv/bin/maripepis-hotkey

mkdir -p ~/.config/systemd/user                # 1. el demonio, con la sesión
cp packaging/maripepis.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now maripepis
```

2. Añade las teclas a `~/.config/hypr/bindings.lua` (ajusta la ruta si el
   proyecto no está en `~/Proyectos/maripepis`):

```lua
-- Maripepis: mantén pulsado para hablar, suelta para enviar.
local maripepis = "/home/TU_USUARIO/Proyectos/maripepis/.venv/bin/maripepis-hotkey"

o.bind("ALT + Z", "Maripepis: hablar (mantener)", maripepis .. " start assistant")
o.bind("ALT + Z", "Maripepis: enviar", maripepis .. " stop", { release = true })

o.bind("ALT + SHIFT + Z", "Maripepis: dictar al portapapeles (mantener)", maripepis .. " start dictation")
o.bind("ALT + SHIFT + Z", "Maripepis: enviar dictado", maripepis .. " stop", { release = true })
```

3. `hyprctl reload && hyprctl configerrors` (debe salir vacío).

> Con `bindings.conf` en vez de Lua, el equivalente es
> `bind = ALT, Z, exec, …start assistant` y `bindr = ALT, Z, exec, …stop`.

### Qué verás

Sin terminal, las notificaciones son la interfaz:
🎙️ Grabando… → 🧠 Transcribiendo… → 🗣️ Has dicho *(lo que entendió)* →
🐙 Maripepis *(la respuesta, que además suena por Piper)*.

Y, si la dejas activada, la **ventana de chat** en el monitor que le digas, que
no se va a los tres segundos como las notificaciones (ver más abajo).

Pulsar **ALT+Z mientras responde** la calla y empieza a escucharte otra vez
(*barge-in*). El historial se mantiene entre pulsaciones, así que puedes
encadenar («¿qué tiempo hace?» → «¿y mañana?»), y se olvida solo tras 5 minutos
sin usarlo.

### Ajustes (`[hotkey]` en `config.toml`)

| Clave | Por defecto | Para qué |
|---|---|---|
| `speak` | `true` | responder por voz en el modo asistente |
| `silence_ms` | `2500` | red de seguridad: corta si te callas ese rato |
| `max_ms` | `60000` | tope duro de duración |
| `min_speech_ms` | `300` | voz real mínima; por debajo se descarta |
| `aggressiveness` | `2` | sensibilidad del VAD (0-3) |
| `context_timeout_s` | `300` | olvidar la conversación tras N s (`0` = nunca) |
| `notify` / `notify_chars` | `true` / `240` | avisos de escritorio y su recorte |
| `window` | `true` | abrir la ventana de chat al pulsar (ver más abajo) |
| `window_python` | `""` | vacío = `python3` del sistema, el que ve GTK4 |
| `auto_paste` | `false` | además de copiar, pegar en la ventana activa |
| `socket` | `""` | vacío = `$XDG_RUNTIME_DIR/maripepis.sock` |

### Detalles que conviene saber

- **El micro solo está abierto mientras pulsas.** En reposo no hay ningún
  `arecord` corriendo (a diferencia del manos libres).
- **Si sueltas ALT antes que Z**, Hyprland puede no disparar el evento de soltar
  y el `stop` nunca llegaría. Por eso la grabación **también se corta sola** tras
  `silence_ms` de silencio y al llegar a `max_ms`. La siguiente pulsación
  funciona con normalidad. Si te molesta, usa una tecla sin modificadores (F9).
- **`auto_paste` viene desactivado** a propósito: `wtype`/`ydotool` no suelen
  estar, la única vía es `hyprctl dispatch sendshortcut`, y al soltar todavía
  tienes ALT+SHIFT pulsados. El modo de fallo es un modificador pegado que
  inutiliza el escritorio. Pega tú con **SUPER+V**.
- **Privacidad:** con `[llm] backend = "claude"` o `"claude-code"`, cada ALT+Z manda la
  transcripción a la nube. Una tecla global hace las capturas accidentales mucho
  más probables que una terminal; el backend Ollama es 100 % local.
- **VRAM:** el demonio deja `large-v3-turbo` residente (~2 GB). Para liberarla,
  `systemctl --user stop maripepis`.

### Diagnóstico

```bash
journalctl --user -u maripepis -f      # qué está haciendo
maripepis-hotkey status                # idle | recording | processing | speaking
systemctl --user restart maripepis     # tras tocar config.toml
hyprctl binds -j | jq '.[] | select(.description | test("Maripepis"))'
```

El demonio registra al arrancar qué `config.toml` está usando y desde qué
directorio: es lo primero que hay que mirar si no habla o no encuentra la voz de
Piper (`[tts].voice` es una ruta relativa).

## Ventana de chat (Fase 7 — GTK4 en el monitor secundario)

Al pulsar **ALT+Z** se abre sola una ventana con la conversación: lo que ha
entendido, los comandos que ha ejecutado, lo que ha contestado y en qué anda
(`🎙️ te escucho…`, `🧠 pensando…`, `🗣️ hablando…`). Las notificaciones de mako se van a los pocos segundos; esto se
queda, que es justo lo que hace falta cuando te dice una dirección o una cifra.

No se escribe en ella: se habla. Y **no roba el foco**, así que puedes seguir
tecleando en lo que estuvieras.

### Dónde aparece

Lo decide Hyprland, no Maripepis. La regla va en `~/.config/hypr/hyprland.lua`
(la `class` es el `app_id` de GTK):

```lua
o.window("^org.maripepis.Chat$", {
	monitor = "HDMI-A-1",     -- `hyprctl monitors` para ver los nombres
	no_initial_focus = true,
	tag = "-default-opacity",
	opacity = "1 1",
})
```

Sin regla, se abre donde tengas el foco. Con ella, en el monitor que le digas.
Aunque lleve `no_initial_focus`, abrir una ventana en el otro monitor mueve el
**monitor activo** y deja el teclado sin ventana enfocada: por eso el visor mira
qué monitor tenías antes y le devuelve el foco al aparecer (`--no-restore-focus`
lo desactiva).

### Cómo está montada

| Pieza | Qué hace |
|---|---|
| `hotkey/daemon.py` | atiende `subscribe` y empuja los eventos del turno |
| `hotkey/window.py` | abre el visor cuando no hay ninguno mirando |
| `ui/chat.py` | la ventana: GTK4, solo escucha, se reengancha sola |

Corre en **otro proceso y otro Python** a propósito: GTK4 llega por
`python-gobject`, que vive en el Python del sistema y el `.venv` no ve; y así una
ventana atascada no puede atascar un turno de voz. Para verla por dentro:

```bash
python3 maripepis/ui/chat.py --socket $XDG_RUNTIME_DIR/maripepis.sock
socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/maripepis.sock   # {"cmd": "subscribe"}
```

### Detalles que conviene saber

- **Sobrevive a `systemctl --user restart maripepis`**: se lanza con `uwsm-app`
  (su propio scope de systemd) y se reconecta sola cuando el demonio vuelve.
- **Al conectarse recibe la conversación en curso**, así que abrirla a mitad de
  charla no la deja en blanco. Cuando el contexto caduca (5 min), lo marca con
  un «conversación nueva».
- **Las acciones se ven según pasan**, en monoespaciada y antes de la respuesta
  (`⚙️ ejecutar_comando · mkdir -p ~/fotos`), con ⚠️ y en rojo si la herramienta
  dijo que no. Es lo único que hay para comprobar lo que ha tocado de verdad,
  porque de viva voz solo llega el resumen.
- **La respuesta se escribe según se genera** solo cuando el turno va en
  streaming. Con `[tools] enabled = true` y Ollama, el turno de herramientas no
  va en streaming (`stream: false`) y la respuesta aparece de una vez.
- **El dictado (ALT+SHIFT+Z) no abre ventana**: va al portapapeles y ni pasa por
  el LLM. Si la ventana ya está abierta, solo apunta que ha copiado.
- **Si la cierras, la siguiente pulsación la vuelve a abrir** (y solo una: entre
  la pulsación y GTK pasan un par de segundos).
- **Para quitarla del todo**: `[hotkey] window = false` y reinicia el demonio.

## Tests

```bash
pytest
```

## Licencia

[MIT](LICENSE) © 2026 Manu Guevara Casado. Puedes usar, modificar y distribuir
Maripepis, incluso comercialmente, conservando el aviso de copyright. Sin
garantía de ningún tipo.
