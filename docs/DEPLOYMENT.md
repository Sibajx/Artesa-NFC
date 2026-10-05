# ArtesaNFC — Despliegues del backend (N-08)

**Estado:** implementado en el repositorio (N-08, ADR-027). **Todavía no adoptado
en el servidor**: el layout `/home/energias/artesa-nfc/`, la unit nueva y el
primer release se instalan en una ventana de mantenimiento aparte, con la
aprobación del Product Owner (§11.1).
**Alcance:** cómo un commit de `main` se convierte en un release verificable,
cómo se despliega, cómo se vuelve atrás y qué evidencia queda.
**Sin secretos:** este documento no contiene credenciales, valores de `.env`,
tokens ni direcciones internas, y no debe recibirlos.

Herramientas (solo biblioteca estándar de Python, `backend/ops/`):

| Herramienta | Dónde corre | Qué hace |
|---|---|---|
| `ops/build_release.py` | máquina del operador / CI (nunca producción) | commit → artifact + `.sha256` |
| `ops/artesa_deploy.py` (`bin/artesa-deploy` en el servidor) | servidor, como `energias` | verificar, preparar, desplegar, backup, restore-check, rollback, retención |
| `tests/ops/rehearsal/run_rehearsal.py` | máquina del operador / CI | ensayo completo con PostgreSQL 18 desechable |

## 1. Preguntas que N-08 debe poder responder

| Pregunta | Respuesta |
|---|---|
| ¿Qué commit está desplegado? | `readlink current` → `releases/<id>`; `releases/<id>/RELEASE.json` → `git.commit`. `artesa-deploy status`. |
| ¿Qué archivos forman el release? | `releases/<id>/MANIFEST.sha256` (SHA-256 de cada archivo); `artesa-deploy verify <id>` detecta cualquier desvío. |
| ¿Qué dependencias exactas usa? | `requirements-prod.lock` (pip-tools, con hashes) dentro del release; `verify <id> --deep` compara el venv con el lock. |
| ¿Cómo se verifica el artifact antes de desplegar? | sidecar `.sha256` → MANIFEST → RELEASE.json → lock → grafo Alembic → ledger (§5), en `prepare` y otra vez en `deploy`. |
| ¿Cómo se crea el backup antes de una migración? | `deploy` hace `pg_dump -Fc` automáticamente antes de cualquier migración (§8). |
| ¿Cómo se comprueba que restaura? | `deploy` restaura ese mismo dump en un cluster PostgreSQL desechable, compara y además ensaya la migración sobre la copia (§9). |
| ¿Cómo se despliega de forma atómica? | release inmutable + venv propio + `rename(2)` del symlink `current` (§4). |
| ¿Cómo se vuelve al release anterior? | `artesa-deploy rollback` (§7.3): symlink + reinicio, validado contra el ledger de migraciones. |
| ¿Cuándo está prohibido el rollback automático? | Siempre que se haya ejecutado una migración (`MIGRATION_DEPLOY`) (§7). |
| ¿Qué evidencia queda? | `shared/state/deployments/<UTC>-<commit12>/` + `deploy-log.jsonl` (§10). |

## 2. El incidente que motivó N-08 (2026-09-20/21)

Resumen sin secretos del incidente (detalle en el Brain, no en el repo):

1. **Producción no era un checkout de Git.** El directorio del servicio
   (`/home/energias/artesa-nfc-backend`) se había formado por copias sucesivas
   (SCP): el código de la app correspondía a un commit, `alembic/env.py` a otro
   más antiguo, y README/Dockerfile a otros; había además un `main.py` ajeno y una
   base SQLite de otra aplicación. **Árbol híbrido, sin procedencia.**
2. **Deriva del venv.** El `.venv` compartido se reconstruyó (Python 3.14) sin
   `sqlalchemy`/`psycopg`/`alembic`. `artesa-nfc.service` entró en
   **crash-loop** (`Restart=always`, `RestartSec=3`, sin límite: más de 12 000
   reinicios y millones de líneas de journal).
3. **Colisión en el puerto 8000.** Una segunda unit (`artesa-finanzas.service`)
   usaba el **mismo directorio y el mismo venv** y tomó `127.0.0.1:8000`: el
   Tunnel entregaba a `api.artesanfc.com` las respuestas HTML de otra
   aplicación.
4. **Recuperación manual.** Se promovió a mano un snapshot anterior verificado en
   un puerto alternativo, se reinstaló el driver que faltaba, y finalmente se
   separaron los servicios: **ArtesaNFC en `127.0.0.1:8000`**, **Finanzas en
   `127.0.0.1:8002`** (con su propio hostname protegido por Cloudflare Access).
   Tunnel en versión 6. PostgreSQL 18 en el puerto 5433.

Lo que N-08 cambia, punto por punto:

| Causa | Control N-08 |
|---|---|
| Árbol mutable/híbrido | Releases inmutables construidos **desde objetos Git**; `verify` detecta cualquier archivo modificado, faltante o ajeno (p. ej. un `main.py` extra). |
| Venv compartido que deriva | Un venv **por release**, instalado solo desde el lock con hashes; `deploy` exige venv == lock. |
| Sin procedencia | `RELEASE.json` (commit, tree, lock, grafo Alembic) + `MANIFEST.sha256` + checksum del artifact + evidencia por despliegue. |
| Otra app en el mismo directorio/puerto | La unit corre solo desde `<root>/current`; `deploy` falla cerrado si el puerto 8000 lo tiene un proceso cuyo cwd no es el release activo; el candidato nunca usa 8000 ni 8002 (Finanzas). |
| Crash-loop sin límite | Recomendación de `StartLimitIntervalSec`/`StartLimitBurst` en la unit de ejemplo (decisión del PO, §15). |
| Recuperación a mano | `rollback` en un paso, `status`, `resolve-activation` y un runbook (§11). |

## 3. Arquitectura y layout

```text
/home/energias/artesa-nfc/
├── bin/
│   ├── artesa-deploy            lanzador (/usr/bin/python3 -I -B bin/ops/artesa_deploy.py)
│   ├── artesa-backup            lanzador (/usr/bin/python3 -I -B bin/ops/artesa_backup.py), D10, desde 1.3.1 (#137)
│   ├── ops -> ops-<release-id>  copia de solo lectura de la herramienta, de un release verificado
│   └── TOOL.json                v2: release/commit instalado (+ SHA-256 de módulos y lanzadores) y el instalador que lo ejecutó (§11.7)
├── incoming/                    artifacts + .sha256 copiados desde la máquina de build
├── releases/
│   └── <YYYYMMDDTHHMMSSZ>-<commit12>/   inmutable
│       ├── app/ alembic/ ops/ alembic.ini requirements-prod.lock
│       ├── RELEASE.json MANIFEST.sha256
│       ├── venv/                venv PROPIO del release
│       └── .prepared            marcador (sha del artifact, del RELEASE.json, del contenido)
├── shared/                      0700 — el único lugar con estado y secretos
│   ├── .env                     0600, dueño energias: APP_ENV, DATABASE_URL, DEBUG, CORS_ALLOWED_ORIGINS
│   ├── backups/                 0700: *.dump + .json + .sha256 (0600) -- dumps previos a migraciones (artesa-deploy)
│   ├── backup/                  0700: backups programados CIFRADOS de artesa-backup (docs/BACKUP.md, D10)
│   └── state/                   0700: deploy-log.jsonl, activation.json, deploy.lock,
│       └── deployments/<UTC>-<commit12>[-rollback]/   evidencia (§10)
├── current  -> releases/<id>    lo que sirve la unit
└── previous -> releases/<id>    destino por defecto del rollback
```

**Unit systemd objetivo:** `backend/ops/systemd/artesa-nfc.service.example`
(no instalada). `WorkingDirectory=<root>/current`,
`EnvironmentFile=<root>/shared/.env`,
`ExecStart=<root>/current/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1`.
Como la unit apunta al symlink, cambiar de release es cambiar `current` y
reiniciar; la unit no se edita en cada despliegue. `deploy` y `rollback`
se niegan (exit 11) si la unit no apunta a `<root>/current`.

`shared/.env` debe contener **solo** las cuatro variables de la app:
`EnvironmentFile=` entrega a la app todo lo que haya en el archivo. `deploy`
avisa (WARN, solo nombres) si hay otras claves.

Finanzas y cualquier otra aplicación tienen su propio directorio, venv, unit y
puerto. N-08 no toca Finanzas.

## 4. Ciclo de vida de un release

```text
  PR → develop → release PR → main (CI obligatorio: backend-ci + release-ci)
                                  │
  máquina del operador:  git fetch origin && build_release.py --ref origin/main
                                  │   (o descargar el artifact de release-ci y verificarlo)
                                  ▼
  scp artifact + .sha256  →  <root>/incoming/
                                  ▼
  artesa-deploy prepare <id>      verificar → extraer a staging → rename → venv →
                                  pip --require-hashes --only-binary --no-deps →
                                  venv == lock → pip check → compilar → import app.main → .prepared
                                  ▼
  artesa-deploy candidate <id>    (opcional) smoke en 127.0.0.1:8001, luego se mata
                                  ▼
  artesa-deploy deploy <id> --expect-commit <sha> [--allow-migration]
                                  ▼
  ACTIVE: current → <id>, previous → anterior; evidencia; retención (5)
```

Pasos de `deploy` (todos fail-closed; `--dry-run` ejecuta solo los de lectura):

| # | Paso | Falla con |
|---|---|---|
| 1-3 | Artifact de `incoming/` re-verificado (sidecar, MANIFEST, RELEASE.json) y **mismo archivo** que se preparó | 10 |
| 4 | Commit autorizado: canal `production` (alcanzable desde `origin/main` al construir) **y** `--expect-commit` coincide | 10 / 2 |
| 5-6 | `shared/.env`: regular, 0600, dueño correcto, `APP_ENV=production`, `DATABASE_URL` presente y no placeholder (nunca se imprime), `DEBUG` off, CORS | 20 / 21 |
| 7-10 | Release preparado e idéntico a su MANIFEST; venv propio == lock | 11 |
| — | Layout, lock, marcador de activación, disco, unit → `current`, puerto 8000 solo del release activo, puerto candidato no reservado | 11 / 2 |
| 11 | Alembic: revisión de la DB conocida; pendientes clasificados por el ledger | 23 |
| — | Política: `breaking` rechazada; migración sin `--allow-migration` rechazada; `pg_dump`/`initdb` ≥ servidor | 30 / 31 |
| — | Confirmación tecleada (el id del release) | 3 |
| 12 | Backup `pg_dump -Fc` (solo si hay migración) | 31 |
| 13 | Restore-check del dump + migración ensayada sobre la copia | 31 |
| 14 | `alembic upgrade head` (forward-only) | 32 |
| 15 | Candidato en `127.0.0.1:8001` + smoke | 40 |
| 15b | Preflight de privilegios, **antes** de tocar symlinks o el marcador: `sudo -v` interactivo (modo sudo) o aviso de abrir una segunda sesión SSH (modo manual). Si falla, nada cambia (§13) | 11 |
| 16 | `previous` → activo actual; `current` → nuevo (cada uno un `rename(2)` atómico) | — |
| 17 | Reinicio: `sudo systemctl restart artesa-nfc.service` interactivo, o `--restart-mode manual`; en ambos casos se verifica con systemd (`InvocationID`/`ExecMainPID`) si la unit se reinició de verdad | — |
| 18 | Smoke final en el puerto 8000 (incluye que el puerto lo tenga el nuevo release); si falla y es code-only: rollback automático *health-first* (§13) | 50/51/54 |
| 19 | Evidencia cerrada (`result.json`; `interrupted` si hubo Ctrl-C/EOF) | 3 |
| 20-21 | Retención: se conservan los 5 releases más nuevos (siempre `current` y `previous`) | — |
| — | Comprobación pública `https://api.artesanfc.com/api/v1/artisans` (JSON) | 53 (sin rollback) |

## 5. Formato del artifact y garantías de procedencia

`artesa-nfc-<release-id>.tar.gz` + `artesa-nfc-<release-id>.tar.gz.sha256`
(formato `sha256sum -c`). `release-id = <UTC YYYYMMDDTHHMMSSZ>-<commit12>`.

- **Fuente:** `git ls-tree` + `git cat-file` del commit; **nunca** el working
  tree. Archivos sin trackear, `.env` locales, cachés o ediciones sin commit no
  pueden entrar. Además el builder **rechaza** un working tree con archivos
  trackeados modificados (`--allow-dirty` solo existe con `--rehearsal`).
- **Allowlist:** solo `app/`, `alembic/`, `ops/`, `alembic.ini`,
  `requirements-prod.lock` de `backend/`. Nada de `tests/`, `docs/`, README.
- **Rechazos:** nombres con forma de secreto (`.env*`, `*.pem`, `*.key`,
  `*.p12`, `*.crt`, `id_rsa*`…), bases/volcados/logs (`*.db`, `*.sqlite*`,
  `*.dump`, `*.log`), `.git`, `__pycache__`, `*.pyc`, venvs (`venv`, `.venv`,
  `site-packages`), cachés de herramientas, temporales de editor/merge (`*~`,
  `*.swp`, `*.tmp`, `*.bak`, `*.orig`, `*.rej`), symlinks, submódulos,
  **y cualquier archivo con material de clave privada** (`BEGIN … PRIVATE KEY`).
- **Determinista:** tar ustar ordenado, `root:root`, modo 0644, `mtime` = hora
  del commit, gzip sin nombre ni fecha. `built_at_utc` sigue la convención
  `SOURCE_DATE_EPOCH` (por defecto: la hora del commit), así que **el mismo
  commit produce los mismos bytes** con el mismo builder (CI lo comprueba
  construyendo dos veces). El contenido lógico (`content_sha256` = SHA-256 del
  MANIFEST) es idéntico siempre, independientemente del gzip.
- **Los tags de Git no forman parte del artifact.** Crear, mover o borrar un
  tag (p. ej. el tag de release previo al rollout, D7) no cambia ni un byte ni
  el SHA-256 del artifact construido desde el mismo commit; un test y CI lo
  comprueban. `build_release.py` imprime los tags que apuntan al commit
  (`tags …  (informational, not in the artifact)`) para que el operador los
  anote en el PR/ticket del despliegue; la evidencia del servidor identifica el
  release por commit, `content_sha256` y SHA-256 del artifact.
- **Límites de la igualdad byte a byte:** fijar `SOURCE_DATE_EPOCH` cambia a
  propósito `release_id` y `built_at_utc`; y el `.tar.gz` puede diferir entre
  máquinas si su zlib comprime distinto. En ambos casos `content_sha256` no
  cambia: es la identidad lógica del release, y la comparación entre un build
  local y el de `release-ci` debe hacerse con el mismo `release_id`.
- **`MANIFEST.sha256`:** `<sha256>  <ruta>` por archivo de contenido, ordenado,
  LF, rutas relativas, sin timestamps.
- **`RELEASE.json`** (esquema cerrado: un campo desconocido invalida el release;
  ningún valor puede parecer host, IP, ruta `/home` o credencial):

  | Campo | Contenido |
  |---|---|
  | `schema_version`, `project` | `1`, `artesa-nfc` |
  | `release_id`, `channel` | id; `production` o `rehearsal` |
  | `git.commit`, `git.commit_short`, `git.tree`, `git.committed_at` | procedencia (sin tags: no deben cambiar el artifact) |
  | `git.reachable_from` | `origin/main` en canal production (verificado al construir) |
  | `build.built_at_utc`, `build.timestamp_source`, `build.builder_version`, `build.target_python` | `commit` o `source_date_epoch`; builder `3`; Python `3.14` |
  | `artifact.roots`, `artifact.file_count`, `artifact.content_sha256` | checksum del contenido sin circularidad |
  | `deps.lockfile`, `deps.lockfile_sha256`, `deps.install_mode` | lock y modo de instalación |
  | `alembic.head`, `alembic.heads_count` (=1), `alembic.revisions` | grafo completo |
  | `runtime.*` | contrato de runtime usado por el smoke |

  El checksum del **archivo** `.tar.gz` no puede ir dentro de sí mismo: está en
  el sidecar, en `.prepared` y en la evidencia (`artifact.json`).

**Garantías y límites.** Se garantiza integridad (sidecar + MANIFEST),
procedencia declarada (commit/tree) y que el canal production se construyó desde
historia alcanzable desde `origin/main` en la máquina de build. El servidor no
tiene Git: la autorización del commit en producción se apoya en el canal del
artifact **y** en `--expect-commit`, que el operador copia de `main`/CI. **No hay
firma criptográfica (D6, diferida):** quien controle a la vez el artifact y su
sidecar en tránsito podría sustituirlos; mitigaciones: construir uno mismo desde
`origin/main` o comparar el SHA-256 con el publicado por `release-ci`, y
`--expect-commit`.

## 6. Dependencias

- `backend/requirements.txt` se mantiene (desarrollo y tests: añade `pytest` y
  `httpx`).
- `backend/requirements-prod.in`: dependencias directas de runtime, con los
  **mismos pins** que `requirements.txt` (un test lo exige).
- `backend/requirements-prod.lock`: generado por pip-tools con `--generate-hashes`,
  solo wheels (`--only-binary=:all:`), para Python 3.14. Un test rechaza
  paquetes de test en el lock y pins sin hash.

Regenerar (sin cambiar versiones: pip-compile conserva los pins existentes):

```bash
cd backend
python3.14 -m venv /tmp/piptools && /tmp/piptools/bin/pip install pip==26.2.1 pip-tools==7.6.1
/tmp/piptools/bin/pip-compile --generate-hashes --no-emit-index-url --strip-extras \
    --pip-args="--only-binary=:all:" --output-file=requirements-prod.lock requirements-prod.in
```

Actualizar una versión es un cambio deliberado aparte: editar
`requirements-prod.in` **y** `requirements.txt`, `pip-compile --upgrade-package
<paquete>`, PR revisado. `release-ci` regenera el lock y falla si difiere.

**Riesgo conocido (M3, sin cambiar el diseño todavía):** la regeneración de
`release-ci` consulta PyPI. Si un paquete ya fijado publica **wheels nuevos
para la misma versión** (típico de `greenlet`, `uvloop`, `watchfiles`,
`pydantic-core`, `psycopg-binary` cuando sale un Python o una plataforma
nueva), pip-compile añade sus hashes y el `diff` falla aunque ninguna versión ni
ningún archivo del repo haya cambiado. No es un riesgo de integridad (el lock
del repo sigue siendo válido e instalable: los hashes existentes no cambian),
sino de CI rojo en `main` por causas externas. Si ocurre: revisar que el diff
solo **añade** hashes de versiones ya fijadas, regenerar el lock en un PR aparte
y documentarlo. Alternativas a evaluar más adelante: comprobar solo que los
pins coinciden, o tolerar hashes añadidos.

Instalación en el servidor (la hace `prepare`): `pip install --require-hashes
--only-binary=:all: --no-deps -r requirements-prod.lock`, luego venv == lock
(ni paquetes extra ni versiones distintas) y `pip check`.

## 7. Migraciones y política de rollback

### 7.1 Clasificación

Cada revisión de Alembic debe estar clasificada en
`backend/ops/migration-classes.json` (`baseline`, `additive`, `breaking`). El
build falla si falta una o sobra una. Con la DB en revisión *R* y el release con
head *H*:

| Tipo | Condición | Qué hace `deploy` |
|---|---|---|
| `CODE_ONLY` | no hay migraciones pendientes | sin backup; **rollback automático** si el smoke posterior al switch falla |
| `MIGRATION_DEPLOY` (additive) | pendientes, todas `additive` | exige `--allow-migration`; backup → restore-check → migración → candidato → switch; **nunca** rollback automático |
| `MIGRATION_DEPLOY` (breaking) | alguna `breaking` | **rechazado en V1** (D9), exit 30 |
| DB desconocida / por delante | *R* no está en el grafo del release | rechazado, exit 23 |

Nada en la herramienta ejecuta `alembic downgrade`, ni automática ni manualmente
(`run alembic` solo admite `current`, `heads`, `history`, `check`).

### 7.2 Por qué no hay rollback automático tras una migración (D13)

Tras `upgrade`, el código anterior correría contra un esquema que no conoce; si
la migración fuera incorrecta, volver el código no deshace los datos. La
herramienta se detiene (exit 54), deja `current` en el release nuevo, deja
`shared/state/activation.json` y la evidencia (`rollback.json`:
`attempted=false`, razón), y **pide una decisión humana**: rollback manual
validado por el ledger, fix forward, o restauración desde el backup del mismo
despliegue.

Se registra la revisión **before / target / after** en `alembic.json`.

### 7.3 Rollback manual

`artesa-deploy rollback [--to <id>] [--dry-run]`:

1. el destino (por defecto `previous`) debe estar preparado e intacto (MANIFEST + venv);
2. **compatibilidad por ledger:** la DB actual puede servir al destino solo si
   cada migración aplicada más allá del head del destino es `additive`; una
   `breaking`, una revisión desconocida o una DB *por detrás* del destino lo
   impiden (exit 52);
3. switch atómico + reinicio + smoke; si falla, vuelve al release de partida;
4. es la salida documentada de una activación fallida: con
   `activation.json` presente, `rollback` avisa (WARN) en vez de bloquear, y un
   rollback exitoso lo resuelve. `deploy` sí queda bloqueado hasta entonces.

Nunca cambia el esquema. Si el esquema tiene que volver atrás, el camino es
restaurar el backup (§11.5), decisión humana.

## 8. Backup

`deploy` lo hace antes de cualquier migración; `artesa-deploy backup` lo hace
bajo demanda.

- `pg_dump -Fc --no-owner --no-privileges --file=<partial>`; las credenciales
  van en `PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE` derivadas de
  `DATABASE_URL`, **nunca en argv** (la herramienta se niega a ejecutar un argv
  que contenga un secreto) ni en logs.
- `pg_dump` debe ser ≥ la versión mayor del servidor (18).
- Verificación: no vacío, TOC legible (`pg_restore --list`) con
  `alembic_version` y todas las tablas vivas. Si falla, se borra el parcial.
- Resultado (todo 0600 en `shared/backups/`, 0700):
  - `artesa-nfc-<UTC>-<alembic_rev>[-<commit12 activo>].dump`
  - `….dump.json`: fecha UTC, sha256, tamaño, versión de pg_dump, versión del
    servidor, revisión, release y commit activos, release destino, `deploy_id`,
    conteo de filas por tabla. Sin host, usuario ni contraseña.
  - `….dump.sha256` (formato `sha256sum -c`).
- El commit activo sale del `RELEASE.json` validado de `current`. Si no se puede
  leer o validar, `backup` avisa (`[WARN] active release commit`, y `detail` en
  el evento del log) y **continúa** con `active_commit` vacío y sin sufijo: un
  backup nunca se bloquea por metadatos dañados.

Backups programados, cifrados y fuera del host: **#126 (D10)**, obligatorio antes del
lanzamiento final. D10.1 (base local cifrada, `artesa-backup`) está implementado: ver
**`docs/BACKUP.md`**. Todavía **no** hay copia fuera del host (D10.2). El backup previo a
una migración sigue siendo de `deploy` y no lo sustituye el programado; conviene lanzar
además `artesa-backup run` antes de un deploy con migración.

## 9. Restore-check

Un backup solo cuenta cuando se ha restaurado en algo desechable y comprobado.
**Nunca se restaura sobre la base de producción.**

Destinos:

| Destino | Uso | Cómo |
|---|---|---|
| `--ephemeral` (lo usa `deploy`) | servidor, CI, portátil con binarios PG | `initdb` de un cluster nuevo en `shared/state/restore-tmp/` (0700), **solo socket Unix** (`listen_addresses=''`, socket en un directorio 0700 corto de `$TMPDIR`), SCRAM con una contraseña aleatoria de un solo uso (en ningún argv ni log). Como `energias`, sin sudo, sin `CREATEDB` en el rol de producción, sin contactar el cluster de producción. Se para y se borra siempre. |
| `--scratch-server-url-env VAR` | portátil / CI con un contenedor desechable | debe ser loopback y **no** el puerto del PostgreSQL de producción; la herramienta crea `artesa_restore_test_<aleatorio>`, restaura y lo borra (`dropdb --if-exists`). |

Pasos: verificar dump contra su `.json`/sha → crear DB → comprobar que está vacía
→ `pg_restore --exit-on-error --no-owner --no-privileges` → revisión Alembic,
conjunto de tablas y conteos idénticos a los del backup → (opcional / siempre
en `deploy`) `alembic upgrade head` del release destino **sobre la copia** →
destruir. Si algo no cuadra, la migración de producción no se ejecuta (exit 31).

Binarios: `--pg-bindir` (por defecto `/usr/lib/postgresql/<mayor del
servidor>/bin`; `initdb` ≥ servidor). En Ubuntu con PGDG ya están instalados
junto al servidor.

## 10. Evidencia por despliegue

`shared/state/deployments/<UTC YYYYMMDDTHHMMSSZ>-<commit12>/` (0700; archivos
0600; `-rollback` para rollbacks manuales). Se escribe a medida que avanza: un
despliegue interrumpido deja evidencia de hasta dónde llegó (`result.json` con
`status: in-progress`, o `interrupted` si la interrupción fue un Ctrl-C/EOF que la
herramienta pudo registrar). `resolve-activation` cierra un `in-progress` pendiente
como `resolved-by-operator`, con un bloque `resolution` aditivo: nunca lo presenta
como un despliegue normal terminado.

| Archivo | Contenido |
|---|---|
| `result.json` | deploy_id, operador, origen/destino, commit, `CODE_ONLY`/`MIGRATION_DEPLOY`, estado final, exit code, `current`/`previous` resultantes, borde público |
| `release.json` | copia del RELEASE.json |
| `artifact.json` | sha256 del archivo, `content_sha256`, nº de archivos, MANIFEST verificado |
| `dependencies.json` | lock, su sha256, modo de instalación, venv == lock |
| `preflight.json` | todas las compuertas y su resultado |
| `alembic.json` | before / target / after, pendientes, clase |
| `backup.json`, `restore-check.json` | solo con migración: dump, sha256, resultado del restore-check, cluster destruido |
| `privilege-preflight.json` | modo, `sudo -v` (código de retorno, duración) o instrucciones del modo manual |
| `candidate-smoke.json`, `final-smoke.json` | cada check; `final-smoke.json` incluye `restart`: modo, propósito, `kind` (`ok`, `sudo-timeout`, `command-not-found`, `not-restarted`, `systemctl-failed`, `systemctl-timeout`, `unknown`, `confirmed`…), código de retorno, duración y si systemd reinició la unit |
| `rollback.json` | automático: resultado, `strategy` (`health-first`, `restart`, `manual-fallback`), causa del fallo de activación y del restart; o no intentado (razón) |
| `retention.json` | conservados / borrados / ignorados |

Además `shared/state/deploy-log.jsonl` (append-only, 0600, claves en allowlist).
Todo documento se escanea contra los valores secretos de `shared/.env` antes de
escribirse. **Nunca** contiene valores del entorno, `DATABASE_URL`, cuerpos de
petición ni tokens de certificado.

## 11. Runbook

Todos los comandos corren en el servidor como `energias`, en un terminal
interactivo real (los comandos que cambian estado se niegan sin TTY y piden una
confirmación tecleada; no existe `--yes`).

### 11.1 Adopción inicial (una vez, ventana de mantenimiento, requiere aprobación del PO)

Prerrequisitos: el release a desplegar está en `main` con CI verde (hoy `main`
va por detrás de `develop`: primero PR de release develop → main).

1. En la máquina del operador: `git fetch origin && python3 backend/ops/build_release.py --repo . --ref origin/main --out dist`
   (o descargar el artifact de `release-ci` y comprobar que su SHA-256 coincide con uno construido localmente).
2. `mkdir -p /home/energias/artesa-nfc/{bin,incoming,releases,shared/backups,shared/state}`; `chmod 700 shared shared/*`.
3. Copiar artifact + `.sha256` a `incoming/`; `cd incoming && sha256sum -c *.sha256`.
4. Crear `shared/.env` (0600, `energias`) **escribiéndolo en el servidor** con las cuatro variables; nunca copiar el `.env` legado (tiene otras claves).
5. Arrancar la herramienta desde el propio artifact verificado:
   `mkdir /tmp/n08 && tar -xzf incoming/<artifact> -C /tmp/n08 ops && python3 -I -B /tmp/n08/ops/artesa_deploy.py prepare <id>`.
6. `python3 -I -B /tmp/n08/ops/artesa_deploy.py install-tools <id>` → a partir de aquí `bin/artesa-deploy`.
7. `bin/artesa-deploy candidate <id>` (smoke real en 8001 contra la DB real, solo lecturas).
8. Paso privilegiado (§13): **guardar una copia de la unit legada** (`systemctl cat
   artesa-nfc.service` y copiar su archivo fuera de `/etc/systemd/system`), luego instalar la
   unit de `backend/ops/systemd/artesa-nfc.service.example` (decidir antes `Restart=`/`StartLimit*`: en el ejemplo están comentados y, copiado tal
   cual, el servicio no se reiniciaría nunca tras un fallo), `sudo systemctl daemon-reload`.
   `daemon-reload` **no** reinicia nada: el proceso legado sigue sirviendo en `:8000`
   desde `/home/energias/artesa-nfc-backend`.
9. `bin/artesa-deploy deploy <id> --expect-commit <sha> --dry-run`. **Aquí el gate
   `port 8000 held only by the active release` debe fallar (exit 11)**, informando el pid
   y el cwd del proceso legado: todavía no existe `current`, así que cualquier proceso en
   `:8000` es ajeno. Es el comportamiento correcto y la prueba de que el gate funciona;
   la herramienta nunca mata un proceso ajeno.
10. **Inicio de la ventana de downtime inicial.** Paso privilegiado:
    `sudo systemctl stop artesa-nfc.service` (para el proceso legado; la unit ya es la
    nueva). Comprobar que `:8000` queda libre (`bin/artesa-deploy status` muestra el puerto 8000 como `free`)
    y que `127.0.0.1:8002` (Finanzas) sigue intacto. A partir de aquí
    `api.artesanfc.com` no responde (el Tunnel devuelve error de origen).
11. `bin/artesa-deploy deploy <id> --expect-commit <sha> --dry-run` otra vez: ahora el gate
    del puerto da `WARN` (libre, servicio parado) y el resto debe pasar. Luego sin
    `--dry-run`: smoke del candidato en 8001, switch de `current`, `systemctl restart` de la
    unit nueva y smoke final en 8000. **Fin del downtime** cuando el deploy termina con
    exit 0. Si falla, no hay release anterior al que volver automáticamente (exit 54,
    primer despliegue): la vuelta atrás manual es reinstalar la copia de la unit legada del
    paso 8, `sudo systemctl daemon-reload` y `sudo systemctl start artesa-nfc.service`, que
    devuelve el servicio al directorio legado intacto.
12. Conservar el directorio legado `artesa-nfc-backend` 14 días (D2) antes de retirarlo.

**Por qué hace falta esta parada, y solo una vez.** El proceso legado y el primer release
no pueden escuchar a la vez en `127.0.0.1:8000`, y el primer release no tiene un release
anterior bajo el layout nuevo: no existe el cambio "reinicio sobre `current`" que usa el
flujo normal. Duración esperada: la del deploy (candidato + reinicio + smoke, típicamente
uno o dos minutos, sin migraciones si la DB ya está en el head del release); anunciarla y
hacerla en la ventana aprobada por el PO. **Después de la adopción esto deja de ser el
flujo normal:** la unit ya corre desde `current`, el puerto 8000 lo tiene el release activo
(el gate pasa), y cada despliegue es solo `systemctl restart` con rollback según §7. Si en
un despliegue normal el gate del puerto vuelve a fallar, **no** se para nada a mano: es un
proceso ajeno que hay que investigar (§14).

### 11.2 Despliegue normal

```bash
bin/artesa-deploy status
bin/artesa-deploy prepare <id>
bin/artesa-deploy deploy  <id> --expect-commit <sha12+> --dry-run
bin/artesa-deploy deploy  <id> --expect-commit <sha12+>            # CODE_ONLY
bin/artesa-deploy deploy  <id> --expect-commit <sha12+> --allow-migration   # MIGRATION_DEPLOY
```

Si `ops/` cambió en el release, instalar la herramienta **con la copia del propio
release** (camino canónico, §11.7), después del deploy:

```bash
/usr/bin/python3 -I -B /home/energias/artesa-nfc/releases/<id>/ops/artesa_deploy.py install-tools <id>
bin/artesa-deploy status        # línea "tooling": bin/ops, TOOL.json, installer matched target: yes
```

### 11.3 Rollback

`bin/artesa-deploy rollback --dry-run` → revisar la compatibilidad por ledger →
`bin/artesa-deploy rollback` (o `--to <id>`).

### 11.4 Activación fallida o interrumpida

- Exit 50: ya volvió solo al release anterior (code-only). Revisar la evidencia: `rollback.json` dice si hizo falta reiniciar (`strategy`).
- Exit 51: el rollback automático tampoco quedó sano. La herramienta imprime `STATE:` (symlinks, qué proceso sirve el puerto, marcador) y `NEXT:`. → `status`, logs de la unit, `rollback --to <id>` a un release conocido bueno.
- Exit 54 (migración aplicada, sin rollback automático): decidir. Si el ledger lo permite, `rollback`; si no, fix forward o §11.5.
- Exit 11 con `privilege-preflight.json` en fallo: `sudo -v` no se completó; **nada cambió**. Repetir (introduciendo la contraseña) o usar `--restart-mode manual`.
- Herramienta interrumpida (Ctrl-C o EOF): exit 3, `result.json` `interrupted`, evento `deploy_interrupted`/`rollback_interrupted`, y la salida muestra `STATE:`/`NEXT:`. El marcador se conserva. Si `current` **no** es lo que sirve el puerto (p. ej. `current` → nuevo con el proceso antiguo aún vivo), reiniciar el servicio o `rollback` antes de resolver.
- Corte sin registro (kill, apagado): `status` muestra `unresolved activation` y el `result.json` sigue `in-progress`.
- En ambos casos, si el release activo está sano: `resolve-activation --dry-run` y `resolve-activation` (comprueba salud, pide `resolve`, cierra la evidencia pendiente); si no: `rollback`.

### 11.5 Recuperación desde backup (decisión humana, nunca automática)

1. Parar el servicio (paso privilegiado).
2. Verificar el dump: `sha256sum -c <dump>.sha256`; opcional `restore-check <dump> --ephemeral`.
3. Restaurar sobre la base de producción es una operación destructiva que la
   herramienta **no** automatiza: hacerla con `pg_restore --clean --if-exists`
   y el rol propietario, con las credenciales en el entorno, en una ventana
   acordada.
4. `rollback --to` el release cuyo head coincide con la revisión restaurada; reiniciar; smoke.

### 11.6 Tareas con `shared/.env` (D18)

`bin/artesa-deploy run alembic current|heads|history|check` y
`bin/artesa-deploy run provision <args>` ejecutan en el venv de `current` con el
entorno compuesto desde `shared/.env`: no hace falta `source` ni pegar
`DATABASE_URL`. La salida va solo al terminal. Las migraciones nunca pasan por
aquí.

### 11.7 Herramienta instalada (`install-tools`, #131)

Un proceso Python no cambia de código porque cambie un symlink: toda la invocación de
`install-tools` la gobierna el código que la **ejecuta**. Con `bin/artesa-deploy
install-tools <id>` ese código es la herramienta *ya instalada* (el release anterior): el
2026-09-27 la de R2 instaló la de R3 y `TOOL.json` salió con las reglas de R2 (0664).

**Camino canónico:** ejecutar la herramienta del release destino desde su directorio
inmutable y ya verificado:

```bash
/usr/bin/python3 -I -B <root>/releases/<id>/ops/artesa_deploy.py install-tools <id>
```

No hay self-exec ni un comando en dos fases. Lo que hace `install-tools`:

- Los archivos a instalar salen del **MANIFEST del destino**: todos los `ops/*.py` de
  primer nivel salvo `build_release.py` (el builder no corre en el servidor), más los
  **lanzadores** `ops/bin/*` (#137). Un módulo nuevo en un release futuro se instala
  aunque el instalador no lo conozca.
- Lanzadores (#137): `ops/bin/artesa-<nombre>` ejecuta exactamente
  `ops/artesa_<nombre>.py` (su única línea `exec /usr/bin/python3 -I -B
  "$here/ops/artesa_<nombre>.py" "$@"`), y cada `artesa_<nombre>.py` es un comando que
  **debe** traer su lanzador. `artesa-deploy` es obligatorio. Un módulo de comando sin
  lanzador, un lanzador que ejecuta otro módulo o cualquier otro archivo en `ops/bin/`
  → exit 11 y no se instala nada. Así un comando nuevo no puede quedar sin su entrada en
  `bin/` (R5 instaló `artesa_backup.py` pero no `bin/artesa-backup`).
- Staging `bin/.ops-<id>.tmp-<pid>` → cada archivo comparado con el MANIFEST → 0444,
  directorio 0555 → `rename` atómico a `bin/ops-<id>`; si falla, el staging propio se
  borra y `bin/ops` no cambia.
- Si `bin/ops-<id>` ya existe se **verifica** (nombres, SHA-256, modos 444/555, nada de
  más): idéntico → se reutiliza (idempotente); distinto → exit 11, **no** se reutiliza ni
  se borra; el operador lo inspecciona y lo retira a mano (`chmod -R u+w`, `rm -r`).
- `bin/ops` → `ops-<id>` (symlink atómico); lanzadores: todos se copian a
  `bin/.<nombre>.tmp-<pid>`, se comparan con el MANIFEST y pasan a 0755 **antes** de
  sustituir ninguno (`os.replace`); si falla, se borran las copias y `bin/` no cambia. Un
  lanzador que registraba el `TOOL.json` anterior y el destino ya no trae se retira.
  `TOOL.json` (tmp + 0644 + `rename`). Con el lock tomado se borran restos de
  instalaciones interrumpidas (`.ops-*.tmp-*`, `.ops.tmp-*`, `.artesa-*.tmp-*`,
  `.TOOL.json.tmp-*`).
- Si el instalador no es la herramienta del destino (hashes distintos), avisa (`WARNING`)
  y muestra el comando canónico. No bloquea: los archivos instalados son siempre los del
  destino.

**`TOOL.json` v2** (compatible: un archivo sin `schema_version` es v1, R1–R3):

```json
{
  "schema_version": 2,
  "release_id": "<destino>", "git_commit": "<destino>", "tool_version": "<TOOL_VERSION del destino>",
  "installed_at": "...", "files": { "<nombre>": "<sha256>" },
  "launchers": { "artesa-backup": "<sha256>", "artesa-deploy": "<sha256>" },
  "installer": {
    "path": "<artesa_deploy.py que se ejecutó>", "release_id": "<o null>", "git_commit": "<o null>",
    "artesa_deploy_sha256": "...", "tool_version": "<TOOL_VERSION del instalador>", "matches_target": true
  }
}
```

El primer nivel describe el **estado instalado**; `installer`, la **ejecución** (medida:
hash del archivo en ejecución y de sus módulos hermanos; `matches_target` es la igualdad
de esos hashes con los del destino). El deploy-log registra lo mismo en `detail`
(`installer=<id> sha=<12> matches_target=<bool>`). `launchers` (#137) es una clave
**añadida** al schema 2: los lectores anteriores la ignoran y un `TOOL.json` sin ella
(instalado por ≤ 1.3.0) sigue siendo válido. `TOOL_VERSION`: `1.2.0` en R4, `1.3.0` con
D10.1 (R5), `1.3.1` con #137 (instalación de lanzadores), `1.4.0` con D10.2 (copias fuera del
host: `ops/backup_remote.py`, `artesa-backup remote-check`).

**`bin/ops` y `TOOL.json` no coinciden.** `status` lo muestra como `TOOLING WARNING`
(solo lectura, sin cambiar el código de salida): `bin/ops` apunta a otro release que
`TOOL.json`, un archivo instalado difiere de `TOOL.json`, falta o sobra un módulo, el
modo de `TOOL.json` no es 0644, o un **lanzador** (#137) falta (uno por cada
`artesa_*.py` de `bin/ops` y los que registra `TOOL.json`), no es 0755, difiere de
`TOOL.json`, no ejecuta su módulo, ese módulo falta en `bin/ops`, o sobra (p. ej.
`TOOLING WARNING: missing launcher bin/artesa-backup`, el estado real tras R5). Es lo que deja un corte entre el cambio de `bin/ops` y la
escritura de `TOOL.json`. Recuperación: volver a ejecutar el camino canónico para el
release al que apunta `bin/ops` (o el que se quiera instalar); limpia los restos y
reescribe `TOOL.json`. Si `bin/artesa-deploy` no arranca (herramienta rota), cualquier
comando puede ejecutarse igual desde `releases/<id>/ops/artesa_deploy.py`, incluido
`rollback`.

### 11.8 Bajar el release de GitHub (`fetch`) y activar Gestión (`ui`)

Desde TOOL 1.9.0, un commit de `main` que pasa Release CI se publica como
**GitHub Release** `release-<release_id>`. El trabajo `publish` sube cuatro
archivos: el artefacto del backend, `admin-ui-<commit12>.tgz` (construido del
mismo commit) y el `.sha256` de cada uno. El repositorio es público, así que
el servidor los lee sin credenciales. **Publicar no es desplegar.**

```bash
cd /home/energias/artesa-nfc
bin/artesa-deploy fetch                 # el último publicado; o: fetch <release_id>
bin/artesa-deploy prepare <id>          # fetch imprime las líneas exactas
bin/artesa-deploy deploy --expect-commit <commit12> <id>     # + --allow-migration si prepare lo pide
bin/artesa-deploy ui <commit12>         # Gestión: instala y cambia current
bin/artesa-deploy ui                    # muestra la versión activa y las instaladas
```

**`fetch`:**

- Consulta la API de releases y descarga por HTTPS verificado; las
  redirecciones solo pueden ir a HTTPS.
- Comprueba cada archivo contra su `.sha256` antes de guardarlo, con
  escritura atómica en `incoming/`.
- Nunca sobrescribe un archivo distinto que ya esté ahí.
- **No** prepara ni despliega nada.

La cadena de confianza es la misma que con `scp`. `prepare` vuelve a
verificar el artefacto completo (MANIFEST, RELEASE.json), y
`deploy --expect-commit` lo ata al commit que el operador espera.

**`ui <commit12>`:**

- Si `shared/admin-ui/<commit12>` no existe, verifica
  `incoming/admin-ui-<commit12>.tgz` contra su `.sha256` y extrae solo
  archivos y directorios con nombres seguros. Rechaza enlaces, `..`, rutas
  absolutas y ocultos.
- Exige `index.html`, renombra a su sitio y cambia `current` de forma
  atómica.
- Si el directorio ya existe, lo reutiliza. Así se hace el **rollback**
  (`ui <commit anterior>`); la salida imprime ese comando.
- No hace falta reiniciar ningún servicio.

**Si GitHub no responde** (por ejemplo, el FortiGate intercepta el HTTPS,
Brain B-027): `fetch` falla sin escribir nada. La ruta de siempre sigue
funcionando:

1. `scp` del artefacto y del `.tgz` a `incoming/`;
2. después, `prepare`, `deploy` y `ui` igual que arriba.

El primer uso necesita la herramienta 1.9.0 instalada (`install-tools`,
§11.7) desde un release que la incluya.

## 12. Códigos de salida

| Código | Significado |
|---|---|
| 0 | correcto (o nada que hacer) |
| 1 | error interno (detalle suprimido a propósito) |
| 2 | uso incorrecto (argumentos, puerto reservado, `--expect-commit` ausente/mal formado) |
| 3 | sin TTY o abortado por el operador |
| 10 | artifact inválido (checksum, MANIFEST, RELEASE.json, commit no esperado, canal) |
| 11 | preflight (layout, lock, activación pendiente, unit, puerto, release no preparado, venv ≠ lock) |
| 12 | prepare falló |
| 20 / 21 | configuración / `APP_ENV` |
| 22 | candidato no arranca |
| 23 | Alembic: revisión desconocida, DB por delante, probe fallido |
| 30 | migración no autorizada (falta `--allow-migration` o es `breaking`) |
| 31 | backup o restore-check fallido (producción no migrada) |
| 32 | la migración falló: estado de la DB **incierto**, sin rollback automático |
| 40 | smoke fallido |
| 50 | activación fallida → rollback automático correcto (code-only) |
| 51 | rollback no completado: intervención manual |
| 52 | rollback incompatible con la DB según el ledger |
| 53 | origen sano pero el borde público falla (Cloudflare/Tunnel): sin rollback |
| 54 | activación fallida y rollback automático **no** intentado (migración aplicada, `--no-auto-rollback`, o primer despliegue) |

## 13. Pasos privilegiados (sudo interactivo, D14)

La herramienta nunca usa `sudo -n` ni `NOPASSWD`. Solo hay dos pasos con
privilegios y ambos pueden hacerse a mano:

| Paso | Cuándo | Cómo |
|---|---|---|
| Reiniciar el servicio | cada `deploy`/`rollback` | por defecto (`--restart-mode sudo`) la herramienta ejecuta `sudo -v` (preflight) y luego `sudo systemctl restart artesa-nfc.service` en el TTY. Con `--restart-mode manual` imprime el comando, el operador lo ejecuta en una **segunda sesión SSH** y teclea `restarted`. |
| Instalar/cambiar la unit | adopción inicial o cambios de la unit | a mano: copiar la unit, `sudo systemctl daemon-reload`, `sudo systemctl restart artesa-nfc.service`. La herramienta solo **lee** la unit (`systemctl show`). |

Parar el servicio para una restauración (§11.5) también es manual.

### 13.1 Restart privilegiado (#130)

Motivo: en la activación de R3 (2026-09-27) los dos `sudo systemctl restart` (activación
y auto-rollback) esperaron la contraseña hasta el timeout de 180 s; `systemctl` nunca se
ejecutó, la herramienta solo dijo `systemctl restart … failed` y el rollback terminó en
exit 51 aunque el release anterior seguía sirviendo.

- **Preflight `sudo -v`.** Después del candidato y **antes** de escribir el marcador o
  cambiar symlinks, la herramienta ejecuta `sudo -v` en el TTY (120 s para contestar) y lo
  anuncia. Si falla o expira: exit 11, sin marcador, sin symlinks cambiados,
  `privilege-preflight.json` con el código de retorno. Con las credenciales ya en caché, el
  restart posterior no pide contraseña (mismo TTY, dentro de `timestamp_timeout`).
- **Diagnóstico.** El restart compara `InvocationID`/`ExecMainPID` (`systemctl show`, sin
  privilegios) antes y después y conserva código de retorno y duración: distingue timeout
  de sudo (124, sin reinicio), comando no encontrado (127), sudo rechazado antes de llegar
  a systemd (`not-restarted`) y un fallo real de `systemctl` (la unit sí se reinició).
- **Auto-rollback *health-first*.** Tras restaurar los symlinks, si el release de origen
  sigue siendo el proceso del puerto y está sano, se verifica (smoke completo) y se cierra
  con exit 50 **sin reiniciar**. Solo si no lo está se reinicia; y si el mecanismo sudo ya
  falló de forma estructural (timeout, 127, `not-restarted`) **no se repite**: se pide un
  restart manual en una segunda sesión SSH.
- **Modo manual verificado.** El prompt dice si es el restart de **activación** o de
  **rollback** y pide una segunda sesión SSH. `restarted` solo se acepta si systemd informa
  de un `InvocationID`/`ExecMainPID` nuevo; si no cambió, se avisa y se puede confirmar de
  nuevo (3 intentos) o `abort`. Si systemd no se puede leer, se avisa y deciden los checks
  de salud.
- **Interrupciones.** Ctrl-C o EOF durante `deploy`/`rollback`: `result.json`
  `interrupted`, evento en el deploy-log, marcador conservado y salida `STATE:`/`NEXT:`
  (§11.4). Nada se deshace a espaldas del operador.
- El default de `--restart-mode` **no** cambia (`sudo`). Mitigación mientras la herramienta
  instalada en `bin/` sea anterior a R4: `sudo -v` en la misma terminal justo antes, o modo
  manual con una segunda sesión SSH.

## 14. Modelo de amenazas y fallos

| Amenaza / fallo | Control | Residual |
|---|---|---|
| Código que no pasó por `main` | canal production solo desde historia alcanzable desde `origin/main`; `--expect-commit`; CI obligatorio en `main` | sin firma: un atacante con el artifact y su sidecar en tránsito (D6) |
| Artifact corrupto o manipulado | sidecar SHA-256, MANIFEST, esquema cerrado; re-verificación en `deploy` y comparación con el preparado | idem |
| Tar hostil (traversal, symlinks, bombas) | validación completa en memoria antes de extraer; solo archivos regulares, rutas normalizadas, límites de tamaño | — |
| Secretos en el artifact | allowlist de raíces, rechazo por nombre y por contenido (clave privada), esquema de RELEASE.json | un secreto con forma arbitraria dentro de un `.py` trackeado |
| Secretos en logs/evidencia/argv | `SecretGuard`: argv rechazado, salida saneada, JSON escaneado; credenciales en PG* | los secretos existen en el entorno y en la memoria de los procesos hijos |
| Hot-patch en el servidor | `verify` / preflight detectan cualquier diferencia con el MANIFEST y bloquean | archivos fuera de las raíces del release |
| Deriva del venv | venv por release, `--require-hashes`, venv == lock en `deploy` | — |
| Otro proceso en el puerto 8000 | fail-closed, se reporta pid/cwd, nunca se mata nada | — |
| Candidato sobre un puerto ajeno | 8000 y 8002 reservados | otros servicios futuros en 8001 (cambiar con `--candidate-port`) |
| Migración mala | backup + restore-check + ensayo sobre la copia antes de producción; `breaking` rechazada | una migración `additive` mal clasificada en el ledger (revisión humana en PR) |
| Rollback que rompe el esquema | ledger; sin downgrade | — |
| Despliegue interrumpido | lock `flock`, marcador de activación, evidencia incremental (`interrupted` en Ctrl-C/EOF), `STATE:`/`NEXT:`, `resolve-activation` que cierra la evidencia pendiente | un kill -9 o apagado no deja `interrupted` (queda `in-progress`) |
| Restart privilegiado que no llega a systemd (sudo espera contraseña) | preflight `sudo -v` antes del switch; diagnóstico por `InvocationID`/`ExecMainPID`; rollback *health-first*; sin repetir sudo tras un fallo estructural (§13.1) | el operador sigue teniendo que introducir la contraseña (D14) |
| Crash-loop sin límite | límites de reinicio recomendados en la unit | decisión pendiente (§15) |
| Pérdida del host | backups pre-migración solo en el host | backups fuera del host: issue separado (D10) |
| Wheels nuevos en PyPI para una versión ya fijada | el lock del repo no cambia y sigue instalando con sus hashes | `release-ci` puede fallar al regenerar el lock (riesgo conocido, §6) |

### 14.1 Controles negativos

Un control que no puede fallar no prueba nada. `backend/tests/ops/test_ops_negative_controls.py`
rompe deliberadamente una cosa por test y comprueba que el control lo detecta:

| NC | Se rompe | Debe | Código |
|---|---|---|---|
| NC-01 | `.env` en el árbol o en el archivo | no entra / artifact rechazado | 10 |
| NC-02 | secreto en argv | nada se ejecuta | 1 |
| NC-03 | MANIFEST alterado | artifact rechazado | 10 |
| NC-04 | artifact equivocado (id/sidecar) | rechazado | 10 |
| NC-05 | módulo ausente en el venv | prepare falla | 12 |
| NC-06 | revisión de DB desconocida | deploy rechazado | 23 |
| NC-07 | otro proceso en el puerto 8000 | deploy rechazado, nada se mata | 11 |
| NC-08 | backup imposible | no hay migración | 31 |
| NC-09 | backup que no restaura | no hay migración | 31 |
| NC-10 | migración `breaking` | rechazada aunque haya `--allow-migration` | 30 |
| NC-11 | candidato falla | nada se activa | 40 |
| NC-12 | reinicio falla | rollback automático solo si code-only | 50 / 54 |
| NC-13 | rollback incompatible con la DB | rechazado | 52 |
| NC-14 | release id malformado | rechazado | 10 / 2 |
| NC-15 | tar con traversal | rechazado antes de extraer | 10 |

## 15. CI

- `.github/workflows/backend-ci.yml`: tests + migraciones en PR/push a `develop` **y `main`** (D17).
- `.github/workflows/release-ci.yml` (PR/push a `main`, manual): lock instalable
  con hashes y reproducible con pip-tools; `alembic upgrade head` + `check` +
  un solo head (PostgreSQL 18); suite completa en Python 3.14 **sobre las
  dependencias exactas de producción** (`requirements-prod.lock` con hashes, más
  solo `pytest`/`httpx` de `requirements.txt`, restringidos a los pins del lock y
  con verificación de que ningún pin cambió); protección de
  árbol sucio; artifact construido dos veces y comparado byte a byte (canal
  production en push a `main`); sidecar, MANIFEST, RELEASE.json y exclusión de
  secretos; y el ensayo completo del deploy tool (dry-runs, code-only,
  migración, rollbacks, rechazos) contra PostgreSQL 18 desechable. **No
  despliega**: no tiene secretos ni acceso al servidor. El artifact se publica
  como artifact del workflow solo para revisión.

Decisiones que siguen abiertas para el Product Owner: política de reinicio de la
unit (`Restart=`, `StartLimit*`), cuándo se adopta el layout en el servidor, y
el issue de backups programados/fuera del host/cifrados.
