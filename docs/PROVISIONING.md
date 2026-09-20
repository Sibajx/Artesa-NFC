# ArtesaNFC — Provisioning de certificados y tags NFC (piloto)

**Estado:** vigente para el piloto. Cierra el hallazgo N-09 (issue #107): antes
no existía un flujo soportado y revisado para emitir el token de un
certificado y programar un tag NFC físico para una pieza real.
**Herramienta:** `python -m app.cli.provision` (`backend/app/cli/provision.py`),
en español. Los subcomandos y los códigos siguen en inglés: `list`, `status`,
`issue`, `rotate`, `revoke`, `lock`.
**Sin secretos:** este documento no contiene ni debe recibir tokens, URLs
`/c/{token}` reales, `token_hash`, credenciales ni UIDs de tags reales.

## 1. Alcance y prerrequisitos

Qué hace: para una pieza que **ya existe**, emite su certificado, muestra la URL
`https://artesanfc.com/c/{token}` una sola vez, guía la escritura del tag NTAG213,
registra el resultado y permite revocar o rotar.

Qué **no** hace (fuera de N-09, sin mecanismo nuevo en este cambio):

- **Crear artesanos o piezas.** Es un prerrequisito externo: el seed está
  prohibido en producción (`assert_safe_for_seed`) y no hay API administrativa.
  La pieza y su artesano deben existir y estar **publicados** antes de emitir;
  la CLI se niega en caso contrario (un certificado de una pieza no publicada se
  muestra como `unavailable`, así que su tag no se podría verificar).
- Escribir ni bloquear el tag: eso lo hace la herramienta NFC del operador. La
  CLI solo guía, pide confirmaciones y registra en la base de datos lo que el
  operador confirma.
- Probar propiedad ni anti-clonación. El certificado autentica la **pieza**, no
  a un dueño; el UID no es un secreto; el NTAG213 no tiene protección
  criptográfica contra clonado (`SECURITY.md` §6). Copiar la URL de un tag a
  otro produce un clon indistinguible.
- `AUDIT_EVENT`: **diferido para el piloto, brecha aceptada** frente a
  `SECURITY.md` §12.1. La procedencia mínima queda en campos existentes (§8).

## 2. Decisiones (issue #107)

| # | Decisión |
|---|---|
| 1 | Producción se opera por SSH en el host del backend/PostgreSQL, con el entorno real del servicio. |
| 2 | `AUDIT_EVENT` diferido; solo metadata no secreta en campos existentes y timestamps. Sin migraciones. |
| 3 | El bloqueo físico **no** es automático ni obligatorio: es un paso aparte, recomendado tras verificar escritura y varios escaneos, solo si la herramienta lo confirma. El tag puede quedar sin bloquear. |
| 4 | CLI en español. |
| 5 | Cambios aditivos: `issue_certificate`, `register_nfc_tag`, normalización de `physical_uid`, parámetro `reason` en `revoke_certificate`/`rotate_certificate`, `assert_safe_for_provisioning`. |
| 6 | Android / herramienta NFC que escriba NDEF URI, lea el UID, verifique lo escrito y bloquee solo si lo soporta de forma explícita. No se asume iOS. |
| 7 | Sin staging público: desarrollo y tests usan local/test; nunca se escribe un tag real con una URL local; ensayo con un NTAG213 de sacrificio antes de una pieza real; el primer flujo público usa `production` con datos identificados como piloto. |
| A–M | `public_code` como identificador; el token no entra por argv/stdin/entorno/archivos/flags; solo se muestra la URL completa; dry-run obligatorio y sin token; `issue` atómico (registrar tag + asignar + emitir); `program` solo tras confirmar escritura y lectura de vuelta; `lock` solo tras el bloqueo físico confirmado; token perdido → `rotate`; sin `replace_nfc_tag`; sin migraciones, sin cambios a la API pública, sin API admin. |

## 3. Modelo de datos y estados

Un tag recorre `available → programmed → locked` (`retired` / `replaced` son
terminales). La CLI respeta que **`programmed` significa "la URL definitiva se
escribió y se leyó de vuelta"**:

```text
issue   : [una transacción] registrar tag (available) + asignar a la pieza + emitir certificado
          ── commit ── autochequeo en proceso ── se muestra la URL (una vez) ──
          el operador escribe y lee de vuelta ── UID coincide ── program (available → programmed)
          ── el operador escanea con un teléfono ── fin
lock    : (aparte, después) el operador bloquea con su herramienta ── lock (programmed → locked)
```

`replace_nfc_tag` **no** se usa: marcaría el tag nuevo como `programmed` antes de
que exista nada escrito. `rotate` con tag nuevo hace `retire` del anterior +
registrar + asignar, y `program` llega después de la escritura confirmada.
`revoke` **siempre** retira los tags vigentes de la pieza (con el certificado
revocado la URL del tag ya no autentica nunca más; dejarlo `programmed` solo
impediría volver a emitir la pieza).

## 4. Preparación

### 4.1 Antes del primer uso real (una vez)

1. **Elegir la herramienta NFC** (Android, ver decisión 6) y comprobar que:
   escribe **un único** registro NDEF de tipo URI; muestra el UID del tag; lee de
   vuelta y muestra la URL escrita; y, si se va a usar, **bloquea** el tag de
   forma explícita y lo confirma. No se asume iOS.
2. **Ensayo con un NTAG213 de sacrificio** (nunca una pieza real y **nunca un
   token real**): escribir con la herramienta una URL obviamente falsa, por
   ejemplo `https://artesanfc.com/c/ENSAYO-NO-ES-UN-TOKEN`, leerla de vuelta,
   anotar el UID, escanearla con un teléfono (debe mostrar "no disponible") y,
   solo si se piensa bloquear en el piloto, bloquearlo y comprobar que la
   reescritura falla. Si el bloqueo no se puede verificar de forma segura con
   esa herramienta, el piloto deja los tags **sin bloquear** (decisión 3).
3. **Ensayo de la CLI** con `APP_ENV=local` o `test` contra una base de datos
   desechable (`backend/README.md`): la CLI marca ese modo con
   "ENSAYO LOCAL" y usa una URL `http://127.0.0.1:5500/c/…` que **no sirve para
   un tag real**. No escribir esa URL en ningún tag de una pieza real.

### 4.2 Acceso a producción

Solo por SSH en el host del backend/PostgreSQL, con el entorno real del servicio
y **sin escribir secretos en el shell**:

```bash
ssh <host-del-backend>
cd <directorio-del-backend>
set -a; source <archivo-de-entorno-del-servicio>; set +a   # solo la ruta queda en el historial
<venv>/bin/python -m app.cli.provision list
```

- **No** teclear `export DATABASE_URL=...` ni pasar credenciales por argumentos
  (quedarían en el historial y en la lista de procesos).
- No usar `script`, `tee`, redirecciones, grabadores de terminal ni logging de
  tmux/screen durante `issue` o `rotate`: la CLI se niega a mostrar la URL si
  stdin, stdout o stderr no son un terminal interactivo real.
- La primera línea del programa muestra `APP_ENV` y el nombre de la base de datos;
  en `production` hay que teclear `production` para continuar.

## 5. Procedimiento: primera emisión (`issue`)

Con la pieza ya publicada, junto a la pieza física y a un tag NTAG213 nuevo:

1. `… provision list` y `… provision status --piece <CODIGO>`: confirmar que es
   la pieza correcta y que el siguiente paso es `issue`.
2. **Simulacro obligatorio de la operación**:
   `… provision issue --piece <CODIGO> --dry-run`. Es de solo lectura
   (`SET TRANSACTION READ ONLY`), no genera token y no pide confirmaciones.
3. Con la herramienta NFC, **leer el UID** del tag (no escribir todavía).
4. `… provision issue --piece <CODIGO>` y responder, en este orden:
   - `production` (solo en producción);
   - el código de la pieza, otra vez;
   - el UID del tag (acepta `04A1…`, `04:A1:…`, `04-a1-…`; muestra cómo lo
     interpretó y pide confirmarlo);
   - `EMITIR <CODIGO>`.
5. La CLI confirma el commit, hace un **autochequeo en proceso** y pide Enter
   para mostrar la URL. **La URL aparece una sola vez**, en la pantalla alterna
   del terminal (desaparece del scrollback al salir). Con `[m]ostrar de nuevo`
   se vuelve a ver dentro del mismo proceso.
6. En la herramienta NFC: **escribir esa URL como un único registro NDEF URI,
   sin nada más**, y **leer el tag de vuelta** comprobando que la URL coincide.
   Al terminar, limpiar el portapapeles y su historial.
7. Responder `e` (ya escrita), confirmar la lectura de vuelta y teclear los
   **últimos 2 bytes del UID** que muestra la herramienta (p. ej. `E5:F6`):
   confirma que se escribió el tag registrado y no otro. Solo entonces la CLI
   registra el tag como `programmed`.
8. **Escaneo #1** con un teléfono: la página debe mostrar **auténtico** con el
   nombre de la pieza y del artesano que indica la CLI. Confirmar en la CLI.
9. Pegar el tag a la pieza, escanearlo **ya pegado** (**escaneo #2**) y repetir
   en más de un teléfono si es posible.
10. Borrar la URL del historial del navegador del teléfono y limpiar el
    portapapeles.
11. Guardar en la bitácora de operación el "registro de la operación" que
    imprime la CLI (no contiene secretos).

## 6. Bloqueo físico (opcional, aparte)

Solo tras verificar escritura y **varios escaneos**, y solo si la herramienta
confirmó explícitamente el bloqueo en el ensayo (§4.1). **Nunca antes de la
verificación y nunca sin confirmación explícita.** El bloqueo del NTAG213 es
**irreversible** y no protege contra el clonado, solo contra reescritura
accidental (`SECURITY.md` §6).

1. Bloquear el tag con la herramienta y comprobar que **confirma** el bloqueo.
2. **Escanear otra vez**: debe seguir mostrando auténtico (**escaneo #3**).
3. `… provision lock --piece <CODIGO> [--dry-run]`: contestar las tres
   confirmaciones, los últimos 2 bytes del UID y `BLOQUEAR <CODIGO>`. La CLI
   **no bloquea nada por sí misma**: solo registra un bloqueo ya hecho.

## 7. Revocar y rotar

**Revocar** (`revoke`): la URL del tag deja de autenticar de inmediato y no se
puede deshacer; los tags vigentes de la pieza se retiran. Motivos permitidos
(código, sin texto libre): `lost`, `damaged`, `compromised`, `wrong-tag`,
`replaced`, `not-deployed`, `other`. Confirmación: `REVOCAR <CODIGO>`. Después,
un escaneo del tag debe mostrar "no disponible". Con la pieza revocada se puede
volver a emitir con `issue` y un tag nuevo.

**Rotar** (`rotate`): revoca el certificado activo y emite uno nuevo con un token
independiente. Confirmación: `ROTAR <CODIGO>`.

- Tag desbloqueado (`available` o `programmed`): se puede **reescribir el mismo
  tag** (`m`) o usar uno **nuevo** (`n`).
- Tag bloqueado o inexistente: solo tag **nuevo** (se retira el anterior).
- Después sigue igual que `issue`: URL única, escritura, lectura de vuelta,
  UID, escaneo.

Casos: tag perdido o dañado → `rotate` con tag nuevo (`lost`/`damaged`); token
sospechoso o filtrado → `rotate` (`compromised`); pieza retirada → `revoke`.

## 8. Metadata no secreta que se conserva

Solo campos existentes, sin migración:

| Dónde | Qué |
|---|---|
| `certificate.issued_at`, `revoked_at` | Cuándo se emitió y se revocó. |
| `certificate.revocation_reason` | Código de motivo (lista fija, §7). Nunca público. |
| `nfc_tag.physical_uid` | UID normalizado: `04:A1:B2:C3:D4:E5:F6` (mayúsculas, bytes en hex separados por `:`, 7 bytes, empieza por `04`). No es un secreto. |
| `nfc_tag.chip_model`, `frequency`, `protocol` | `NTAG213`, `13.56 MHz`, `ISO 14443A`. |
| `nfc_tag.programmed_at`, `locked_at` | Cuándo se confirmó la escritura y el bloqueo. |
| `nfc_tag.notes` | Una línea por operación: fecha UTC, acción, `piece=`, ids de certificado y tag, UID, motivo, usuario del sistema operativo y versión de la CLI. **Nunca** token, URL ni hash (la CLI se niega a guardar texto con forma de secreto). |

## 9. Fallos y recuperación

El token **no se puede recuperar** (solo se guarda su hash): si se pierde después
del commit, **no se intenta recuperarlo** — se rota.

| Situación | Estado | Salida | Qué hacer |
|---|---|---|---|
| Falla antes del commit (entorno, validación, confirmación, conflicto, base de datos) | Nada cambia | 2 / 3 / 4 / 5 | Corregir y repetir. |
| El commit falla | Nada cambia; **la URL no se muestra** | 5 | Repetir. |
| El proceso o la terminal mueren después del commit, sin escribir el tag | Certificado activo con token perdido; tag `available` | 6 | `rotate` (mismo tag). |
| Falla la escritura NFC y el proceso sigue vivo | Nada cambia | — | `[m]ostrar de nuevo` y reintentar (misma URL). |
| Se aborta (`a`) tras el commit | Certificado sin desplegar | 4 ó 6 | La CLI ofrece revocarlo; si se declina queda parcial → `rotate`/`revoke`. |
| Los últimos 2 bytes del UID no coinciden | Tag **sin** registrar como programado | 6 | Probablemente es otro tag: usar el registrado o `rotate`. |
| El escaneo con teléfono no muestra la pieza correcta como auténtica | Tag `programmed`, certificado activo | 6 | No usar el tag: `rotate`. |
| **Se escribió la URL de otra pieza** en este tag | El token está en un objeto equivocado | — | `revoke`/`rotate` de la pieza dueña de esa URL, y reescribir. |
| Tag equivocado ya **bloqueado** | No se puede reescribir | — | `revoke` (retira el tag), descartar el tag y usar uno nuevo con `issue`. |

Exit `6` significa **estado parcial ya confirmado**; el mensaje incluye el comando
exacto para seguir.

**Break-glass** (solo si la CLI no estuviera disponible; revocar es siempre la
dirección segura y no requiere token). Con `psql` en el host, sin pegar nada
sensible:

```sql
UPDATE certificate
SET status = 'revoked', revoked_at = now(), revocation_reason = 'other'
WHERE id = '<uuid-del-certificado>' AND status = 'active';
```

## 10. Códigos de salida

| Código | Significado |
|---|---|
| 0 | Éxito (incluye `--dry-run`). |
| 1 | Error inesperado (solo se imprime el nombre de la clase). |
| 2 | Rechazado por la política de seguridad: entorno no permitido (`staging` y alias), base de datos no apta, o sin terminal interactivo real. |
| 3 | Precondición fallida o entrada inválida; no se cambió nada. |
| 4 | Cancelado por el operador; no queda nada pendiente. |
| 5 | Error de base de datos o concurrencia; ese paso no se confirmó. |
| 6 | Estado parcial ya confirmado que requiere seguimiento. |

## 11. Lo que la CLI garantiza y lo que no

Garantiza (cada punto con tests, `backend/tests/test_provisioning_*.py`):

- El token nunca se recibe (no hay opción, argumento, stdin ni archivo capaz de
  aceptarlo) y solo se muestra como parte de la URL completa, una vez, **después**
  del commit, en la pantalla alterna de un terminal interactivo.
- No escribe archivos, no usa `logging`, no imprime tracebacks (solo la clase de
  una excepción inesperada) y desactiva los core dumps.
- Los simulacros (`--dry-run`) y `list`/`status` son de solo lectura por
  PostgreSQL y no generan token.
- `staging` está rechazado; `local` solo con host local; `test` solo con base de
  datos de prueba; `production` exige teclear el nombre del entorno.

No garantiza — riesgos residuales del operador:

- La URL pasa por su pantalla, su portapapeles y su herramienta NFC: nadie
  mirando, sin grabaciones, sin pegarla en chats, notas, tickets ni capturas.
- El autochequeo en proceso prueba solo el lado de la base de datos; el
  **escaneo con teléfono** prueba Cloudflare Pages, la API y el contenido NDEF.
- Un tag clonado es indistinguible del original (NTAG213).

## 12. Qué nunca debe aparecer

Nunca, en logs, documentos, tests, issues, PR, chats, capturas ni bitácoras:

- un token real ni una URL `/c/{token}` real;
- `token_hash`;
- `DATABASE_URL`, credenciales o el host de la base de datos;
- capturas de la app NFC donde se vea la URL;
- UIDs de tags reales en el repositorio (los tests usan UIDs ficticios).

## 13. Checklist por pieza

- [ ] Pieza y artesano existen y están publicados (prerrequisito externo).
- [ ] Herramienta NFC verificada en el ensayo con el tag de sacrificio.
- [ ] `status` y `issue --dry-run` sin bloqueos.
- [ ] UID leído con la herramienta; `issue` completado; registro guardado.
- [ ] Escritura leída de vuelta; UID coincide; escaneo #1 auténtico y correcto.
- [ ] Tag pegado a la pieza; escaneo #2 (varios teléfonos).
- [ ] Portapapeles y historial del teléfono limpios.
- [ ] (Opcional) bloqueo confirmado por la herramienta, escaneo #3, `lock`.
