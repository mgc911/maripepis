# Maripepis 🐙

Asistente de voz para Linux. La voz, el oído y las acciones son de tu equipo;
quien piensa es **Claude**, por dos caminos que se pagan distinto: **tu
suscripción** (vía Claude Code, sin clave) o la **API por token**, cambiando una
sola línea de `config.toml`. Diseño completo en
[`ARQUITECTURA.md`](ARQUITECTURA.md).

> **Lo que sale del equipo.** Hubo un motor local (Ollama) y ya no está: hoy lo
> que dices se transcribe aquí, pero el texto viaja a la nube de Anthropic en
> los dos backends. Lo que no viaja: el audio, los teléfonos de tu agenda de
> WhatsApp (al modelo se le pasan los nombres) y nada de lo que no le cuentes.

> **Estado:** completo + **acciones** + **tecla de hablar**. Habla mientras
> genera, palabra de activación, frases de salida, barge-in, servicio systemd, y
> **abre apps / busca en internet** por voz (tool-calling del LLM).
> Modos: **ALT+Z global (Hyprland)** · manos libres · push-to-talk · texto.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate            # fish: source .venv/bin/activate.fish

pip install -e .                     # backend claude-code (por defecto): usa el
                                     # CLI de Claude Code, no necesita nada más
pip install -e ".[claude]"           # además, backend Claude por API
pip install -e ".[dev]"              # además, pytest
```

## Uso (Fase 1 — chat de texto)

### Con Claude por API

```bash
set -x ANTHROPIC_API_KEY sk-ant-...   # fish (bash: export ANTHROPIC_API_KEY=...)
python -m maripepis --backend claude  # o pon backend = "claude" en config.toml
```

> ⚠️ El texto de tus mensajes se envía a la nube de Anthropic. Aquí se paga por
> token; con la suscripción (abajo) no, y es el modo por defecto.

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
backend = "claude-code"   # claude-code | claude
```

O al vuelo con `--backend {claude-code,claude}`. El resto del programa no cambia:
los dos proveedores cumplen el mismo contrato (`llm/base.py`).

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

## Acciones: abrir apps, buscar, leer y escribir ficheros, ejecutar comandos, WhatsApp, luces

Con `[tools] enabled = true` (por defecto), el asistente puede **ejecutar
acciones** cuando se lo pides — el LLM decide cuándo (tool-calling):

- *"Abre el navegador"* → abre el navegador (`xdg-open`).
- *"Abre Firefox"* / *"Abre el gestor de archivos"* → lanza la aplicación.
- *"Abre una terminal en Documentos"* → la abre **en esa carpeta**.
- *"¿Quién inventó el teléfono?"* → lo **busca y te lo cuenta**, con el texto que
  ha encontrado (no te abre una pestaña y se calla).
- *"¿Qué tiempo va a hacer en Alicante?"* → el parte **de verdad**, hasta 3 días.
- *"Créame una carpeta fotos"* / *"¿cuánto espacio me queda?"* → lo **hace** con zsh.
- *"Guárdame una nota con la lista de la compra"* → **escribe el fichero**, con lo
  que le hayas dictado dentro.
- *"Revisa el resumen que me hiciste y actualízalo"* → lo **lee**, y lo reescribe
  con lo que haya que cambiar.
- *"Mándale un wasap a mi hermana diciendo que llego tarde"* → le abre el chat en
  WhatsApp con el mensaje **escrito**, para que le des a enviar tú. (Con sesión
  propia lo envía ella, pero antes te lee a quién va y qué pone, y espera tu «sí»
  — y si te arrepientes, *"bórralo"* lo retira.)
- *"Apaga el salón"* / *"pon la cocina al 20"* / *"las luces en rojo"* → lo hace,
  hablándole al puente Hue por la red local. Y *"¿me he dejado alguna luz dada?"*
  va a mirarlo, no lo recuerda.
- *"¿Cuánto es 7×8?"* / *"capital de Italia"* → responde directo, sin abrir nada.

Hace las cosas en vez de explicarte cómo hacerlas: si te pide algo que puede
hacer con una herramienta, la usa y te cuenta el resultado; no te dicta comandos
para que los escribas tú.

**Tus carpetas se llaman como se llaman.** El modelo escribe `~/Downloads` y
`~/Desktop` porque es lo que ha visto un millón de veces; en un
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

**Y si algo falla, te lo dice.** Un modelo lee que la herramienta ha fallado y
remata igualmente con un «ya lo tienes»: por escrito cantaría, pero hablando, y
sin ver la pantalla, no hay forma de distinguirlo. Maripepis se queda con lo que
la herramienta hizo de verdad y añade un aviso cuando la respuesta no lo
reconoce.

**Y si no ha hecho nada, también.** Hay una mentira peor que esa, y es la que
sale en una conversación larga: el modelo **no llama a ninguna herramienta** y
narra el éxito igual («he actualizado el archivo»). No hay ningún fallo que
enseñar, porque no se llegó a intentar nada, y el fichero se queda como estaba.
Maripepis cuenta las llamadas del turno: si son cero y la respuesta presume de
haber hecho algo, lo desmiente en voz alta.

> Todo esto se midió contra modelos locales de 7B, que mentían así a diario. Ese
> motor ya no está y con Claude pasa mucho menos, pero los desmentidos se quedan:
> «mucho menos» no es «nunca», y quien escucha sigue sin ver la pantalla.

> **Con `backend = "claude"`** el modelo usa estas herramientas; con
> `claude-code`, las suyas (ver arriba). Añade las tuyas en
> `maripepis/tools/system.py`.

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

### Cambiar de motor sin reiniciar

La cabecera de la ventana dice **quién está contestando** (`☁️ Claude Code` o
`☁️ Claude`). Es una etiqueta, no un mando: el motor se elige en `config.toml`, y
se cambia en caliente desde la terminal, que para eso es una orden del protocolo:

```bash
maripepis-hotkey backend claude       # o: claude-code
maripepis-hotkey status               # dice en cuál está
```

El cambio es **sin reiniciar el servicio y sin perder la conversación** —el
historial es neutro, así que se sigue por donde ibas pero con el otro motor—. Que
sirva de algo teniendo los dos el mismo modelo detrás: son dos formas de pagarlo,
y cuando se te agota una, saltas.

Tres detalles que importan:

- **Si el motor nuevo no se puede montar, no se cambia.** El backend `claude`
  necesita `ANTHROPIC_API_KEY`; sin ella el proveedor ni llega a existir, el
  viejo sigue en su sitio y la ventana te dice por qué. Antes esto se descubría
  al hablar, que es justo cuando no estás mirando la pantalla.
- **A mitad de turno, no.** Si está grabando o pensando, la orden devuelve
  «ocupado» y el motor se queda como está.
- **Todas las ventanas se enteran.** El cambio viaja como evento, así que la
  cabecera de las que tengas abiertas se actualiza sola.

> Con `claude` (API) mantienes **todas** las herramientas de maripepis, y se paga
> por token. Con `claude-code` usas tu suscripción sin clave, pero ese backend
> trae las suyas y las de `[tools]` no le llegan: pasa a ser conversación a
> secas, salvo que le des las de Claude Code (ver arriba).

**`🧠 pensando…` es un estado de verdad, no un adorno.** Claude Code piensa y se
va a internet **antes** de abrir la boca, y eso son diez o treinta segundos. Así que el salto a `🗣️ hablando…` lo dispara el primer trozo de
respuesta, no el principio del turno, y por el camino se van pintando las
herramientas que usa (`⚙️ WebSearch · tiempo Sevilla mañana`, y en rojo las que
fallan). Sin eso, un turno con búsqueda era medio minuto de ventana congelada
diciendo que hablaba, que es exactamente lo que se ve cuando algo se ha colgado.

Se puede interrumpir mientras piensa, no solo mientras habla: **ALT+Z** corta el
turno igual.

### Buscar en internet

`buscar_en_internet` **te devuelve el texto** de lo que encuentra, para que el
asistente pueda contestar con datos y no con una pestaña abierta. Antes solo
lanzaba `xdg-open` y devolvía «he buscado X»: al modelo no le llegaba ni una
palabra, así que ante *"busca el tiempo y apúntamelo en un fichero"* no podía
hacer nada — y en vez de decirlo, escribía el fichero con los días en blanco.

Tira de dos fuentes sin clave ni registro: la **respuesta directa de DuckDuckGo**
(acierta con nombres propios exactos) y la **Wikipedia en español** (que cubre
quién, qué, cuánto y dónde). Si ninguna sabe nada, entonces sí abre la búsqueda
en el navegador —y **lo dice claramente**, para que el modelo no conteste como si
tuviera los datos.

> **No hay búsqueda web general, y no por pereza: no la hay gratis.** DuckDuckGo
> (`html` y `lite`), SearXNG y Mojeek contestan con un captcha a todo lo que no
> sea un navegador de carne y hueso, y el resto pide clave de API. Si quieres una
> búsqueda web de verdad, hace falta darse de alta en algo (Brave Search o Serper
> tienen plan gratuito) y meter la clave en la configuración.

### Escribir y leer ficheros

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

Y su otra mitad, `leer_fichero`, que es la que hace posible *"revisa el documento
que me hiciste"*. Sin ella el modelo no ve el disco: de un turno anterior solo le
queda su propia frase («he creado el archivo»), sin la ruta ni el contenido, así
que antes que reconocerlo se inventaba lo que ponía y te aseguraba que lo había
corregido. Ahora lee de verdad antes de reescribir, y en el historial queda
apuntado **lo que las herramientas hicieron**, no lo que el modelo dijo que había
hecho. Recorta a 4000 caracteres (lo que lee viaja en cada petición siguiente) y
no toca binarios.

### El tiempo

`consultar_tiempo` da el parte de ahora y hasta **3 días**, con temperaturas,
cielo, lluvia y viento, y el detalle cada tres horas si lo pides. Sale de
[wttr.in](https://wttr.in), en español y sin clave ni registro. Está aparte de la
búsqueda porque es de lo que más se pregunta hablando y porque la enciclopedia no
sabe qué tiempo hará el jueves. Más de tres días no hay: si pides «la semana», lo
dice en vez de rellenar el resto.

### WhatsApp: el mensaje se queda escrito

*"Mándale un wasap a Marta diciendo que llego en diez minutos"* → te abre su chat
en [ZapZap](https://github.com/rafatosta/zapzap) con el mensaje **escrito en el
cuadro de texto**. Y ahí se para: **no lo envía**.

Eso es el modo por defecto (`modo = "borrador"`), y hay otro que lo envía de
verdad — más abajo. Pero el que viene puesto es este, y por algo:

No es que no se pueda. Es que de todo lo que hace Maripepis, esta es la única
acción que **sale del equipo y le llega a otra persona**, y la única que no se
deshace: una carpeta mal creada se borra, un mensaje enviado no se retira. Quien
lo pide hablando no está mirando la pantalla mientras habla — el Enter es
justamente el momento en que la mira, ve a quién va y ve qué pone. Así, que el
micrófono entienda *Marta* donde dijiste *Marcos* es un mensaje que no llegas a
enviar, en vez de uno que ya no puedes retirar.

Y lo dice, además de hacerlo: si el modelo remata el turno con un *"ya se lo he
mandado"*, se le desmiente en voz alta. Esa mentira es cara — te quedas esperando
una respuesta a un mensaje que sigue en el cuadro de texto.

**La agenda es tuya y va aparte.** La libreta de WhatsApp vive dentro de la sesión
del navegador de ZapZap y desde fuera no se lee, así que los nombres salen de un
fichero propio, `~/.config/maripepis/contactos.toml`:

```toml
marta = "+34600112233"
"mi hermana" = "+34600112233"     # varios nombres, un número: vale cualquiera al hablar
pepe = "600998877"                # 9 cifras → se le pone el prefijo de [tools.whatsapp]

[grupos]                          # al final del fichero, y solo con modo = "envio"
familia = "120363021234567890@g.us"
```

> Los nombres con espacios, **entre comillas**: sin ellas el TOML no es válido y la
> agenda se queda a cero. Si pasa, te lo dice con esas palabras en vez de
> mandarte a crear una agenda que ya tienes.

Va fuera del repositorio a propósito (`.gitignore`): son teléfonos de otra gente.
Al modelo se le pasan **los nombres, nunca los números** — esa lista viaja en cada
petición, y con el backend de Claude eso es la nube.

Lo que no hace, por si acaso:

- **No se inventa un teléfono.** Si le pides escribir a alguien que no está en la
  agenda, lo dice y te recuerda a quién sí tienes apuntado.
- **No elige entre dos Martas.** Si encajan varias, pregunta.
- **No confunde a Ana con Juana**: compara por palabras enteras.
- **No da el mensaje por escrito si ZapZap estaba cerrado.** Un enlace a una
  aplicación cerrada se pierde (su `SingleApplication` solo lo mira si ya hay una
  instancia abierta), así que te la abre y te dice que se lo pidas otra vez.

| `[tools.whatsapp]` | Para qué |
|---|---|
| `enabled = true` | `false` y el asistente no puede abrirle el chat a nadie. |
| `modo = "borrador"` | `"envio"` y lo manda de verdad, por sesión propia, tras leértelo y preguntar. Ver abajo. |
| `agenda = ""` | Vacío = `~/.config/maripepis/contactos.toml`. Ahí van también los grupos, en `[grupos]`. |
| `prefijo = "34"` | Prefijo de país para los números de 9 cifras. Lo que empieza por `+` o `00` se respeta tal cual. |
| `cliente = "zapzap"` | Quien abre WhatsApp; tiene que entender un `whatsapp://` en la línea de órdenes. |

#### Si quieres que lo envíe de verdad: `modo = "envio"`

Con sesión propia de WhatsApp, Maripepis lo manda ella: no hay chat en pantalla,
ni Enter, ni marcha atrás. Y por el mismo camino se pueden abrir grupos, que un
enlace `whatsapp://` nunca pudo tocar porque un grupo no tiene teléfono.

Como ahí ya no hay pantalla que mirar, **el freno se muda a la conversación**: va
en dos turnos, y en medio hablas tú.

> — *Mándale un wasap a Edu diciendo que llego en diez.*
> — *A Edu: «llego en diez». ¿Te lo mando?*
> — *Sí.*
> — *Enviado.*

El primer turno no manda nada: deja el mensaje preparado y te lee **a quién va y
qué pone**. El segundo lo suelta. Y son dos herramientas de verdad, no una con un
paso de más: la de confirmar **no lleva ni destinatario ni texto**, así que lo
único que el modelo puede hacer con ella es soltar lo que tú acabas de oír — no
hay hueco por donde se cuele una Marta que nadie ha nombrado. Tampoco vale que se
conteste a sí mismo: las dos llamadas tienen que venir de turnos distintos, y
entre dos turnos solo se pasa hablando. Si dices que no, o cambias el mensaje, se
prepara otra vez; y si te vas a hacer otra cosa, el mensaje preparado caduca al
minuto.

Eso es lo que le devuelve al usuario lo que le quitó el envío directo: enterarse
**antes** de a quién le va a llegar qué. Si el micrófono entendió «Marcos» donde
dijiste «Marta», se oye — y basta con no decir que sí.

##### Grupos

Por este camino sí se puede escribir a un **grupo**, que un enlace `whatsapp://`
nunca pudo tocar: un grupo no tiene teléfono, tiene un identificador que solo se
ve desde dentro de la sesión. Se apuntan a mano en `[grupos]`, y el identificador
te lo dice la propia maripepis:

```bash
maripepis-wa grupos familia          # busca por parte del nombre
  "120363021234567890@g.us"   # Familia Guevara
```

El filtro es obligatorio a propósito: esta cuenta está en **269 grupos**, y esa
lista no es una agenda, es un listín — que además acabaría viajando al modelo en
cada frase. Apunta los tres o cuatro a los que de verdad escribes.

Cuando el mensaje va a un grupo, la confirmación lo dice: *«va **al grupo**
Familia y dice…»*. No es un adorno — es la diferencia entre que lo lea una
persona y que lo lean doce, y sin ver la pantalla no hay otra forma de notarlo. Y
si tienes una persona y un grupo apuntados con el mismo nombre, no elige: pregunta.

##### «No, bórralo»

Y si aun así sale lo que no era, se retira hablando:

> — *Mándale un wasap a Edu diciendo que llego en diez.*
> — *A Edu: «llego en diez». ¿Te lo mando?*
> — *Sí… ¡uy, no! Bórralo.*
> — *Borrado.*

Una sola herramienta para las dos situaciones, porque tú no tienes por qué
distinguirlas: si el mensaje **solo estaba preparado**, lo descarta y no llega a
salir nada; si **ya había salido**, le pide a WhatsApp el «eliminar para todos».
Tampoco lleva argumentos —siempre es el último— y aquí **no hay confirmación que
valga**: retirar es el lado seguro, y lo peor que puede pasar por hacerte caso de
más es tener que mandar el mensaje otra vez.

> Dos cosas que no se prometen: WhatsApp solo deja retirar un mensaje **durante
> un rato**, y cuando dice que no, te lo dice con esas palabras en vez de dejarlo
> en el aire. Y aunque lo retire, **el otro puede haberlo leído antes** — eso no
> lo sabe nadie.

```bash
pip install -e '.[whatsapp]'                       # neonize (bindings de whatsmeow)
maripepis-wa vincular                              # QR: móvil → Dispositivos vinculados
cp packaging/maripepis-whatsapp.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now maripepis-whatsapp
maripepis-wa estado                                # ¿viva la sesión?
```

Luego, en `config.toml`, `modo = "envio"`.

Vive en un servicio aparte porque la biblioteca se queda con el hilo para siempre
(su `connect()` no vuelve nunca), y porque son dos cosas que fallan por motivos
distintos: que WhatsApp pierda la sesión no tiene por qué llevarse por delante la
tecla de hablar. En el móvil, el dispositivo vinculado aparece como
**«Firefox (Linux)»**.

**Lo que sigue igual:** la agenda, el «no me invento teléfonos», el «no elijo entre
dos Martas» y el tope de texto. Equivocarse de persona es el mismo error se envíe
o se deje escrito, así que esas barandillas las comparten los dos modos — y valen
igual para los grupos.

**Lo que cambia:** donde había una herramienta hay tres —
`preparar_mensaje_whatsapp`, que redacta; `enviar_mensaje_whatsapp`, que confirma;
y `borrar_mensaje_whatsapp`, que se arrepiente— y el desmentido se reparte con
ellas. Mientras el mensaje solo esté
preparado, un «ya se lo he mandado» se sigue desmintiendo en voz alta, pero
diciendo lo que toca hacer ahora («lo tengo preparado, dime que sí»); una vez
confirmado, «se lo he enviado» es verdad y desmentirlo sería la misma mentira del
revés.

**Y lo que asumes**, dicho sin adornos: es un cliente no oficial sobre tu número
personal, así que hay un riesgo pequeño pero real de bloqueo de cuenta; el fichero
de sesión (`~/.local/share/maripepis/whatsapp/`) es tu WhatsApp entero para quien
lo copie, y por eso se crea en 600; y un fallo del micrófono ya no se queda en un
mensaje sin enviar. Lo único que te queda entonces es el «eliminar para todos» de
WhatsApp, y dura un rato.

**Con el backend `claude-code` va por otro sitio.** Ese proveedor trae sus propias
herramientas y las de maripepis no le llegan (`accepts_tools = False`), así que no
vale con activarla: no existiría. Si tiene `Bash` en `[llm.claude_code] tools`, se
le pasa en el *system prompt* **la orden hecha**, y la ejecuta él:

```bash
maripepis-whatsapp "Edu" "llego tarde"    # en el bin/ del venv
maripepis-whatsapp --enviar               # solo en modo envío: el «sí» del usuario
maripepis-whatsapp --borrar               # y el «no, espera»
```

Es la misma herramienta —misma agenda, mismo «no me invento teléfonos», mismo «NO
está enviado»—, solo que llamada por la shell. Se le da la orden y no la receta a
propósito: contarle que ZapZap entiende enlaces `whatsapp://` acaba en un enlace
montado a mano con un teléfono inventado. Sin `Bash` no se le cuenta nada, y queda
dicho en el log al arrancar.

Los dos pasos del modo envío también son dos por aquí, y con el mismo freno: cada
turno del CLI lleva su marca en el entorno (`MARIPEPIS_TURNO`), la hereda todo lo
que lance con su `Bash`, y el `--enviar` de la misma vuelta que el `preparar` no
manda nada. El mensaje que espera vive mientras tanto en un fichero de
`$XDG_RUNTIME_DIR`, en 600, que es lo que permite que dos procesos distintos sean
los dos pasos de una misma conversación.

> Lo que sí se pierde por ahí es el desmentido en voz alta: `desmiente_envio` vive
> en el turno con herramientas, y por esta vía no pasa. Queda la orden del *system
> prompt* y el «NO está enviado» que devuelve la propia orden.

### Las luces de casa

*"Apaga el salón."* *"Pon la cocina al veinte."* *"Las luces en rojo."* *"Pon la
escena relax."* *"¿Me he dejado alguna luz dada?"*

Esto empezó siendo *"conéctate a mi cuenta de Google y controla el Google Home"*,
y conviene dejar escrito por qué no es eso, para que nadie lo intente otra vez:

| Vía de Google | Estado |
|---|---|
| SDK de Google Assistant | **Cerrada.** Era lo único que dejaba mandar «apaga la luz» por código; Google la retiró en 2023 |
| Smart Device Management | Viva, pero solo llega a los **Nest** de la propia Google (termostato, cámaras, timbre), y con registro de pago |
| Home APIs (2024-25) | SDKs de **Android y de iOS**, con verificación de marca |
| Home Graph API | Para **fabricantes** de dispositivos, no para tu casa |

Ninguna sirve para un script en Linux. Así que Maripepis le habla a la bombilla,
no a la nube: la **API local** del puente Philips Hue. No pasa por internet, no
hay token que caduque a media noche, y la luz se apaga en el mismo momento en que
acabas la frase — que es justo lo que hace falta cuando lo pides hablando.

**Vincularse, una vez.** La llave del puente solo se le da a quien está
físicamente en la casa, y eso es precisamente la seguridad del invento:

```bash
maripepis-hue vincular      # y pulsas el botón redondo del puente
maripepis-hue luces         # para ver cómo se llaman tus luces
```

La llave queda en `~/.config/maripepis/hue.toml`, en 600 y fuera de git, igual
que la agenda de WhatsApp: con esa cadena, cualquiera que la lea enciende y apaga
las luces de tu casa desde la red local. El puente se busca solo por mDNS y se
recuerda; si cambia de IP porque el router se reinició, se vuelve a buscar sin
que tengas que hacer nada. En `[tools.hogar]` puedes fijarla a mano si tu red no
deja pasar el descubrimiento.

**Los nombres son los tuyos.** Los de la app de Hue, y se dicen como se dicen: se
quitan tildes y relleno, así que *"las luces del salón"*, *"Salon"* y *"el salón"*
son lo mismo, y *"el dormitorio"* acierta con «Dormitorio principal». *"Todo"*,
*"toda la casa"* o *"apaga la luz"* a secas van a todas. Si dices un sitio que no
existe, no se inventa otro: te devuelve la lista de los que hay para que el
asistente te pregunte cuál querías.

**Los colores, por su nombre**: rojo, naranja, amarillo, verde, turquesa, cian,
azul, morado, violeta, lila, rosa, magenta, melocotón — y los blancos por
temperatura: cálido, neutro, frío, luz de día. *"Azul clarito"* se queda con el
azul: perder el matiz es mejor servicio que contestar que no se ha entendido.

Dos detalles que parecen pequeños y no lo son. Pedir brillo **enciende** la luz
(atenuar una bombilla apagada no se ve, y quien lo pide la quiere encendida), y
*"ponlo a cero"* la **apaga** de verdad, que es lo que quiere decir — en el
puente, brillo 0 la deja al mínimo pero dada. Y en *"apaga toda la casa"*, que
una bombilla esté sin corriente no cancela las demás: se apagan las que
respondan y se dice cuántas no lo hicieron.

**Con `backend = "claude-code"` va por la shell.** Ese proveedor trae sus propias
herramientas y las de maripepis no le llegan, igual que le pasa a WhatsApp. Así
que en vez de `controlar_luces` se le da **la orden hecha**, que por dentro llama
a la misma función y devuelve su mismo texto:

```bash
maripepis-hue luz 'el salón' apagar
maripepis-hue luz 'la cocina' --brillo 20 --color calido
maripepis-hue luz 'salón' --escena relax
maripepis-hue estado
```

Se le da la orden y no la explicación a propósito: contarle que hay un puente Hue
en la red acaba con el modelo escribiendo un `curl` a una IP inventada, sin llave
y sin la búsqueda por nombre, mientras alguien espera a oscuras. Necesita `Bash`
en `[llm.claude_code] tools`; si no lo tiene, queda dicho en el log al arrancar.

**Aquí no se confirma nada**, al revés que WhatsApp. No hace falta: encender una
luz se deshace apagándola, se ve desde donde estás y no le llega a nadie más. La
única acción que necesitaba una red de seguridad ya la tiene.

Se quita con `[tools.hogar] enabled = false`, y conviene quitarlo si no tienes
puente: sin él las dos herramientas solo sirven para que el modelo las intente y
falle, y cada una que sobra es contexto gastado en cada frase.

> **Lo que todavía no está.** Solo Hue. Los altavoces y pantallas Cast (Nest
> Mini, Nest Hub, Chromecast) se controlan igual de bien en local con
> `pychromecast`, y los enchufes y persianas casi siempre tienen API propia; la
> puerta está abierta en `maripepis/hogar/`, cada cacharro con su módulo al lado
> de `hue.py`. De Google no va a venir, por lo de la tabla de arriba.

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
> `memoria.md` está en `.gitignore` porque son datos personales. Y viajan a la
> nube en cada turno, con los dos backends: no pongas ahí nada que no le
> contarías a Anthropic.

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
- **Privacidad:** cada ALT+Z manda la transcripción a la nube, con los dos
  backends. Una tecla global hace las capturas accidentales mucho más probables
  que una terminal: lo que se grabe por error también sale del equipo.
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
(`🎙️ te escucho…`, `✍️ transcribiendo…`, `🧠 pensando…`, `🗣️ hablando…`). Las notificaciones de mako se van a los pocos segundos; esto se
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

### El fichero, dentro del chat

Cuando le pides que te genere un archivo —o que te lea uno— no se queda en un «ya
lo tienes en Documentos»: **el fichero entero aparece en la conversación**, con el
Markdown pintado (títulos, negritas, listas, código, enlaces) y en una tarjeta que
se **pliega y despliega** de un clic. Vale para las dos mitades: ver lo que acaba
de escribir, y «enséñame el resumen que me hiciste».

- **Se abre desplegado** si cabe (hasta 40 líneas) y **plegado** si no, para que
  un README no te eche la conversación de la pantalla. Plegado, la cabecera sigue
  diciendo qué hay: `📄 compra.md · 18 líneas`.
- **Solo se pinta como Markdown lo que lo es** (`.md`, `.markdown`). Un `.py` o un
  `.txt` van tal cual, en monoespaciada: interpretar sus `#` como títulos sería
  inventarle un formato que no tiene.
- **Se relee del disco**, no se reutiliza lo que se le pasó a la herramienta. En
  modo «añadir» ese argumento son solo las líneas nuevas, y lo que quieres ver es
  el fichero.
- **El texto no vuelve al modelo**: viaja por el socket y se pinta, nada más. Para
  que el modelo lea un fichero está `leer_fichero`.

De qué ficheros se entera:

| Motor | Cómo llega al fichero | ¿Se ve? |
|---|---|---|
| `claude` | `escribir_fichero`, `leer_fichero` | sí |
| `claude` | `ejecutar_comando` con `echo … > x` o `cat x` | no |
| `claude-code` | su `Write`, `Edit`, `Read` | sí |
| `claude-code` | su `Bash` con `cat > x << EOF` o `cat x` | no |

Los dos «no» son el mismo motivo: de un comando de shell no hay forma de saber
con qué fichero anda sin ponerse a interpretar redirecciones, y equivocarse ahí es
enseñar un documento que no es. Por eso, con `claude-code`, el proveedor le añade
al system prompt que use `Write` y `Read` en vez de Bash —sin esa nota escribía
con un `cat > … << EOF`, medido— y `[llm.claude_code] tools` lleva las dos.
Quitarlas de esa lista no le impide leer ni escribir (Bash ya puede): lo único que
pierdes es ver el fichero en el chat.

### Reiniciar de un clic

A la izquierda de la cabecera hay un botón (↻) que **reinicia Maripepis**: hace
`systemctl --user restart maripepis` y ya. Es para las dos veces que se acaba
abriendo una terminal:

- cuando has tocado `config.toml` (el demonio solo lo lee **al arrancar**: cambiar
  de modelo de Whisper, de voz de Piper o de `[tools]` pide reinicio);
- cuando se ha quedado colgado y no contesta.

Justo por lo segundo **no habla con el demonio, habla con systemd**: pedirle a un
proceso atascado que se reinicie es lo que no iba a funcionar. Por eso el botón
sigue activo aunque la cabecera diga `⚠️ sin demonio`, que es cuando más falta
hace.

La ventana **no se cierra**: se lanza con `uwsm-app`, fuera del cgroup del
servicio, así que sobrevive al reinicio y se reengancha sola en cuanto el demonio
vuelve a abrir el socket. Lo que sí se pierde es la conversación —el demonio
arranca en blanco—, y por eso queda escrito un `— reiniciando Maripepis —` en el
hilo: lo de arriba ya no es contexto de nadie.

Si Maripepis no corre como servicio (a mano, `python -m maripepis --daemon`), el
botón lo dice en el hilo en vez de fingir que ha hecho algo.

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
  streaming. Con `[tools] enabled = true` y el backend `claude`, el turno de
  herramientas no va en streaming y la respuesta aparece de una vez.
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
