# ArtesaNFC — QA reproducible de la ruta privada `/c/{token}`

**Estado:** vigente desde el hallazgo F-06 (post-Sprint 4).
**Comando principal:** `./qa/validate-private-route.sh`

## 1. Para qué sirve

`docs/SPRINT_4.md` (§9 y §12) describe una verificación con navegador real de
`/c/{token}` que no se podía repetir desde el repositorio: no había scripts, ni
forma de generar un token, ni un servidor local que aplicara la regla
`/c/*  /c/  200` de `frontend/_redirects` (Live Server y `python -m http.server`
devuelven 404 para `/c/<token>`), ni dependencias fijadas. Este QA lo convierte
en un flujo del repositorio, repetible con un solo comando.

No reemplaza ni reescribe los números históricos de Sprint 4, y **no es
equivalente 1:1** a aquella verificación (ver §8).

## 2. Requisitos previos

El script **no instala nada**: si falta algo, se detiene con el comando exacto
para arreglarlo (código de salida 2).

| Requisito | Detalle |
|---|---|
| Docker | Para una base PostgreSQL desechable. La imagen debe existir localmente: `docker pull postgres:16` (misma versión que el CI). |
| Python 3.12+ | Con los paquetes del backend **y** Playwright (abajo). Se usa `python3`, o el que indiques con `PYTHON=/ruta/a/python`. No depende de `backend/.venv` ni de `venv/` (hoy son directorios vacíos; arreglar esa documentación es tarea aparte). |
| Playwright `1.63.0` + Chromium | Versión exacta en `qa/private_route/requirements.txt`. El script comprueba la versión y que el navegador esté descargado. |
| Puerto `127.0.0.1:8000` libre | `frontend/assets/js/api-config.js` fija la API del frontend `localhost`/`127.0.0.1` en `http://127.0.0.1:8000/api/v1`, y ese archivo no se modifica. Si el puerto está ocupado el script se detiene y lo explica (`ss -ltnp \| grep :8000`). |
| Opcional: `wrangler` `4.135.0` | Solo para `QA_SERVER=wrangler`. Ese modo **exige además almacenamiento en RAM** (`/dev/shm` con tmpfs, o un directorio tmpfs/ramfs escribible indicado en `QA_WRANGLER_RAM_DIR`); sin él, el script falla (código 2) y no arranca nada (§6). |

Preparación (una vez), en un entorno virtual **fuera del repositorio**:

```bash
python3 -m venv ~/.venvs/artesanfc-qa        # Debian/Ubuntu: sudo apt install python3-venv
source ~/.venvs/artesanfc-qa/bin/activate
pip install -r backend/requirements.txt -r qa/private_route/requirements.txt
python -m playwright install chromium         # descarga el navegador (~150 MB)
docker pull postgres:16
```

Con el entorno activado, `python3` ya apunta a ese intérprete. Un intérprete
del sistema con los mismos paquetes también sirve.

> Nota de verificación: los comandos anteriores no se pudieron ejecutar
> literalmente en la máquina donde se implementó (le faltaba `python3-venv`);
> allí se usó un intérprete con los mismos paquetes ya instalados.

## 3. Uso

```bash
./qa/validate-private-route.sh                    # servidor integrado (por defecto, sin Node)
QA_SERVER=wrangler ./qa/validate-private-route.sh # mismas comprobaciones sobre wrangler pages dev
```

Se puede lanzar desde cualquier directorio. Códigos de salida: **0** todo pasó,
**1** falló alguna comprobación, **2** problema de preparación o error del
propio arnés. Imprime una línea `PASS`/`FAIL` por comprobación y un resumen.

Variables opcionales: `PYTHON`, `POSTGRES_IMAGE` (por defecto `postgres:16`),
`WRANGLER_BIN`, `QA_WRANGLER_RAM_DIR` (solo Wrangler, ver §6) y `QA_FRONTEND_DIR` (probar otro árbol de `frontend/`; se usa
para los controles negativos de §7, nunca edita el árbol de trabajo).

### Wrangler (verificación de fidelidad)

`wrangler pages dev` es el emulador de Cloudflare Pages: el mismo que reveló el
bloqueador B1. El servidor integrado (§5) es reproducible y no necesita Node,
pero **solo implementa el subconjunto de reglas de Pages que este QA usa**;
Wrangler es la comprobación de mayor fidelidad y debe pasar también.

```bash
npm install -g wrangler@4.135.0    # una vez; el script nunca lo instala
QA_SERVER=wrangler ./qa/validate-private-route.sh
```

Si `wrangler` no está en el `PATH`, el script prueba
`npx --no-install wrangler@4.135.0` (usa la caché de npx, sin descargar nada).

## 4. Qué levanta y qué comprueba

Todo en loopback y con nombre único por corrida: un contenedor PostgreSQL
desechable (`tmpfs`, puerto efímero), migraciones (`alembic upgrade head`), la
API real (`uvicorn`, `127.0.0.1:8000`, `APP_ENV=test`), el árbol real de
`frontend/` y un único proceso Python que ejecuta las comprobaciones.

| Grupo | Comprobaciones |
|---|---|
| **Reglas estáticas** (sin servicios) | La regla real es exactamente `/c/*  /c/  200`. Se **rechaza** la forma rota de B1 (`/c/*  /c/index.html  200`, que Pages ignora), cualquier regla splat hacia `/index[.html]`, una regla ausente, duplicada, con otro estado o precedida por otra que capture `/c/{token}`. `_headers` de `/c/*`: `no-store`, `noindex`, `no-referrer` efectivo. Con autopruebas del propio linter (controles negativos). |
| **Enrutamiento HTTP** | `GET /c/{token}`, `/c/`, `/c/foo/bar`, `/c/x?x=1` → **200** con el shell del certificado (no Home, sin redirección) y cabeceras privadas; `/c` → redirección a `/c/`; las páginas públicas y los assets no son capturados por la regla. |
| **Navegador** (Chromium, escritorio 1280×800 y móvil 390×844) | Ver la tabla de casos abajo. |
| **Aislamiento público** | Con certificados activos existentes: `GET /artisans`, `/artisans/{slug}`, `/pieces`, `/pieces/{slug}` sin token, `token_hash`, UUID de certificado, canario de metadatos internos ni campos de certificado/NFC; la pieza no publicada es 404 y no aparece en el listado. Archivos estáticos de páginas públicas: sin datasets JSON, sin enlaces a `/c/`, sin internos. |
| **Persistencia del token** | En la BD el token crudo no aparece y el `token_hash` esperado sí (control positivo); ningún token ni hash en ningún archivo de la corrida (logs de API y servidor incluidos) ni en el perfil de Chromium; el perfil desaparece al cerrar. |

Casos del navegador (cada uno en ambos viewports):

| Caso | Resultado esperado |
|---|---|
| Token válido | 1 POST, estado *auténtico*, contenido de pieza/artesano/versión/fecha/notas, enlaces `/piezas/…` y `/artesanos/…` sin token, sin `Referer` al seguirlos, canario interno ausente |
| Token inválido (bien formado) · certificado revocado · pieza no publicada con certificado activo · alfabeto correcto y longitud incorrecta | 1 POST, *unavailable*; las cuatro respuestas y páginas son **idénticas** |
| Sin token (`/c/`) · token solo en query (`/c/?token=`) | 0 peticiones a la API, *unavailable* |
| Malformados (punto, `%` inválido, ruta anidada, 300 caracteres, espacio, token real + sufijo o segmento extra) | 0 peticiones a la API, *unavailable*, sin `pageerror` |
| Token en ruta + otro en query/fragmento | 1 POST con el token de la **ruta** |
| Fallo de transporte (petición abortada · HTTP 500) | estado *error temporal*, nunca *unavailable* |

En **todo** caso con token se exige: una única petición de resolve, método
`POST`, URL exactamente `/api/v1/certificates/resolve` (por tanto sin query),
cuerpo JSON con exactamente `{"token": …}`; ninguna otra URL ni cabecera
(`Referer` incluido) contiene el token; la URL final es la pedida
(`/c/{token}`, sin `/` final ni redirección); el DOM, `localStorage`,
`sessionStorage`, cookies, IndexedDB, Cache Storage, service workers,
`window.name`, `history.state` y la consola no lo contienen. Para los casos
sin token se exige **cero** peticiones a la API: así una redirección que
rompa la extracción del token (el falso positivo que encontró la auditoría)
no puede hacerse pasar por un "unavailable" correcto.

## 5. Estrategia de enrutamiento local

El servidor integrado (`qa/private_route/static_server.py`, solo biblioteca
estándar) **lee los archivos reales** `frontend/_redirects` y
`frontend/_headers` y **se niega a arrancar** si la regla privada falta o está
mal formada, y descarta —como Pages— las reglas que Pages ignora. Implementa
únicamente el subconjunto de Pages que se necesita: reescrituras 200 (solo si
no existe un asset en esa ruta) y redirecciones 3xx con `*`; cabeceras
combinadas (`, `) de todos los bloques que coinciden; `/dir` → 308 `/dir/`;
ruta desconocida → `index.html` (fallback SPA). **No es un emulador general de
Cloudflare Pages.**

## 6. Garantías sobre el token

- El token sintético existe solo en la memoria del proceso de comprobaciones
  (el navegador debe navegar a `/c/{token}`). **No** hay archivo de token,
  variable de entorno, argumento de línea de comandos, captura, HAR, traza,
  vídeo, informe HTML ni perfil de navegador persistente.
- Todo texto que llega a la terminal (incluidas excepciones y trazas) pasa por
  un filtro que sustituye el valor por `<TOKEN:valid>`, `<TOKEN:revoked>`,
  `<TOKEN:invalid>`, `<TOKEN:unpublished>` (y `<HASH:…>`); se conservan el tipo
  de error y la estructura del traceback. El script imprime alias, nunca
  valores.
- Los tokens se crean con los servicios reales del ciclo de vida
  (`activate_certificate`/`revoke_certificate`) sobre una BD que se destruye al
  terminar: dejan de existir de inmediato.
- Los servidores no registran rutas (el integrado no tiene log de peticiones;
  Wrangler corre con `--log-level warn`) y sus logs se buscan al final.
- **Wrangler exige almacenamiento en RAM.** Wrangler registra localmente la
  ruta de cada petición (`/c/{token}` incluida) en un almacén SQLite sin
  interruptor de apagado. Por eso el modo Wrangler requiere que su estado
  viva en RAM (`/dev/shm`, o el tmpfs/ramfs que indique `QA_WRANGLER_RAM_DIR`;
  un valor que no sea tmpfs/ramfs se rechaza igual). **El QA falla en cerrado:**
  si no hay almacenamiento en RAM adecuado, sale con código 2 y un mensaje de
  requisito previo *antes de arrancar nada*, sin ninguna alternativa en disco y
  sin excluir ningún directorio del escaneo de tokens. El directorio de estado
  es único por corrida (`artesanfc-qa-route-wrangler-<id>`, modo 0700), se
  verifica (existe, es escribible, está en tmpfs/ramfs, no está bajo el
  directorio temporal de disco) y se elimina al terminar, también en fallo e
  interrupción. Invariante: modo integrado → ningún estado con tokens
  persistido; modo Wrangler → ese estado solo existe, de forma transitoria, en RAM.
- `.gitignore` cubre `qa/.artifacts/`, `qa/**/test-results/`,
  `qa/**/playwright-report/`, `test-results/`, `playwright-report/`,
  `*.trace.zip` y `*.har` por si alguna herramienta de navegador los generara.

## 7. Controles negativos

Se comprobó que el QA **falla** con copias temporales de `frontend/` (nunca se
edita el árbol de trabajo):

```bash
cp -r frontend /tmp/fe && sed -i 's#^/c/\*  /c/  200#/c/*  /c/index.html  200#' /tmp/fe/_redirects
QA_FRONTEND_DIR=/tmp/fe ./qa/validate-private-route.sh    # exit 1: forma rota de B1
```

Otras copias probadas: token movido de cuerpo POST a query string en
`assets/js/api.js`; `hydrate-certificate.js` que nunca llama a la API (todo se
ve "unavailable"); y un `console.log(token)`. Las cuatro fallan.

## 8. Producción-like frente a producción real

- **No es Cloudflare.** El servidor integrado aplica un subconjunto; Wrangler
  es un emulador local. La verificación en el primer despliegue real
  (`docs/SPRINT_4.md` §13: `/c/{token}` sirve el shell con las tres cabeceras
  en Pages/producción) sigue pendiente y no la sustituye este QA.
- **No cubre Nginx** (límites de tasa, `client_max_body_size`, log de acceso):
  eso es `backend/nginx/validate-log-privacy.sh` y las pruebas del backend.
- CORS, la base de la API (`api-config.js`) y el origen usado son los locales;
  no hay HTTPS ni CSP/HSTS.
- **Cobertura frente a Sprint 4:** no repite el JavaScript deshabilitado, la
  API realmente detenida, el timeout de 5 s con API colgada, las 12 rutas
  públicas × 2 viewports ni las 47 verificaciones HTTP de resolve (esas viven
  en `backend/tests/`). No se añadió rotación de certificados.
- Fuentes de Google: se sustituyen por una hoja vacía y cualquier otro host
  externo se bloquea, para que la corrida no dependa de la red.

## 9. Limpieza

Trampas en `EXIT`, `INT` y `TERM`. Los servidores y el proceso de
comprobaciones arrancan con `setsid`, cada uno como su propio grupo de
procesos, y se detienen **por grupo** (nunca por patrón de nombre). Como
Playwright lanza Chromium en su propia sesión, además todos los procesos de la
corrida heredan una variable de entorno con el identificador único de la
corrida y cualquier resto con esa marca exacta se termina al final. Después se
elimina el contenedor
(`docker rm -f -v`) y el directorio temporal, y el script informa si quedó
algo. Un vigilante interno aborta con error si alguna llamada del navegador se
bloquea más de 90 s, en lugar de colgar la corrida.
