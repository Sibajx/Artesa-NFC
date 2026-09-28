# ArtesaNFC — Backups cifrados de la base de datos (D10, #126)

> **Estado: D10.1 — base local cifrada.** Esto **no** es D10 completo: todavía no hay
> copia fuera del host (D10.2) ni recuperación demostrada con la clave offline (D10.3).
> `artesa-backup status` lo dice siempre: `OFFSITE: NOT CONFIGURED` y `D10: INCOMPLETE`.
> #126 sigue abierto hasta cumplir la Definition of Done (§11).

## 1. Decisiones aprobadas (2026-09-27)

| Tema | Decisión |
|---|---|
| RPO | **24 h** (un backup diario) + backup manual (`artesa-backup run`) después de cada sesión de provisioning o carga importante |
| RTO | Base de datos **≤ 1 h**. Servicio completo en un host nuevo **≤ 1 día laborable**; incluye trabajo manual: aprovisionar el host, PostgreSQL y roles, secretos, Tunnel, recuperar las claves offline, preparar y activar un release compatible |
| Cifrado | `age`, en el cliente, antes de que nada salga del host. En el servidor solo hay **destinatarios públicos**; las claves privadas nunca están en easerver |
| Destinatarios | **K1** (principal: gestor de contraseñas + copia offline) y **K2** (independiente: otra ubicación física o custodia separada). Cada backup se cifra para los dos |
| Plaintext | **Cero backups persistentes en claro** (§4) |
| Fuera del host | Backblaze B2, **provisional**: sujeto a una prueba de egress real desde easerver (inspección TLS de Fortinet). Pertenece a D10.2; **no está activo** |
| Herramienta | `artesa-backup`, separada de `artesa-deploy` (su propio lock, estado y ciclo de vida) |
| Programación | systemd `artesa-backup.service` + `.timer`, a diario ~03:30 America/Mexico_City |
| Pruebas de restauración | restore-check local en **cada** backup; simulacro fuera del host con la clave offline mensual los 3 primeros meses del piloto y trimestral después; prueba de K2 semestral |
| Retención | Local: 7 backups cifrados. Remota (D10.2): daily 35 d / weekly 91 d / monthly 400 d por lifecycle del proveedor; el servidor no puede borrar |

La base se respalda **completa**, incluidas las tablas legadas que aún se están
investigando (issue #134). Finanzas queda fuera.

## 2. Qué se respalda y qué no

- **Sí:** la base PostgreSQL `artesa_nfc` completa (`pg_dump -Fc --no-owner
  --no-privileges`), más metadatos de recuperación dentro del bundle cifrado:
  `RELEASE.json` del release activo, `bin/TOOL.json` y `deploy-log.jsonl`.
- **No:**
  - releases y artifacts (reproducibles byte a byte desde Git + CI);
  - venvs;
  - media (no existe en disco hoy);
  - **ningún secreto**: `shared/.env` no entra en el backup. La contraseña de la base se
    regenera al recrear el rol y el token del Tunnel se reemite desde Cloudflare;
  - roles y grants de PostgreSQL (se recrean, §9).

## 3. Layout

```text
<root>/shared/backup/            0700 -- separado de shared/backups/ (dumps previos a migraciones de artesa-deploy)
├── backup.env                   0600 -- SOLO destinatarios públicos K1/K2
├── backup.lock                  flock propio (independiente del deploy lock)
├── staging/                     0700 -- plaintext temporal de UNA ejecución; vacío al terminar
├── encrypted/                   0700
│   └── <backup_id>/             0700 -- backup_id = <UTC YYYYMMDDTHHMMSSZ>-<commit12 del release activo>
│       ├── bundle.tar.age       0600 -- age v1, dos destinatarios X25519
│       └── meta.json            0600 -- metadatos PÚBLICOS mínimos
└── state/                       0700
    ├── status.json              0600 -- estado consultable (atómico)
    └── backup-log.jsonl         0600 -- eventos, claves en allowlist, sin secretos
```

`backup.env`:

```bash
ARTESA_BACKUP_AGE_RECIPIENT_K1=age1...
ARTESA_BACKUP_AGE_RECIPIENT_K2=age1...
```

Debe ser un archivo regular, 0600 y del usuario del servicio. Si contiene algo con forma
de clave privada (`AGE-SECRET-KEY-…`), la herramienta se niega a ejecutarse.

## 4. Tubería de `artesa-backup run`

1. Toma `backup.lock`. Si otra ejecución lo tiene: exit 11, sin tocar el estado.
2. Borra restos de `staging/` de una ejecución interrumpida (podrían tener plaintext).
3. Lee K1/K2 (obligatorios, distintos), comprueba `age`, lee `DATABASE_URL` de
   `shared/.env` y el release y commit activos.
4. Crea `staging/<backup_id>.tmp-<pid>/` (0700).
5. `pg_dump -Fc` a `database.dump` (0600). Las credenciales van solo en variables PG*,
   nunca en argv. El **deploy lock** se toma solo mientras dura el `pg_dump`; si un deploy
   lo tiene, espera hasta 10 min y después registra `deferred`. El orden es siempre
   backup lock → deploy lock y `artesa-deploy` nunca toma el backup lock, así que no hay
   deadlock posible.
6. Verificación: dump no vacío, TOC legible con `alembic_version` y todas las tablas
   vivas; sha256.
7. **restore-check local:** un cluster PostgreSQL 18 desechable (`initdb`, solo socket,
   contraseña de un solo uso) → `pg_restore` → revisión Alembic, tablas y conteos
   idénticos → cluster destruido. Nunca toca producción.
8. `manifest.json` completo (§5) y bundle tar: dump + manifest + recuperación.
9. `age --encrypt -r K1 -r K2`. Después se comprueba el cifrado **sin clave privada**:
   cabecera `age-encryption.org/v1`, exactamente dos estrofas X25519, línea de MAC y
   payload no vacío. Se calcula el sha256.
10. `rename` atómico de `encrypted/.<id>.partial/` a `encrypted/<id>/`.
11. **Unlink** del dump, el sidecar, el manifest y el tar; se comprueba que `staging/`
    queda vacío y que el backup final solo contiene `bundle.tar.age` y `meta.json`.
12. Estado (`status.json`, atómico), log y retención local.

**Ante cualquier fallo:**
- no queda backup final ni plaintext (se borra el staging);
- exit ≠ 0 (la unit queda `failed`);
- `consecutive_failures` sube y `last_error` guarda el mensaje saneado.

**No hay vuelta atrás a un backup sin cifrar:** si faltan K1/K2 o `age`, no se hace
backup.

**Cero plaintext persistente.** El plaintext solo existe en `staging/` durante la
ejecución y se borra (unlink) en cuanto el cifrado verificado está en su sitio. En un SSD
o en un filesystem con journal, **unlink no es un borrado seguro**. La garantía es la
siguiente: no hay persistencia deliberada, el staging es privado (0700/0600), la
exposición dura lo mínimo y se limpian los restos al arrancar. Como no se guarda ninguna
copia en claro, cualquier restauración (también la local) necesita K1 o K2.

## 5. Manifest (dentro del cifrado) y `meta.json` (público)

`manifest.json` (schema 1), solo dentro del bundle cifrado:

```json
{
  "schema_version": 1, "backup_id": "...", "created_at": "...Z", "hostname": "...", "backup_tool_version": "1.3.0",
  "application": {"release_id": "...", "git_commit": "..."},
  "db": {"engine": "postgresql", "server_version": 180006, "server_major": 18, "pg_dump_version": "...",
         "dump_format": "custom (pg_dump -Fc --no-owner --no-privileges)", "alembic_revision": "...",
         "table_counts": {"...": 0}, "roles_and_grants": "not included (recreate per docs/BACKUP.md)"},
  "files": {"database.dump": {"size": 0, "sha256": "..."}},
  "restore_check": {"at": "...", "ok": true, "target": "ephemeral-cluster", "tables": 0, "alembic_revision": "...",
                    "counts_match": true, "target_destroyed": true},
  "encryption": {"algorithm": "age", "recipient_type": "X25519", "recipients": ["age1...", "age1..."]},
  "remote": {"status": "not-configured"}
}
```

`meta.json`, junto al cifrado y **sin datos sensibles**: `backup_id`, fecha, versión,
`encrypted` (nombre, tamaño, sha256), `encryption` (destinatarios públicos),
`restore_check.ok` y `remote.status`. Es el contrato que D10.2 subirá junto al cifrado.

**Nunca** contienen `DATABASE_URL`, contraseñas, claves privadas ni secretos de proveedor.

## 6. Comandos

| Comando | Qué hace |
|---|---|
| `artesa-backup run [--scheduled]` | La tubería de §4. No necesita TTY (systemd) |
| `artesa-backup status [--json]` | Último intento y último éxito, restore-check, antigüedad, fallos seguidos, cifrado, `OFFSITE: NOT CONFIGURED`, `D10: INCOMPLETE`; `STALE` si pasan más de 26 h sin éxito. Exit 0 si está al día; 11 si está `STALE` o el último intento falló |
| `artesa-backup verify [<id> \| --all]` | **Sin clave privada:** archivos, tamaño y sha256 frente a `meta.json`, cabecera age con 2 destinatarios, modos, coherencia con el estado. No prueba que el contenido descifre: eso solo lo demuestra una restauración (D10.3) |
| `artesa-backup restore-test <ruta>` | restore-check sobre un plaintext **dado explícitamente**: un dump de `artesa-deploy` con su `.json`, o un bundle ya descifrado **fuera del servidor** y extraído (`database.dump` + `manifest.json`). **Nunca descifra**: el servidor no tiene claves privadas |

En producción se ejecuta la copia instalada con su lanzador,
`/home/energias/artesa-nfc/bin/artesa-backup <comando>`, que equivale a `/usr/bin/python3
-I -B /home/energias/artesa-nfc/bin/ops/artesa_backup.py <comando>`. `install-tools` instala
el módulo con el resto del tool (#131) y, desde `TOOL_VERSION` 1.3.1, también el lanzador
`bin/artesa-backup` (#137). R5 (1.3.0) instaló el módulo pero **no** el lanzador: la
activación de D10.1 necesita un release con 1.3.1 o posterior.

## 7. Claves (K1, K2): se generan **fuera** del servidor

En una máquina de confianza del operador, nunca en easerver:

```bash
age-keygen -o artesa-backup-K1.key     # imprime "Public key: age1..."
age-keygen -o artesa-backup-K2.key
age-keygen -y artesa-backup-K1.key     # vuelve a mostrar la pública
```

- **K1:** en el gestor de contraseñas, más una copia offline (papel o USB). **K2:** en
  otra ubicación física o bajo custodia separada. Opcional: proteger cada archivo de
  identidad con passphrase (`age -p`).
- Al servidor **solo** van las dos líneas `age1...` (en `shared/backup/backup.env`,
  0600). **Nunca** copiar allí `*.key` ni nada que empiece por `AGE-SECRET-KEY-`.
- Las claves públicas se registran en el Brain y en el runbook para poder comprobar a qué
  destinatarios se cifró cada backup (`meta.json`).
- **Rotación:** añadir la clave nueva como destinatario (hoy dos fijos: rotar significa
  sustituir K1 o K2 en `backup.env`) y **conservar la privada antigua** hasta que caduque
  el último backup cifrado con ella (≥ 400 días con la retención remota aprobada).
- Para cerrar D10, K2 debe existir, estar separada de K1 y haberse usado al menos una vez
  para restaurar.

## 8. Retención local

- Se conservan los **7** backups cifrados correctos más recientes (por `backup_id`).
- Nunca se borra el último válido, es decir, el más reciente que pase `verify`.
- Solo se tocan directorios con nombre de `backup_id` dentro de `encrypted/`.
- **Nunca** se toca `shared/backups/` (N-08).
- Con D10.2, tampoco se borrará un backup sin copia remota verificada.
- Un fallo de la retención queda como aviso en el estado y no borra el backup recién
  creado.

## 9. Recuperación: límites de D10.1

- La herramienta **no descifra ni restaura sobre producción**. Una restauración real es
  manual (`DEPLOYMENT.md` §11.5) y ahora requiere además descifrar con K1 o K2 **fuera
  del servidor**:
  1. copiar `bundle.tar.age` a la máquina de confianza;
  2. `age -d -i artesa-backup-K1.key -o bundle.tar bundle.tar.age`;
  3. `tar -xf bundle.tar`;
  4. comprobar `sha256sum database.dump` frente a `manifest.json`;
  5. ensayarlo con `artesa-backup restore-test <dir>` o `pg_restore` en un PostgreSQL 18
     desechable;
  6. solo entonces, el procedimiento de restauración real.
- Roles y grants no están en el dump. Hay que recrear el rol de la aplicación
  (`NOSUPERUSER NOCREATEDB NOCREATEROLE`) y la base con ese propietario antes de
  `pg_restore --no-owner`.
- El runbook completo de recuperación en un host nuevo y los simulacros son D10.3.

## 10. systemd (ejemplos, no instalados)

`backend/ops/systemd/artesa-backup.service.example` y `.timer.example`:
- `oneshot`, `User=energias`, `UMask=0077`, `TimeoutStartSec=45min`, prioridad baja;
- `OnCalendar=*-*-* 03:30:00 America/Mexico_City`, `RandomizedDelaySec=15min`,
  `Persistent=true`;
- hardening: `NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`, `ProtectSystem=strict`,
  `ProtectHome=read-only` + `ReadWritePaths=<root>/shared`, `ProtectKernel*`,
  `ProtectControlGroups`, `ProtectClock`, `ProtectHostname`, `RestrictSUIDSGID`,
  `RestrictNamespaces`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, sin
  capabilities.
- `MemoryDenyWriteExecute` no se usa (riesgo con Python/ctypes).

**Probado en desarrollo:** un `run` completo (`pg_dump` por TCP, cluster `initdb` por
socket, `age`) bajo `NoNewPrivileges`, `RestrictAddressFamilies`, `RestrictNamespaces`,
`RestrictSUIDSGID`, `LockPersonality`, `SystemCallArchitectures`, `UMask` y la prioridad
baja termina bien.

**No se pudo probar en desarrollo** (un gestor systemd de usuario con
`kernel.apparmor_restrict_unprivileged_userns=1` acepta pero no aplica los namespaces de
montaje, ni puede soltar capabilities): la **aplicación efectiva** de `ProtectSystem`,
`ProtectHome`, `PrivateDevices`, `ProtectKernelModules/Logs`, `ProtectClock` y
`CapabilityBoundingSet=`. Por eso, al instalar la unit de sistema en easerver hay que
comprobar que:
1. `systemctl start artesa-backup.service` termina con exit 0;
2. `status` está al día;
3. un `ExecStartPre` de prueba, o `systemd-run -p ProtectSystem=strict -p
   ReadWritePaths=... touch <root>/releases/x`, **falla** fuera de `shared/`.

**Instalación (pasos privilegiados, D14, manuales):** runbook completo y en orden en
§13. En resumen: `age` → K1/K2 generadas fuera del host → `backup.env` → primer `run`
a mano → units sin timer → `systemctl start` bajo el hardening real → simulacro fuera
del host con K1 **y** con K2 → solo entonces `enable --now` del timer.

## 11. Definition of Done de #126 (D10 completo)

#126 se cierra solo cuando:
- el timer de backup está activo;
- el último backup tiene menos de 26 h;
- el backup está cifrado, fuera del host y con la subida remota verificada;
- el restore-check local pasa;
- el simulacro offline pasa y K2 está probada;
- la credencial del servidor **no puede borrar** copias remotas;
- la retención está activa;
- `status` está limpio y la alerta externa está probada;
- RPO/RTO están documentados y el runbook de restauración está probado;
- tests y rehearsal están en verde.

**Fases:**
- **D10.1** (esta): base local cifrada.
- **D10.2:** fuera del host (prueba de egress, B2 o alternativa, credencial sin borrado,
  Object Lock, lifecycle, alerta externa).
- **D10.3:** recuperación demostrada (simulacro con la clave offline, K2, runbook de host
  nuevo) y cierre de #126.

## 12. Tests y rehearsal

- `backend/tests/ops/test_backup_d10.py`: tubería, fallos sin restos, cifrado, locks,
  retención, estado y status, verify, restore-test. Con fakes; no usa producción.
- `backend/tests/ops/rehearsal/run_backup_rehearsal.py`: PostgreSQL 18 desechable,
  release real construido desde git, `age` real con K1/K2 de usar y tirar. Cubre
  descifrar con K1 **y** con K2, `restore-test` del bundle descifrado, retención, rechazos
  (falta K2, dump corrupto, lock ocupado) y limpieza. Se ejecuta en Release CI.

## 13. Runbook de activación de D10.1 (manual; después de R6)

Requisito: estar en un release con `TOOL_VERSION` ≥ 1.3.1 (R6) y con su tooling
instalado. `bin/artesa-backup` tiene que existir (#137). Cada bloque indica **dónde** se
ejecuta. Los pasos con `sudo` o que crean configuración en el servidor los hace el
operador. Ninguno toca la base de producción, salvo el `pg_dump` de solo lectura.

Estado comprobado el 2026-09-28 (solo lectura):
- easerver: Ubuntu 26.04.1, systemd 259, zona horaria del sistema `Etc/UTC`;
- `age` no instalado; el candidato de apt es `1.2.1-1build1`;
- PostgreSQL 18.6 con `pg_dump`, `initdb` y `pg_ctl` en `/usr/lib/postgresql/18/bin`;
- `shared/backup/` no existe; no hay units ni timers de backup;
- `kernel.apparmor_restrict_unprivileged_userns=1`.

La CI prueba con `age` 1.1.1 (Ubuntu 24.04). El formato age v1 es el mismo; el primer
`run` real con 1.2.1 (paso 5) lo confirma.

```bash
ROOT=/home/energias/artesa-nfc
```

### 13.1 `age` en easerver (servidor)

```bash
sudo apt-get update && sudo apt-get install -y age
age --version                      # esperado: v1.2.1 (paquete 1.2.1-1build1)
command -v age                     # /usr/bin/age: el PATH que usa la unit
```

Si `age` no está disponible, `artesa-backup run` sale con exit 20 (CONFIG) **sin** crear
backup ni plaintext. No hay vuelta atrás a un backup sin cifrar.

### 13.2 K1 y K2 (máquina de confianza del operador; nunca en easerver)

```bash
sudo apt-get install -y age        # en la máquina local
umask 077; mkdir -p ~/artesa-keys && cd ~/artesa-keys
age-keygen -o artesa-backup-K1.key
age-keygen -o artesa-backup-K2.key
age-keygen -y artesa-backup-K1.key > artesa-backup-K1.pub
age-keygen -y artesa-backup-K2.key > artesa-backup-K2.pub
cat artesa-backup-K1.pub artesa-backup-K2.pub        # dos líneas age1... (públicas)
# huella corta, para registrarla en el Brain y compararla con meta.json
for k in K1 K2; do printf '%s  %s\n' $k "$(tr -d '\n' < artesa-backup-$k.pub | sha256sum | cut -c1-16)"; done
```

**Prueba de las dos antes de tocar el servidor:**

```bash
echo "d10 key test $(date -u +%FT%TZ)" > probe.txt
age -r "$(cat artesa-backup-K1.pub)" -r "$(cat artesa-backup-K2.pub)" -o probe.txt.age probe.txt
age -d -i artesa-backup-K1.key probe.txt.age        # imprime el texto
age -d -i artesa-backup-K2.key probe.txt.age        # imprime el texto
rm probe.txt probe.txt.age
```

**Protección y custodia:**

- Cifra cada identidad con passphrase. `age` acepta la identidad cifrada directamente en
  `-i` y pide la passphrase:

  ```bash
  age -p -o artesa-backup-K1.key.age artesa-backup-K1.key
  age -p -o artesa-backup-K2.key.age artesa-backup-K2.key
  age -r "$(cat artesa-backup-K1.pub)" <<<ok | age -d -i artesa-backup-K1.key.age   # pide la passphrase; imprime "ok"
  age -r "$(cat artesa-backup-K2.pub)" <<<ok | age -d -i artesa-backup-K2.key.age
  shred -u artesa-backup-K1.key artesa-backup-K2.key  # solo DESPUÉS de guardar las copias
  ```

- **K1:** el `.key.age` y su passphrase van al gestor de contraseñas como entradas
  separadas, más una copia offline (USB cifrado o papel).
- **K2:** en **otra ubicación física** o bajo custodia de otra persona (sobre sellado o
  caja fuerte), nunca junto a K1.
- **Nunca:** en easerver, en el repositorio, en el Brain, en Nextcloud ni en ninguna
  carpeta sincronizada. El Brain registra **solo** las dos líneas `age1...` y sus huellas.

### 13.3 `backup.env` (servidor; solo recipients públicos)

```bash
install -d -m 700 $ROOT/shared/backup
( umask 077; cat > $ROOT/shared/backup/backup.env <<'ENV'
ARTESA_BACKUP_AGE_RECIPIENT_K1=age1<K1 pública, 58 caracteres>
ARTESA_BACKUP_AGE_RECIPIENT_K2=age1<K2 pública, 58 caracteres>
ENV
)
stat -c '%a %U %F %n' $ROOT/shared/backup $ROOT/shared/backup/backup.env   # 700 / 600 energias, archivo regular
grep -ci 'AGE-SECRET-KEY' $ROOT/shared/backup/backup.env                   # 0
```

La herramienta rechaza con exit 20 (CONFIG, sin tocar nada):
- un archivo que no sea regular o que sea un symlink;
- un modo distinto de 0600 o un dueño distinto del usuario del servicio;
- cualquier texto `AGE-SECRET-KEY-`;
- K1/K2 ausentes o mal formados;
- K1 = K2.

### 13.4 Precheck (servidor, solo lectura)

```bash
cd $ROOT
bin/artesa-deploy status | grep -iE 'current|tooling'    # R6; sin TOOLING WARNING
bin/artesa-backup status; echo "exit $?"                  # sin backups todavía: STALE, exit 11 (esperado)
df -h $ROOT/shared | tail -1                               # espacio: cada backup ocupa hoy ~decenas de KB
```

### 13.5 Primer backup a mano (servidor)

```bash
bin/artesa-backup run; echo "exit $?"         # exit 0: "BACKUP OK <id>: encrypted to K1+K2 ..."
bin/artesa-backup status; echo "exit $?"      # último éxito < 1 h, restore-check ok, exit 0
bin/artesa-backup verify --all                # sin clave privada: tamaños, sha256, cabecera age con 2 recipients, modos
ls -A $ROOT/shared/backup/staging             # vacío
find $ROOT/shared/backup -type f ! -path '*/encrypted/*/bundle.tar.age' ! -path '*/encrypted/*/meta.json' \
     ! -name status.json ! -name backup-log.jsonl ! -name backup.env ! -name backup.lock -print   # nada: cero plaintext
ls -l $ROOT/shared/backup/encrypted/*/        # solo bundle.tar.age y meta.json, 0600
python3 -m json.tool $ROOT/shared/backup/encrypted/*/meta.json | head -30   # recipients = K1, K2; restore_check.ok = true
tail -3 $ROOT/shared/backup/state/backup-log.jsonl
```

La retención local (7) todavía no borra nada. Se ve con 8 o más backups; la rehearsal
de CI la cubre (B4).

### 13.6 Units sin timer y hardening real (servidor)

```bash
ID=<release activo, p. ej. 20260928T071035Z-7c8b51bd6c24>
sudo install -m 0644 -o root -g root $ROOT/releases/$ID/ops/systemd/artesa-backup.service.example /etc/systemd/system/artesa-backup.service
sudo install -m 0644 -o root -g root $ROOT/releases/$ID/ops/systemd/artesa-backup.timer.example   /etc/systemd/system/artesa-backup.timer
sudo systemd-analyze verify /etc/systemd/system/artesa-backup.service /etc/systemd/system/artesa-backup.timer
sudo systemctl daemon-reload
systemctl show artesa-backup.service -p ProtectSystem -p ProtectHome -p ReadWritePaths -p NoNewPrivileges -p CapabilityBoundingSet
systemd-analyze security artesa-backup.service --no-pager | tail -1        # exposición: anotarla (esperado "OK" o "MEDIUM")
# NO habilitar el timer todavía.

# Un backup completo bajo el hardening real (oneshot: vuelve al terminar)
sudo systemctl start artesa-backup.service
systemctl show artesa-backup.service -p Result -p ExecMainStatus          # Result=success ExecMainStatus=0
journalctl -u artesa-backup.service -n 40 --no-pager
bin/artesa-backup status; echo "exit $?"                                  # exit 0, 2 backups locales

# ¿Se aplican de verdad ProtectSystem/ProtectHome? (no se pudo comprobar en desarrollo, §10)
sudo systemd-run --wait --pipe --quiet -p User=energias -p ProtectSystem=strict -p ProtectHome=read-only \
     -p ReadWritePaths=$ROOT/shared -p PrivateTmp=yes -p NoNewPrivileges=yes -- /bin/sh -c "
  touch $ROOT/releases/.d10probe 2>/dev/null && { echo 'BAD: releases/ writable'; rm -f $ROOT/releases/.d10probe; } || echo 'OK: releases/ read-only';
  touch /etc/.d10probe 2>/dev/null && echo 'BAD: /etc writable' || echo 'OK: /etc read-only';
  touch $ROOT/shared/.d10probe && rm $ROOT/shared/.d10probe && echo 'OK: shared/ writable'"
```

Las tres líneas tienen que salir `OK`. Si alguna sale `BAD`, el hardening no se aplica
en este host: **no** habilites el timer y registra el hallazgo antes de seguir.

### 13.7 Simulacro fuera del host con K1 y con K2 (máquina local)

Requisitos: `age` y docker (usa un PostgreSQL 18 desechable y sin red). El script es
`qa/d10-offhost-drill/drill.py`; su autotest es `qa/d10-offhost-drill/selftest.py`.

```bash
BID=<backup_id del paso 13.5 o 13.6>
mkdir -p ~/d10-drill && scp -r energias@100.93.35.86:/home/energias/artesa-nfc/shared/backup/encrypted/$BID ~/d10-drill/
cd ~/artesa-nfc
python3 qa/d10-offhost-drill/drill.py ~/d10-drill/$BID ~/artesa-keys/artesa-backup-K1.key.age   # pide la passphrase de K1
python3 qa/d10-offhost-drill/drill.py ~/d10-drill/$BID ~/artesa-keys/artesa-backup-K2.key.age   # pide la passphrase de K2
# cada uno: "DRILL PASS: backup <BID> recovered with ..." y "plaintext work dir removed: True"
```

El simulacro comprueba:
- `bundle.tar.age` coincide con `meta.json` y lleva 2 recipients;
- el bundle se descifra con esa identidad;
- `database.dump` coincide con el sha256 del manifest;
- `pg_restore` restaura en PostgreSQL 18 con la misma revisión Alembic y los mismos conteos por tabla que el manifest.

El plaintext vive solo en un directorio temporal 0700 que se borra al salir.
`~/d10-drill/` contiene únicamente el cifrado y `meta.json`, que se pueden conservar.

### 13.8 Timer (servidor; solo con 13.5–13.7 en verde)

```bash
sudo systemctl enable --now artesa-backup.timer
systemctl list-timers artesa-backup.timer --no-pager     # próxima ejecución ~03:30 America/Mexico_City (09:30 UTC ± 15 min)
systemctl is-enabled artesa-backup.timer                  # enabled
# al día siguiente:
bin/artesa-backup status; echo "exit $?"                  # último éxito < 26 h, exit 0
journalctl -u artesa-backup.service --since yesterday --no-pager | tail -20
```

**Vuelta atrás** (sin pérdida): `sudo systemctl disable --now artesa-backup.timer`. Los
backups cifrados y `backup.env` se quedan; la herramienta no se toca.

### 13.9 Qué deja D10.1 activo y qué no

- **Activo:** un backup diario cifrado a K1+K2, restore-check local en cada ejecución,
  7 backups locales y `status`.
- **Sigue faltando:**
  - copia fuera del host y alerta externa (D10.2);
  - runbook de host nuevo y simulacros periódicos (D10.3).
- `status` sigue mostrando `OFFSITE: NOT CONFIGURED` y `D10: INCOMPLETE`. #126 sigue abierto.
