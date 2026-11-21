> Documento original de lienzo (Willy Sotomayor). La versión vigente de open-lienzo está en [README.md](README.md).

# lienzo — artifacts de claude.ai en local, sincronizados

Cada usuario trabaja sus artifacts **en local y en claude.ai a la vez**. Este repo trae dos piezas, pensadas para
integrarse en **otterm**:

| Pieza | Qué hace |
|---|---|
| **Visor** (`viewer/`) | Corre en `127.0.0.1` sobre la carpeta de artifacts: índice, lienzo de Design editable, Slides y páginas con db local |
| **Sync** (`sync/` + skill `lienzo-sync`) | Lleva local ↔ claude.ai por cuenta: find, status, pull, push, db, historial, deduplicación y etiquetas de entidad/app. Lo de red lo hace una sesión de Claude Code con las tools `Artifact` y `ArtifactData`; los scripts llevan el estado |

Es Python con la biblioteca estándar, sin dependencias, más HTML/JS sin build.

| Vista | Ruta | Qué hace |
|---|---|---|
| Índice | `/` | Todos los artifacts: búsqueda, pestañas por app, chips de cuenta/entidad/tipo, orden, teclado (`/`, ↑↓, Enter, Esc) |
| **Lienzo** (Design) | `/<id>/canvas` | El lienzo de claude.ai: clic derecho y arrastrar = mano (también Espacio y rueda del medio), rueda = desplazar, ⌘/Ctrl+rueda = zoom, clic izquierdo = seleccionar. **Edita elementos** dentro de los boards (doble clic = texto; inspector de color, fondo, letra, peso, relleno, margen, esquinas; ⌘Z deshace) y guarda en el `.dc.html`. Comentarios anclados al lienzo, al board o al elemento |
| Slides | `/<id>/deck` | Presentación de solo lectura (1920×1080, ←/→, notas) con comentarios |
| Página | `/<id>/` | La página tal cual; si llama a `claude.use()`, recibe un shim local (`db`, `user`, `downloads`) |

Este repo **no trae** artifacts, cuentas ni el motor de Design de Anthropic (ver más abajo): cada usuario pone los suyos.

## Correrlo

```sh
python3 viewer/serve.py --root /ruta/a/artifacts --author "Nombre"     # http://127.0.0.1:8765/
python3 viewer/serve.py --root /ruta/a/artifacts --prefetch-fonts      # baja las Google Fonts para ver sin conexión
python3 -m unittest discover tests                                     # 30 tests (uno usa Chrome headless si está)
```

`--root` y `--author` también se leen de `LIENZO_ROOT` y `LIENZO_AUTHOR`. `--author` es el nombre que firma los
comentarios y el que devuelve `claude.use('user')` (por defecto `Local`).

`viewer/service.sh` y `viewer/sandbox.sb` son **solo para macOS**: dejan el visor como LaunchAgent dentro del sandbox
del kernel (lee solo la carpeta de artifacts y el visor, escribe solo `_db`, `_comments`, los `.dc.html`, la caché de
fuentes y su log). En Windows no aplican.

## Formato de la carpeta de artifacts

```
<root>/
├── _runtime/                          motor de los tipos y caché (puede ir aquí o dentro de cada cuenta)
│   ├── design-<versión>/artifact-type/dc-runtime.js   ← de Anthropic, no viene en este repo
│   ├── slides-<versión>/artifact-type/…               ← ídem, para Slides
│   ├── vendor/{react,react-dom}.production.min.js, babel.min.js   opcional: si falta, dc-runtime va al CDN
│   └── fonts/                         caché de Google Fonts (la llena el servidor)
└── <cuenta>/
    ├── manifest.json                  lista de artifacts de la cuenta
    └── <id>/
        ├── index.html …               la página publicada
        ├── project/canvas.json        → es un Design   (boards: project/<nombre>.dc.html)
        ├── project/deck.json          → es un Slides   (láminas: project/slides/<id>.html)
        ├── assets/<assetId>.<ext>     servidos también en /_blob/<assetId>
        ├── _db/<colección>/<doc>.json la db local de claude.use('db')  ("~" se guarda como "@")
        └── _comments/<cid>.json       comentarios locales (no van a claude.ai)
```

También sirve una sola cuenta: `--root <root>/<cuenta>` (con `manifest.json` en la raíz). El servidor recarga solo
cuando cambia algún `manifest.json`.

**`manifest.json`**: un arreglo. Solo `id` es obligatorio; el resto alimenta el índice.

| Campo | Uso |
|---|---|
| `id` | carpeta del artifact (único entre cuentas) |
| `title` | título en el índice |
| `entity`, `app` | chips y pestañas del índice (`?` y `Otros` si faltan) |
| `updated` | fecha de la versión de claude.ai (ISO) |
| `url` / `urls` | marca el artifact como publicado |
| `duplicate_of` | lo oculta del índice |
| `family`, `family_rank`, `family_size`, `family_latest`, `family_tie` | versiones del mismo título (`v2/3`, «Solo vigentes») |
| `sync.pages` | `{ruta: sha256}` de la última sincronización: la columna «Modificado» compara contra esto |

**`project/canvas.json`** (formato del tipo Design de claude.ai), lo que lee el lienzo: `title`,
`pages: [{id, name}]`, `launch.page`, `order: [ruta]`, `boards: {ruta: {title, x, y, w, h, page}}`,
`notes: {id: {text, kind ("title1" o sticky), fill, size, x, y, w, h, maxW, bold, italic, page}}`.

## API (la usa el front; mismo origen)

Todas las rutas `/_api/*` exigen `Host` 127.0.0.1/localhost, rechazan un `Origin` de otro sitio y, en escrituras,
exigen `Content-Type: application/json` (fuerza preflight de CORS, que nunca se concede). El cuerpo de las escrituras
es `{"data": {...}}`.

| Ruta | Métodos | Contrato |
|---|---|---|
| `/_api/board/<id>?path=X.dc.html` | GET | `{src, sha}`: el board crudo y su sha256. 422 si no es UTF-8 |
| | PUT | `{data: {src, prev}}` → reescribe el board si el sha en disco sigue siendo `prev`; si no, **409** y no pisa nada. `src` debe contener `<x-dc`. Responde `{sha}` |
| `/_api/comments/<id>` | GET | arreglo de `{id, text, anchor, author, at, resolved}` |
| | POST | `{data: {text, anchor}}` → crea; `anchor` es libre (el lienzo usa `{kind: "canvas", page, board, x, y, el?…}`) |
| `/_api/comments/<id>?cid=X` | PATCH · DELETE | cambia `text`/`resolved` · borra |
| `/_api/doc/<id>?path=col/doc` | GET · PUT · PATCH · DELETE | `{exists, data}` · reemplaza · fusiona en profundidad (`{"__delete__": true}` borra la clave) · borra |
| `/_api/col/<id>?path=col` | GET | `[{id, data}]` |
| `/_api/rev/<id>` | GET | `{rev}`: sube con cada escritura; el front lo sondea para refrescar |

Otras rutas: `/<id>/project/support.js` (el motor de Design con un mapa que redirige el CDN a `_runtime/vendor/`),
`/<id>/artifact-type/*` (archivos del tipo desde `_runtime/<tipo>-<versión>/`), `/_viewer/*` (este front),
`/_fonts/{g,s}/*` (Google Fonts desde la caché) y `/_blob/<assetId>`.

## Sync con claude.ai

Las tools `Artifact` y `ArtifactData` solo existen dentro de una sesión de Claude Code con cuenta de claude.ai, así
que la red la hace la sesión siguiendo la skill y los scripts llevan la cuenta de qué cambió.

**Puesta en marcha, por usuario:**

1. Crear el repo de artifacts (una carpeta por cuenta; se recomienda git para tener historial).
2. `cp sync/accounts.example.json sync/accounts.json` y poner email → cuenta y cuenta → carpeta (no se versiona; o
   `LIENZO_ACCOUNTS=/ruta/accounts.json`).
3. Instalar la skill: `ln -s "$PWD/.claude/skills/lienzo-sync" ~/.claude/skills/lienzo-sync`.
4. Opcional: `entities.rules.json` en el repo de artifacts (copia de `sync/entities.rules.example.json`) para que
   `entities.py` asigne entidad y app solo.
5. En una sesión: `/lienzo-sync` → primera bajada de la cuenta (la skill lo guía).

| Comando | Qué hace |
|---|---|
| `sync.py whoami --email E` | cuenta y carpeta de ese email |
| `sync.py status --root R --account A [--json]` | qué cambió en local desde la última sync (páginas y db) → update o create |
| `sync.py mark --root R --id ID --account A [--url --version --updated --title --entity]` | registra una sync hecha (versión en `history`) |
| `sync.py files --root R --id ID` | el mapa `files` listo para `Artifact publish` (con `contentType` donde la tool no lo deduce) |
| `sync.py find QUERY [--email E]` | URL, id o título → copia local, cuenta, entidad, versión vigente y si la sesión tiene acceso |
| `sync.py history --root R --id ID` | versiones de claude.ai registradas y commits de git con cada contenido |
| `sync.py new --account A --title T --entity E` | artifact solo local (`local-…`) cuando la cuenta dueña no está disponible |
| `sync.py init --root R --account A` | línea base: lo que hay en disco = lo último sincronizado |
| `entities.py --repo R [--workspace W] [--apply]` | asigna `entity` y `app` (manual > sesión > referencia en el workspace > título > contenido) |
| `dedupe.py --repo R [--prefer CUENTA] [--apply]` | duplicado = contenido idéntico (también entre cuentas); mismo título y otro contenido = familia de versiones |

Reglas que la skill aplica: cada sesión sincroniza solo la carpeta de su cuenta; no se publica un artifact de una
entidad en la cuenta de otra; no se publica ni se borra sin pedido explícito; en un conflicto se baja y se muestra
el diff (nunca `force`).

## El motor de Design (no viene aquí)

Los boards `.dc.html` cargan `./support.js`, que en claude.ai es el motor del tipo Design (`dc-runtime.js`). **Es código
de Anthropic, así que este repo no lo redistribuye.** Cada artifact Design lo trae entre sus archivos del tipo
(`artifact-type/*`): se baja desde la cuenta del usuario con la tool `Artifact` (`read` con `path`) y se copia a
`<root>/_runtime/design-<versión>/artifact-type/`. Si falta, el servidor arranca igual, avisa en stderr,
`support.js` responde 404 y los boards se ven en blanco (el lienzo, las notas y los comentarios funcionan).

## Integrarlo en otterm

| Opción | Cómo | A favor | En contra |
|---|---|---|---|
| A. Proceso hijo | otterm lanza `serve.py` y abre `http://127.0.0.1:<puerto>/` en una ventana | cero cambios al visor; lo cubren los tests | necesita Python en la máquina |
| B. Portar la API a Node | el proceso principal sirve `viewer/*` como estáticos e implementa las rutas de la tabla | sin Python; un solo runtime | reimplementar ~12 rutas y sus validaciones (409, `SEG`, `BOARD`, mismo origen) |

La sync encaja tal cual: cada sesión de Claude Code que otterm lanza puede correr `/lienzo-sync` con la cuenta de su
usuario (la skill y `sync/` no dependen del visor). El front solo depende de esas rutas y del mismo origen: `canvas.html` carga `/_viewer/comments.js` y
`/_viewer/canvas-edit.js` (globales `LocalComments` y `CanvasEdit`) y monta los boards en iframes del mismo origen,
que es lo que le deja seleccionar y editar elementos.

Está probado en macOS con Chrome; en Windows todavía no se probó.

## Archivos

| Archivo | Qué hace |
|---|---|
| `viewer/serve.py` | Servidor (rutas de arriba, recarga por manifests, fuentes sin conexión) |
| `viewer/index.html` | Índice; los datos llegan como JSON en `{{DATA}}` |
| `viewer/canvas.html` · `canvas-edit.js` | Lienzo de Design y su edición de elementos (`data-dc-tpl` = n-ésima etiqueta de apertura dentro de `<x-dc>`; lo que tiene `{{ }}` o no mapea 1:1 queda de solo lectura) |
| `viewer/deck.html` | Visor de Slides |
| `viewer/comments.js` | Comentarios locales: modo Comentar, pines, panel, resolver/borrar |
| `viewer/claude-local.js` | Shim de `window.claude.use()` (`db` tipo Firestore con `onSnapshot` incremental, `user`, `downloads`); se inyecta solo en páginas que llaman `claude.use(` |
| `viewer/service.sh` · `sandbox.sb` | Servicio y sandbox de macOS |
| `sync/sync.py` | Estado de la sync por cuenta (tabla de arriba) |
| `sync/entities.py` · `entities.rules.example.json` | Entidad y app de cada artifact, con reglas del usuario |
| `sync/dedupe.py` | Duplicados y familias de versiones |
| `sync/accounts.example.json` | Plantilla de cuentas (la real, `sync/accounts.json`, no se versiona) |
| `.claude/skills/lienzo-sync/SKILL.md` | El procedimiento para las sesiones: pull, push, db, cuenta nueva, mantenimiento |
| `tests/test_viewer.py` | API, inyección, fuentes, arranque sin motor y el shim en Chrome headless |
| `tests/test_sync.py` | sync, dedupe, familias y reglas de entidad |

Aportado a Cognikas por Willy Sotomayor el 2026-11-15. Licencia MIT: ver `LICENSE`.
