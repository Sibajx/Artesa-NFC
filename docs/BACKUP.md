# ArtesaNFC — Backups cifrados de la base de datos (D10, #126)

> **Estado documentado al 2026-09-30:** D10.1 local cifrado, D10.2 fuera del
> host en B2 y D10.3 con restauración remota mediante K1 y K2 están activos o
> demostrados según §§13–15. Desde TOOL 1.7.0, el mismo flujo puede respaldar
> originales de media cuando `MEDIA_ROOT` y `remote.env` están configurados
> (§16); no hay un registro versionado de activación o restore sample de M3.
> Estos son registros operativos fechados; el repositorio no consulta el estado
> vivo del servidor ni de B2.

## 1. Decisiones aprobadas (2026-09-27)

| Tema | Decisión |
|---|---|
| RPO | **24 h** (un backup diario) + backup manual (`artesa-backup run`) después de cada sesión de provisioning o carga importante |
| RTO | Base de datos **≤ 1 h**. Servicio completo en un host nuevo **≤ 1 día laborable**; incluye trabajo manual: aprovisionar el host, PostgreSQL y roles, secretos, Tunnel, recuperar las claves offline, preparar y activar un release compatible |
| Cifrado | `age`, en el cliente, antes de que nada salga del host. En el servidor solo hay **destinatarios públicos**; las claves privadas nunca están en easerver |
| Destinatarios | **K1** (principal: gestor de contraseñas + copia offline) y **K2** (independiente: otra ubicación física o custodia separada). Cada backup se cifra para los dos |
| Plaintext | **Cero backups persistentes en claro** (§4) |
| Fuera del host | Backblaze B2, activado y verificado según el registro fechado de §14.6; credencial del servidor sin capacidad de borrado |
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
  - derivados públicos de media. El repo no contiene una regeneración
    determinista que preserve los `storage_path` ya guardados; después de una
    pérdida requieren restauración aparte o reconciliación. Los originales
    cuentan con el flujo opcional de §16;
  - **ningún secreto**: `shared/.env` no entra en el backup. La contraseña de la base se
    regenera al recrear el rol y el token del Tunnel se reemite desde Cloudflare;
  - roles y grants de PostgreSQL (se recrean, §9).

## 3. Layout

```text
<root>/shared/backup/            0700 -- separado de shared/backups/ (dumps previos a migraciones de artesa-deploy)
├── backup.env                   0600 -- SOLO destinatarios públicos K1/K2
├── remote.env                   0600 -- D10.2 (opcional): destino fuera del host, credencial SIN borrado (§14)
├── backup.lock                  flock propio (independiente del deploy lock)
├── staging/                     0700 -- plaintext temporal de UNA ejecución; vacío al terminar
├── encrypted/                   0700
│   └── <backup_id>/             0700 -- backup_id = <UTC YYYYMMDDTHHMMSSZ>-<commit12 del release activo>
│       ├── bundle.tar.age       0600 -- age v1, dos destinatarios X25519
│       └── meta.json            0600 -- metadatos PÚBLICOS mínimos
└── state/                       0700
    ├── status.json              0600 -- estado consultable (atómico)
    ├── backup-log.jsonl         0600 -- eventos, claves en allowlist, sin secretos
    └── offsite/<backup_id>.json 0600 -- D10.2: objetos remotos verificados (nombre, tamaño, SHA-1, SHA-256, fileId)
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
`restore_check.ok` y `remote.status`. Es el contrato que D10.2 sube junto al cifrado.

**Nunca** contienen `DATABASE_URL`, contraseñas, claves privadas ni secretos de proveedor.

## 6. Comandos

| Comando | Qué hace |
|---|---|
| `artesa-backup run [--scheduled]` | La tubería de §4. No necesita TTY (systemd) |
| `artesa-backup status [--json]` | Último intento y último éxito, restore-check, antigüedad, fallos seguidos, cifrado y estado off-site/media; `STALE` si pasan más de 26 h sin éxito. Exit 0 si está al día; 11 si está `STALE` o el último intento falló |
| `artesa-backup verify [<id> \| --all]` | **Sin clave privada:** archivos, tamaño y sha256 frente a `meta.json`, cabecera age con 2 destinatarios, modos, coherencia con el estado. No prueba que el contenido descifre: eso solo lo demuestra una restauración (D10.3) |
| `artesa-backup restore-test <ruta>` | restore-check sobre un plaintext **dado explícitamente**: un dump de `artesa-deploy` con su `.json`, o un bundle ya descifrado **fuera del servidor** y extraído (`database.dump` + `manifest.json`). **Nunca descifra**: el servidor no tiene claves privadas |
| `artesa-backup remote-check [--ping-deadman]` | D10.2, **solo lectura**. Comprueba el destino fuera del host: TLS verificado, capacidades **reales** de la credencial y sus restricciones (modelo sin borrado). No sube nada. Con `--ping-deadman` envía un ping de prueba al dead-man's switch. Es la prueba de egress desde easerver |

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

## 9. Recuperación: límites del diseño D10.1

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

Los registros de §§13–15 documentan el cumplimiento de estos puntos. La lista
se conserva como criterio auditable para futuras verificaciones.

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

## 13. Runbook histórico de activación de D10.1

Esta sección conserva el procedimiento usado para activar D10.1. El resultado
actual documentado está en §§14.6–15.

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

### 13.9 Alcance al terminar D10.1 (corte histórico)

- **Activo:** un backup diario cifrado a K1+K2, restore-check local en cada ejecución,
  7 backups locales y `status`.
- **Sigue faltando:**
  - copia fuera del host y alerta externa (D10.2);
  - runbook de host nuevo y simulacros periódicos (D10.3).
- `status` sigue mostrando `OFFSITE: NOT CONFIGURED` y `D10: INCOMPLETE`. #126 sigue abierto.

## 14. D10.2 — copias fuera del host

La base se implementó en `TOOL_VERSION` 1.4.0 (`ops/backup_remote.py`, solo
stdlib) y se activó según el registro de §14.6. En cualquier instalación sin
`shared/backup/remote.env`, el comportamiento vuelve al modo local de D10.1.

### 14.1 Qué hace `run` con `remote.env`

1. El backup local se hace igual que en D10.1. Un `remote.env` roto **no** impide el
   backup local, pero la ejecución termina con exit 31 y `OFFSITE: FAILED`.
2. `authorize` con la credencial. Antes de subir nada, el tool **rechaza** (exit 20) una
   clave que:
   - tenga cualquiera de `deleteFiles`, `deleteBuckets`, `writeBuckets`,
     `writeBucketRetentions`, `writeBucketEncryption`, `writeBucketReplications`,
     `writeBucketNotifications`, `deleteKeys`, `writeKeys`, `bypassGovernance`,
     `writeFileRetentions` o `writeFileLegalHolds`;
   - no tenga `writeFiles` y `listFiles`;
   - no esté restringida a **exactamente** el bucket configurado;
   - tenga un prefijo que no cubra el configurado. Sin prefijo solo avisa.

   El módulo no tiene ninguna llamada de borrado ni de *hide*; un test lo comprueba.
3. Por cada backup local sin copia verificada (el nuevo y los anteriores: *backfill*) se
   suben `bundle.tar.age` y `meta.json` a:
   `<prefijo>{daily|weekly|monthly}/YYYY/MM/DD/<backup_id>/`.
   - `daily` siempre, `weekly` los domingos y `monthly` el día 1, según el calendario de
     America/Mexico_City.
   - Cabeceras: SHA-1 (`X-Bz-Content-Sha1`) y SHA-256 (`X-Bz-Info-sha256`).
   - Reintentos: 3, con backoff de 2 s y 8 s; ante 401/408/429/5xx se pide una upload URL nueva.
4. **Verificación:** cada objeto se vuelve a listar y su tamaño, SHA-1 y SHA-256 tienen que
   coincidir con el archivo local.
   - Si el nombre ya existe con el mismo contenido, cuenta como hecho (idempotencia).
   - Si existe con **otro** contenido, es un conflicto: exit 31 y **nunca** se sobrescribe.
5. El registro va a `state/offsite/<backup_id>.json` (0600). El directorio del backup no
   se modifica.
6. **Retención local:** con `remote.env` presente, un backup sin copia remota verificada
   **no** se borra nunca.
7. **Dead-man's switch** (opcional, `ARTESA_BACKUP_DEADMAN_URL`, https): un GET **solo**
   si todo salió bien. La alarma la da el servicio externo cuando falta un ping. Un ping
   fallido solo deja un aviso.
8. **`status`:**
   - `OFFSITE: VERIFIED | FAILED | PENDING`, con la antigüedad de la última copia verificada;
   - `OFFSITE STALE` a las 26 h;
   - exit 11 si el offsite está configurado y falla o está obsoleto;
   - `D10: INCOMPLETE` hasta D10.3.

Pruebas:
- `tests/ops/test_backup_d10_2.py`: 48 tests. Incluyen el cliente B2 real contra un
  B2 falso en memoria (reintentos, 400 sin reintento, cuentas sin restricción o con borrado,
  versiones ocultas, `http` rechazado, secretos nunca en la salida).
- Rehearsal D10, paso B7: bucket falso *write-once* y dead-man's switch en loopback. La
  copia remota se descifra con K1 usando `age` real.

### 14.2 `remote.env` (plantilla; 0600, solo en el servidor)

```bash
ARTESA_BACKUP_REMOTE=b2
ARTESA_BACKUP_B2_KEY_ID=<keyID de la clave de aplicación, NO la master key>
ARTESA_BACKUP_B2_APPLICATION_KEY=<applicationKey (secreto; se muestra una sola vez al crearla)>
ARTESA_BACKUP_B2_BUCKET=<nombre del bucket>
ARTESA_BACKUP_B2_PREFIX=artesanfc/prod/postgres/
ARTESA_BACKUP_DEADMAN_URL=https://hc-ping.com/<uuid>     # opcional
```

- Cualquier otra clave es un error (exit 20). Los valores secretos nunca aparecen en la
  salida, en el estado ni en el log.
- **Nunca** va aquí la master key ni una clave con capacidades de borrado.

### 14.3 Egress desde easerver: comprobado

El 2026-09-28, desde easerver y de solo lectura, sin credenciales:
`GET https://api.backblazeb2.com/b2api/v4/b2_authorize_account` → **HTTP 401** (lo
esperado sin credenciales).
- Certificado de **Let's Encrypt** (`CN=backblazeb2.com`): no hay inspección TLS del
  Fortinet en este camino.
- `urllib` con verificación completa (el camino del tool) conecta.

Falta la prueba con credencial: `remote-check`, paso 5 de §14.4. Los hosts de subida
(`pod-*.backblaze.com`) solo se prueban con la primera subida real.

### 14.4 Runbook de activación de D10.2 (humano; después de D10.1)

1. **Cuenta B2** con MFA (decisión y alta del PO). El tramo gratis cubre el volumen: ~KB
   por backup.
2. **Bucket** privado, con **Object Lock** activado y una retención por defecto (§14.5):

   ```bash
   b2 bucket create --default-server-side-encryption SSE-B2 --file-lock-enabled <bucket> allPrivate
   ```

   Después se fija la retención por defecto en la web o con `b2 bucket update`.
3. **Lifecycle** por prefijo:
   - `…/daily/`: ocultar a los 35 d y borrar 1 d después;
   - `…/weekly/`: 91 d / 1 d;
   - `…/monthly/`: 400 d / 1 d.

   Un objeto bloqueado no se borra antes de que venza su retención.
4. **Clave del servidor**, restringida al bucket y al prefijo, **sin** borrado. Solo se
   muestra una vez; va directa al `remote.env`:

   ```bash
   b2 key create --bucket <bucket> --name-prefix artesanfc/prod/postgres/ artesa-backup-easerver listFiles,writeFiles
   ```

5. **En easerver:**

   ```bash
   (umask 077; ${EDITOR:-nano} /home/energias/artesa-nfc/shared/backup/remote.env)   # plantilla §14.2
   stat -c '%a %U' /home/energias/artesa-nfc/shared/backup/remote.env                 # 600 energias
   /home/energias/artesa-nfc/bin/artesa-backup remote-check     # TLS, capacidades reales, "no-delete model OK"; no sube nada
   ```

6. **Dead-man's switch** (p. ej. healthchecks.io, gratis; decisión del PO): un check con
   periodo de 1 día y gracia de 2 h. Su URL de ping va en `remote.env`:

   ```bash
   bin/artesa-backup remote-check --ping-deadman
   ```

7. **Primera subida real:**

   ```bash
   bin/artesa-backup run; bin/artesa-backup status
   ```

   Esperado: `OFFSITE: VERIFIED` y todos los backups locales subidos (backfill).
8. **La credencial del servidor no puede borrar** (DoD). Desde la máquina del operador,
   con esa misma clave:

   ```bash
   b2 rm b2://<bucket>/<un objeto>
   ```

   Tiene que fallar por permisos. Anotar el resultado.
9. **Consultas periódicas:**
   - `bin/artesa-backup remote-check` vuelve a listar lo verificado y detecta objetos
     ocultados o cambiados (exit 31);
   - el simulacro de D10.3 descarga una copia **remota** y la restaura con K1 y con K2
     (`qa/d10-offhost-drill/drill.py`).

### 14.5 Decisiones previas a la activación

| # | Decisión | Recomendación |
|---|---|---|
| a | Alta de la cuenta B2 y quién custodia la master key (MFA) | PO; master key fuera del servidor, en el gestor de contraseñas |
| b | Object Lock: la retención por defecto del bucket es **una sola** para todos los prefijos | **Un bucket, `governance` 35 d**, más una clave del servidor sin `bypassGovernance`. `weekly`/`monthly` quedan bloqueados 35 d y después solo los protege la clave sin borrado. Alternativa más fuerte: 3 buckets con retención = lifecycle (35/91/400 d), a cambio de 3 claves |
| c | `compliance` frente a `governance` | `governance` al principio: un error de configuración se puede corregir. Pasar a `compliance` cuando el piloto tenga datos reales |
| d | Servicio de dead-man's switch | healthchecks.io (gratis, 1 check), aviso por email |

### 14.6 Activación en producción (2026-09-28)

Decisiones de §14.5 aprobadas por el PO: **(a)** B2, con la master key en el gestor de
contraseñas del PO; **(b)** un bucket con `governance` de 35 d; **(c)** `governance`
por ahora; **(d)** healthchecks.io.

| Paso | Resultado |
|---|---|
| Bucket `artesanfc-backups-prod` | privado, SSE-B2, Object Lock activo, retención por defecto `governance` 35 d, lifecycle daily/weekly/monthly (35/91/400 d + 1 d) |
| Clave del servidor | `listFiles,writeFiles`, restringida al bucket y a `artesanfc/prod/postgres/`; creada por CLI y escrita en `remote.env` sin mostrarse |
| R7 (`20260929T042348Z-1a68d20615fb`, TOOL 1.4.0) | desplegado; `install-tools` a R7 |
| `remote-check --ping-deadman` | `no-delete model OK`, ping de prueba OK, nada subido |
| Primer `run` | backfill de 3 backups, `OFFSITE: VERIFIED`; objetos con `fileRetention` `governance` durante 35 d |
| La credencial no puede borrar (DoD, §14.4 paso 8) | `b2_delete_file_version` con la clave del servidor sobre un `meta.json` real: **HTTP 401 `unauthorized`**, objeto intacto (misma versión). Sonda ejecutada en easerver con los módulos instalados, sin imprimir la clave. No se probó `b2_hide_file`, porque `writeFiles` lo permite y ocultaría el objeto de verdad |

**Lecciones:**
- `b2 account authorize` imprime la clave. Usarlo siempre con `> /dev/null`.
- Nunca copiar una plantilla con marcadores (`<…>`) a `remote.env`: la herramienta
  rechaza una URL que no sea https (exit 20).
- `writeFiles` también permite `b2_hide_file`. La protección frente a un objeto oculto es
  el Object Lock (la versión sigue existiendo) más `remote-check`, que vuelve a listar lo
  verificado.

## 15. D10.3 — recuperación demostrada y host nuevo

### 15.1 Simulacro desde la copia remota (2026-09-28)

Se descargó desde B2 a la máquina del operador el backup
`20260929T043741Z-1a68d20615fb`, con la master key y `b2 file download`. El sha256 del
bundle coincide con `meta.json`. Después se ejecutó
`qa/d10-offhost-drill/drill.py <dir> <K?.key.age>` con cada identidad:

| Identidad | Resultado |
|---|---|
| K1 (`artesa-backup-K1.key.age`, con passphrase) | **DRILL PASS**: descifrado, dump = manifest, restaurado en PostgreSQL 18 desechable (alembic `895974720462`, 13 tablas, conteos idénticos), plaintext borrado |
| K2 (`artesa-backup-K2.key.age`, con passphrase) | **DRILL PASS**, mismos resultados |

**Cadencia:**
- un simulacro remoto por trimestre, alternando K1 y K2;
- además, uno después de cada cambio en el tool de backup o en el esquema.

Cada simulacro se registra con fecha, `backup_id` e identidad.

### 15.2 RPO y RTO

- **RPO ≤ 24 h:** el timer corre a las 03:30 America/Mexico_City (±15 min). Si falla
  una noche, `status` pasa a `STALE` a las 26 h y healthchecks.io avisa por email tras
  el periodo de 1 d más 2 h de gracia.
- **RTO objetivo: 4 h** hasta tener la API pública en un host nuevo. Es un objetivo,
  **no está medido**. El próximo simulacro de host nuevo tiene que cronometrar el §15.3
  completo.
- **Fuera del alcance del backup:** los derivados públicos de media, la
  configuración de Cloudflare (reglas A/B/C, Tunnel, DNS; ver `OPERATIONS.md`) y
  los secretos (`shared/.env`, que se recrean). Los originales sí se respaldan
  mediante el flujo separado de §16 cuando `MEDIA_ROOT` está configurado.

### 15.3 Runbook: recuperar en un host nuevo (easerver perdido)

Es una decisión humana. Cada bloque indica **dónde** se ejecuta. Todo lo que implique
sudo, secretos o Cloudflare lo hace el operador.

**Qué se necesita:**
- K1 **o** K2 (`.key.age`) con su passphrase;
- acceso a B2 (master key, o una clave de solo lectura `listFiles,readFiles`);
- acceso a GitHub (artifact de Release CI) y al panel de Cloudflare (Tunnel);
- un host Ubuntu con Python 3.14 y PostgreSQL 18.

1. **Máquina del operador: elegir y probar el backup.**
   ```bash
   ~/b2cli/bin/b2 account authorize > /dev/null
   ~/b2cli/bin/b2 ls -r b2://artesanfc-backups-prod/artesanfc/prod/postgres/daily/ | grep bundle.tar.age | sort | tail -3
   BID=<el más reciente>; P=<ruta daily/AAAA/MM/DD/$BID>
   mkdir -p ~/recover/$BID && for f in bundle.tar.age meta.json; do ~/b2cli/bin/b2 file download --no-progress "b2://artesanfc-backups-prod/$P/$f" ~/recover/$BID/$f; done
   python3 qa/d10-offhost-drill/drill.py ~/recover/$BID ~/artesa-keys/artesa-backup-K1.key.age   # DRILL PASS antes de seguir
   ```
2. **Máquina del operador: descifrar para la restauración real.** Hacerlo en un
   directorio 0700 y borrarlo al final:
   ```bash
   umask 077; mkdir -p ~/recover/plain && cd ~/recover/plain
   age -d -i ~/artesa-keys/artesa-backup-K1.key.age -o bundle.tar ~/recover/$BID/bundle.tar.age
   tar -xf bundle.tar
   python3 -c 'import json,hashlib;m=json.load(open("manifest.json"));print(m)' | head -40   # revisión alembic y commit
   cat recovery/RELEASE.json   # commit y release_id que corrían al hacer el backup
   ```
   Comprobar `sha256sum database.dump` frente al manifest.
3. **Host nuevo: base.**
   - usuario `energias`;
   - SSH solo por llave;
   - Tailscale;
   - `apt install postgresql-18 python3.14 python3.14-venv`;
   - layout de `DEPLOYMENT.md` §11.1 paso 2.
4. **Host nuevo: base de datos.** Los roles no están en el dump.
   - Crear el rol de la aplicación con `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE` y una
     contraseña **nueva**, más la base con ese propietario.
   - Copiar `database.dump` con `scp` a un archivo 0600 y ejecutar
     `pg_restore --no-owner --role=<rol> -d <db> database.dump`.
   - Borrar el dump del host y `~/recover/plain` de la máquina del operador.
5. **Host nuevo: release.**
   - Usar el artifact del commit de `recovery/RELEASE.json`: el de Release CI de ese
     commit de `main`, o reconstruirlo con `build_release.py --ref <commit>`, que es
     byte-idéntico.
   - Seguir `DEPLOYMENT.md` §11.1 pasos 3–7. `shared/.env` se escribe **nuevo** con las 4
     variables: `DATABASE_URL` con la contraseña nueva, y `CORS_ALLOWED_ORIGINS` con
     `https://artesanfc.com` más los orígenes de staging.
   - `bin/artesa-deploy run alembic current` debe coincidir con el manifest.
6. **Host nuevo: servicio.**
   - Unit de `ops/systemd/artesa-nfc.service.example` (§11.1 paso 8, sin unit legada).
   - `deploy <id> --expect-commit <sha>`. Es el primer despliegue del host: no hay
     rollback automático (exit 54).
7. **Cloudflare Tunnel.**
   - Instalar `cloudflared` y conectar el **Tunnel existente** con un conector nuevo
     (token del panel), con `api.artesanfc.com` apuntando a `http://localhost:8000`.
   - Retirar el conector del host perdido.
   - Las reglas A/B/C viven en el borde y no cambian.
8. **Verificación:** checklist externo de `OPERATIONS.md` §10 y
   `curl https://api.artesanfc.com/api/v1/artisans`.
9. **Backups en el host nuevo.**
   - D10.1: §13, con `backup.env` que lleva **los mismos recipients públicos** K1/K2.
   - D10.2: §14.4 pasos 4–7, con una **clave de servidor nueva**. **Borrar la clave del
     host perdido** con la master key. La URL de healthchecks no cambia.
10. **Cierre.**
    - Revocar todo lo del host perdido: llaves SSH, conector del Tunnel, clave B2.
    - Registrar la recuperación (hora de inicio y de fin, `backup_id`, identidad usada).

## 16. Originales de medios fuera del host (M3, docs/MEDIA.md §5)

`media/originales/` guarda el material de campo tal como llegó y **no se puede
volver a tomar**. Desde TOOL 1.7.0, `artesa-backup run` prepara su índice si
`shared/.env` tiene `MEDIA_ROOT` y lo copia fuera del host cuando también existe
`remote.env`. Lo hace el módulo `ops/backup_media.py`. El repositorio documenta
la implementación, pero no una activación ni un restore sample de M3.

### 16.1 Qué hace `run`

1. **Recorre `originales/`.** Solo toma archivos regulares; no sigue symlinks y
   omite los nombres que empiezan con `.`. Con eso arma el índice
   `ruta → sha256, tamaño`. El SHA-256 se guarda en caché por tamaño y mtime en
   `state/media-hashes.json`.
2. **Mete el índice dentro del bundle cifrado** como `media-index.json`, y el
   manifest lleva los totales. Fuera del host no se puede leer ningún nombre de
   archivo; el `meta.json` público no los menciona.
3. **Sube los contenidos nuevos (con `remote.env`).** Cada contenido que todavía
   no esté verificado se cifra con age para K1+K2 en un staging privado. Se sube
   **una sola vez** como `<prefijo>media/originales/<sha256>.age`, se verifica
   listándolo (tamaño y SHA-1) y el cifrado local se borra.
   - Es incremental: un original ya subido no se vuelve a enviar, y dos archivos
     idénticos son un solo objeto.
   - El registro `state/offsite/media-originals.json` tiene la misma forma que el
     de un backup, así que `remote-check` también vuelve a listar estos objetos.
4. **Un fallo en los originales no descarta el backup de la base.** El run
   termina con exit 31 (`MEDIA OFFSITE: FAILED`), no manda el ping al dead-man y
   el siguiente run reintenta. `status` lo muestra y deja de estar sano.

Sin `remote.env`, el índice va en el bundle, pero los originales **no** salen del
host (`MEDIA: … NOT copied off-host`). Sin `MEDIA_ROOT`, se muestra
`MEDIA: NOT CONFIGURED` y todo sigue igual que antes.

### 16.2 Por qué bajo el prefijo de postgres

La clave del servidor está restringida a `artesanfc/prod/postgres/` (§14.4, paso 4).
Poner `media/` debajo de ese prefijo evita crear una clave nueva. Además queda
**fuera** de las reglas de lifecycle `daily/`, `weekly/` y `monthly/`, así que
estos objetos **no expiran**. El Object Lock (`governance`, 35 d) los protege al
subirlos, y la clave sigue sin poder borrar.

**Comprobación humana, una vez:** que ninguna regla de lifecycle del bucket tenga
como prefijo `artesanfc/prod/postgres/` completo, ni `…/media/`:

```bash
b2 bucket get artesanfc-backups-prod   # lifecycleRules: solo …/daily/, …/weekly/, …/monthly/
```

### 16.3 Recuperar originales (máquina del operador, nunca easerver)

```bash
# 1. un backup reciente (bundle + meta) y los objetos de medios
b2 file download b2://artesanfc-backups-prod/artesanfc/prod/postgres/daily/AAAA/MM/DD/<backup_id>/bundle.tar.age ~/m3/backup/bundle.tar.age
b2 file download b2://artesanfc-backups-prod/artesanfc/prod/postgres/daily/AAAA/MM/DD/<backup_id>/meta.json ~/m3/backup/meta.json
b2 sync b2://artesanfc-backups-prod/artesanfc/prod/postgres/media/originales/ ~/m3/objects/
# 2. descifrar y verificar (pide la passphrase de K1 una vez)
python3 qa/d10-offhost-drill/media_restore.py ~/m3/backup ~/artesa-keys/artesa-backup-K1.key.age ~/m3/objects ~/m3/originales
```

El script lee `media-index.json` del bundle y descifra cada `<sha256>.age` en su
ruta. Después comprueba el SHA-256 contra el índice; un objeto que falte o no
coincida hace fallar el run (exit 1). Con `--sample N` recupera solo N archivos:
es el simulacro periódico. La identidad se descifra una sola vez en un
directorio temporal 0700, y el script lo borra al salir.

Para devolver los originales al servidor, copiar `~/m3/originales/` a
`MEDIA_ROOT/originales/` sin sobrescribir nada (`rsync --ignore-existing`).
`publico/` no forma parte de este respaldo. Volver a subir en Gestión crea
nuevas rutas/registros y no restaura por sí solo los `storage_path` existentes;
un recovery debe copiar `publico/` desde una fuente aparte o reconciliar la base
con los nuevos derivados.
