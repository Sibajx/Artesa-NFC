# ArtesaNFC — Operaciones de producción (borde de la API)

**Estado:** vigente para el piloto. Cierra los hallazgos N-03 y F-14 en lo que
el repositorio puede cerrar; lo que depende de Cloudflare o del servidor queda
descrito aquí como **pendiente de aplicar** y **pendiente de verificar**.
**Alcance:** el camino de una petición a `api.artesanfc.com` y las superficies
operativas de la API (`/docs`, `/health`, tamaño de cuerpo, rate limiting).
**Sin secretos:** este documento no contiene credenciales, tokens de API,
identificadores de cuenta ni credenciales del Tunnel, y no debe recibirlos.

> **Nginx: NOT CURRENTLY DEPLOYED.** Nginx no está en el request path de
> producción. `backend/nginx/artesanfc-api.conf.example` es una configuración
> alternativa / de referencia; **no** es el enforcement vigente y no debe
> asumirse que protege nada en producción.

## 1. Topología real

```text
Navegador / teléfono
  │ HTTPS
  ▼
Cloudflare (edge: TLS, reglas, rate limiting)
  │
  ▼
Cloudflare Tunnel (cloudflared, en el servidor)
  │  http://localhost:8000
  ▼
Uvicorn / FastAPI   127.0.0.1:8000   (servicio systemd: artesa-nfc.service)
  │
  ▼
PostgreSQL
```

- El frontend (`artesanfc.com`) vive en Cloudflare Pages y **no** pasa por este
  camino; solo llama a `https://api.artesanfc.com/api/v1/*`.
- El servidor no expone puertos públicos: Uvicorn escucha solo en loopback y el
  Tunnel es la única entrada.
- TLS lo termina Cloudflare; entre cloudflared y Uvicorn es HTTP en loopback.

## 2. Qué controla cada capa

| Control | Capa | En el repo | Estado |
|---|---|---|---|
| Sin `/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect` en `staging`/`production` | FastAPI | Código + tests | **Aplicado** (al desplegar esta versión) |
| `/health` = 200 con DB, 503 sin DB, `no-store`, `HEAD` | FastAPI | Código + tests | **Aplicado** (al desplegar esta versión) |
| Límite de cuerpo 1024 bytes en `POST /api/v1/certificates/resolve` | FastAPI | Código + tests | **Aplicado** (al desplegar esta versión) |
| `Cache-Control: no-store` y CORS también en el `413` | FastAPI | Código + tests | **Aplicado** (al desplegar esta versión) |
| `--proxy-headers --forwarded-allow-ips 127.0.0.1` | Uvicorn (unit systemd) | Solo documentado (§3) | **Pendiente de verificar en el servidor** |
| Allowlist `/api/v1/*` en el host de la API (regla A) | Cloudflare | Solo documentado (§8) | **Pendiente de aplicar** |
| Bloqueo de `POST` a resolve con query string (regla B) | Cloudflare | Solo documentado (§8) | **Pendiente de aplicar** |
| Rate limit de `POST` a resolve por IP (regla C) | Cloudflare | Solo documentado (§8) | **Pendiente de aplicar; umbral pendiente de validar contra el plan** |
| Rate limit en memoria dentro de FastAPI | — | No existe | **Decidido: no se implementa** |
| Nginx | — | Ejemplo, no desplegado | **No aplica** |

Las reglas de Cloudflare, el ingress del Tunnel y la unit de systemd son
configuración operativa **no versionada**. El repo solo las documenta con
exactitud (aquí) y aporta una verificación externa (§10).

## 3. systemd: flags esperados de Uvicorn

La unit real vive en el servidor y no se gestiona desde este repo. Lo que
producción **debe** cumplir:

| Requisito | Valor |
|---|---|
| Comando | `python -m uvicorn app.main:app` |
| Dirección | `--host 127.0.0.1 --port 8000` (nunca `0.0.0.0` fuera de un contenedor) |
| Cabeceras del proxy | `--proxy-headers` (ya es el valor por defecto; se fija explícito) |
| Proxies de confianza | `--forwarded-allow-ips 127.0.0.1` |
| **Prohibido** | `--forwarded-allow-ips '*'` y `FORWARDED_ALLOW_IPS=*` |
| Entorno | `APP_ENV=production`, `DEBUG=false`, `CORS_ALLOWED_ORIGINS=https://artesanfc.com`, `DATABASE_URL` real (ver `backend/README.md`, "Environment safety") |

Ejemplo **documental** (no es la unit real ni se aplica desde el repo; las
rutas entre `<>` las define quien opera el servidor):

```ini
[Service]
User=<usuario-sin-privilegios>
WorkingDirectory=<directorio-del-backend>
EnvironmentFile=<archivo-de-entorno-con-los-valores-anteriores>
ExecStart=<venv>/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=on-failure
```

Verificación (solo lectura): `systemctl cat artesa-nfc.service`,
`systemctl show artesa-nfc.service -p Environment -p EnvironmentFiles` (revisar
que `FORWARDED_ALLOW_IPS` no sea `*`) y `ss -ltnp | grep 8000` (debe mostrar
`127.0.0.1:8000`, no `0.0.0.0` ni `[::]`).

## 4. Confianza en cabeceras reenviadas

- La aplicación **no lee** `X-Forwarded-For` ni `CF-Connecting-IP`; no confía en
  ninguna cabecera de cliente para nada. Solo Uvicorn las interpreta, y solo
  cuando el peer TCP es un proxy de confianza.
- Con `--forwarded-allow-ips 127.0.0.1`, Uvicorn confía únicamente en
  conexiones desde loopback (cloudflared). De `X-Forwarded-For` toma la primera
  dirección **no confiable leyendo de derecha a izquierda**: según el
  comportamiento documentado de Cloudflare, este añade la IP real al final, así
  que una dirección falsa escrita por el cliente a la izquierda se ignora (se
  confirma en producción con la comprobación 2 de §10). `X-Forwarded-Proto`
  fija el esquema (evita redirecciones `307` a `http://`).
- Consecuencia: la IP del access log de Uvicorn es la del visitante real, útil
  como evidencia de abuso (`SECURITY.md` §5.4). Está fijada por un test contra
  un Uvicorn real (`tests/test_edge_uvicorn.py`).
- Con `'*'` cualquiera podría falsificar IP y esquema: por eso está prohibido.
- El Managed Transform de Cloudflare **"Remove visitor IP headers"** debe estar
  **desactivado**; si no, `X-Forwarded-For` desaparece y todo el tráfico se ve
  como `127.0.0.1`.
- Límite conocido: cualquier proceso local del servidor puede conectarse a
  `127.0.0.1:8000` sin pasar por Cloudflare y, al ser loopback de confianza,
  falsificar IP y esquema. Los controles de la app (docs, límite de cuerpo) no
  dependen de eso; el rate limiting y la allowlist del borde sí se saltan desde
  local, lo cual es aceptable en un servidor de propósito único.

## 5. Política de docs / OpenAPI

- `APP_ENV=production` y `APP_ENV=staging`: `/docs`, `/redoc`, `/openapi.json` y
  `/docs/oauth2-redirect` **no existen** (rutas no registradas; 404 con el
  envelope estándar). No dependen de ninguna regla externa.
- `APP_ENV=local` y `APP_ENV=test`: siguen disponibles. Para usar Swagger en
  desarrollo, `APP_ENV=local`.
- El contrato público vive en `docs/API_CONTRACT.md`, no en el schema generado.

## 6. Semántica de `/health`

Un único endpoint, `GET` y `HEAD /health` (no forma parte del contrato público):

| Situación | Estado | Cuerpo | Cabeceras |
|---|---|---|---|
| Base de datos alcanzable | `200` | `{"status":"ok","database":"connected"}` | `Cache-Control: no-store` |
| Base de datos no alcanzable | `503` | `{"status":"unavailable","database":"unavailable"}` | `Cache-Control: no-store` |

- `HEAD` devuelve el mismo estado y cabeceras, sin cuerpo.
- Los cuerpos son cadenas fijas: nunca incluyen host, usuario, driver ni el
  mensaje del error.
- **No conectarlo a un reinicio automático** (watchdog, `HEALTHCHECK`
  con reinicio): una caída momentánea de la base de datos no debe reiniciar el
  proceso. Es una señal de disponibilidad, no de vida.
- Cada llamada abre una conexión nueva a PostgreSQL (timeout de 2 s). Por eso
  no debe quedar accesible desde Internet (regla A) ni usarse como sondeo
  frecuente.
- Uso previsto: verificación local tras un despliegue
  (`curl -si http://127.0.0.1:8000/health`). Un monitor externo debe sondear
  `GET /api/v1/artisans` (pasa por la base de datos y sí está en la allowlist).
- **Fuera de alcance de este cambio:** las rutas de datos (`/api/v1/*`) ante una
  base de datos caída siguen respondiendo `500` con el envelope genérico; un
  `503` general es trabajo posterior (N-02).

## 7. Política de tamaño de cuerpo

- `POST /api/v1/certificates/resolve` (con o sin `/` final) acepta como máximo
  **1024 bytes** de cuerpo (el legítimo mide unos 60). Más de eso: `413`,
  `{"error":{"code":"payload_too_large","message":"The request body is too large."}}`,
  `Cache-Control: no-store` y CORS del origen permitido.
- Se aplica en la app porque Uvicorn no limita cuerpos y FastAPI lee todo el
  cuerpo en memoria antes de validar; el límite de cuerpo del borde de
  Cloudflare (del orden de 100 MB o más, según el plan) es demasiado alto para
  proteger al proceso.
- Cubre `Content-Length` mayor de 1024, cuerpo sin `Content-Length` y cuerpo por
  chunks; no confía solo en `Content-Length` y deja de leer al superar el límite.
- No afecta a `OPTIONS`, `GET` ni a otros endpoints.
- Detalle técnico: `SECURITY.md` §5.6.

## 8. Reglas de Cloudflare pendientes de aplicar

**No aplicadas.** Se aplican de una en una, en este orden, verificando (§10)
después de cada una. Si el plan ofrece la acción *Log* (o equivalente), usarla
primero para revisar falsos positivos antes de pasar a *Block*. Las expresiones
usan el lenguaje de reglas de Cloudflare; confirmar la sintaxis exacta en el
dashboard al crearlas.

Antes de empezar:

- Confirmar que el Managed Transform "Remove visitor IP headers" está apagado (§4).
- Confirmar que el servicio del Tunnel apunta al origen esperado
  (`http://localhost:8000`) y que no hay reglas previas que interfieran.

### Regla A — solo el namespace público en el host de la API

Objetivo: desde Internet, `api.artesanfc.com` solo debe atender `/api/v1/*`.
Quedan bloqueados `/health`, `/c/*`, `/` y cualquier otra ruta que no sea del
namespace público (incluida cualquier ruta de docs, aunque la app ya no las
sirva).

- Tipo: regla personalizada (WAF custom rule).
- Expresión: `(http.host eq "api.artesanfc.com" and not starts_with(http.request.uri.path, "/api/v1/"))`
- Acción: Block.
- Efecto colateral esperado: `/health` deja de ser alcanzable desde fuera (se usa
  en local, §6). `/c/*` en el host de la API deja de llegar a Uvicorn, así que
  un token pegado por error en ese host no queda en el access log del origen.
- Opcional (misma regla o aparte): bloquear métodos que la API pública no usa,
  `(http.host eq "api.artesanfc.com" and not http.request.method in {"GET" "HEAD" "POST" "OPTIONS"})`.

### Regla B — `POST` a resolve con query string

Objetivo: el token viaja en el cuerpo; una petición a resolve con query string
es un cliente equivocado (o un intento de dejar un token en un log). Se bloquea
antes de que llegue al origen, donde Uvicorn registraría la query.

- Tipo: regla personalizada.
- Expresión: `(http.host eq "api.artesanfc.com" and http.request.method eq "POST" and starts_with(http.request.uri.path, "/api/v1/certificates/resolve") and http.request.uri.query ne "")`
- Acción: Block.

### Regla C — rate limit de `POST /api/v1/certificates/resolve` por IP

- Tipo: regla de rate limiting.
- Expresión de coincidencia: `(http.host eq "api.artesanfc.com" and http.request.method eq "POST" and starts_with(http.request.uri.path, "/api/v1/certificates/resolve"))`
- Contador: por IP de origen del visitante.
- **Umbral deseado** (`SECURITY.md` §5.1): 30 solicitudes por minuto por IP en
  régimen sostenido, con una ráfaga pequeña (unas 5) tolerada para recargas y
  escaneos NFC repetidos.
- **Estado del umbral: PENDIENTE DE VALIDACIÓN de las capacidades del plan de
  Cloudflare. No es un hecho aplicable todavía.** Antes de fijar cualquier
  número hay que comprobar en el dashboard: cuántas reglas de rate limiting
  admite el plan; qué periodos de conteo ofrece; qué características de
  agrupación permite (IP); qué acción y respuesta permite (bloqueo, código de
  estado, cuerpo) y cuánto dura el bloqueo; y que no existe un concepto de
  "burst" separado (se aproxima con umbral y periodo). Como candidato ilustrativo,
  si solo hubiera periodo de 10 s, 5 solicitudes por 10 s equivaldrían a unas 30
  por minuto sostenidas; es un candidato, no una decisión.
- Opcional y posterior: un límite más laxo para los `GET` públicos
  (`SECURITY.md` §5.2, 120 por minuto por IP), sujeto a las mismas capacidades.
- Las respuestas generadas por Cloudflare (403 de bloqueo, 429 de rate limit)
  no llevan cabeceras CORS: el navegador las ve como fallo de red y el frontend
  las trata como estado temporal de servicio (aceptado, igual que en
  `SECURITY.md` §5.5).

## 9. Política de rate limiting

- La capa **primaria** es Cloudflare (regla C): ve la IP real del visitante,
  actúa antes del Tunnel y del origen y no requiere código.
- **No** se implementa un limitador en memoria en FastAPI en esta fase.
- El rate limiting es control de costo y de abuso, no la defensa contra
  fuerza bruta: el token tiene 256 bits de entropía (`SECURITY.md` §2.1).
- Si el plan de Cloudflare no permitiera una regla utilizable, se reabre la
  decisión (por ejemplo un limitador por proceso en la app, correcto solo con un
  único worker) y se actualiza `SECURITY.md` §5.5; no se improvisa en producción.

## 10. Checklist de verificación externa

Solo lecturas y peticiones pequeñas, siempre con tokens **obviamente falsos**
(nunca un token real). Definir `API=https://api.artesanfc.com`. Ejecutar fuera de
horarios de uso: una ráfaga puede bloquear temporalmente tu propia IP.

Resultados esperados según la fase:

| Petición | Solo app desplegada | Con reglas A/B/C |
|---|---|---|
| `curl -sS -o /dev/null -w '%{http_code}' $API/docs` (y `/redoc`, `/openapi.json`, `/docs/oauth2-redirect`) | 404 (de la app) | 403 (bloqueo de Cloudflare) |
| `curl -sS -i $API/health` | 200 o 503 (de la app; accesible) | 403 |
| `curl -sS -i "$API/c/NOT_A_TOKEN?x=1"` | 404 (de la app) | 403 |
| `curl -sS -i $API/api/v1/artisans` | 200 con datos | 200 con datos |
| `curl -sS -i -X POST "$API/api/v1/certificates/resolve?token=NOT_A_TOKEN" -H 'Content-Type: application/json' -d '{"token":"x"}'` | 200 `unavailable` (**y Uvicorn registra la query**) | 403 |

Comprobaciones adicionales:

1. **Límite de cuerpo** (esperado `413` con `Cache-Control: no-store` y, con
   `Origin`, `Access-Control-Allow-Origin: https://artesanfc.com`):
   ```bash
   head -c 2048 /dev/zero | tr '\0' 'a' > /tmp/body2k
   curl -sS -i -X POST "$API/api/v1/certificates/resolve" \
     -H 'Content-Type: application/json' -H 'Origin: https://artesanfc.com' \
     --data-binary @/tmp/body2k
   curl -sS -i -X POST "$API/api/v1/certificates/resolve" \
     -H 'Content-Type: application/json' -H 'Origin: https://artesanfc.com' \
     -H 'Transfer-Encoding: chunked' --data-binary @/tmp/body2k
   ```
   Un cuerpo pequeño válido (`{"token":"NOT_A_TOKEN"}`) debe seguir dando `200`.
   No usar cuerpos de megabytes.
2. **IP real** (esperado: tu IP pública en el log, no `203.0.113.7` ni
   `127.0.0.1`): enviar `curl -sS -H 'X-Forwarded-For: 203.0.113.7' $API/api/v1/artisans`
   y, en el servidor, `journalctl -u artesa-nfc -n 5 --no-pager`.
3. **Sin token en logs** tras las pruebas de `/c/NOT_A_TOKEN` y de `?token=`:
   en el servidor, `journalctl -u artesa-nfc --since "-15 min" | grep -c NOT_A_TOKEN`
   debe dar `0` una vez aplicadas las reglas A y B.
4. **Rate limit** (solo tras aplicar la regla C y con el umbral ya validado):
   una ráfaga corta de peticiones inválidas a resolve desde una IP propia debe
   terminar en el estado que la regla defina (429 o 403 de Cloudflare); esperar
   a que expire el bloqueo antes de seguir.
5. **CORS intacto**: una petición `OPTIONS` de preflight con
   `Origin: https://artesanfc.com` a resolve responde 200 con
   `Access-Control-Allow-Origin: https://artesanfc.com`.
6. **En el servidor**: `ss -ltnp | grep 8000` solo en `127.0.0.1`; `systemctl
   cat artesa-nfc.service` cumple §3.
7. **`/health` local**: `curl -si http://127.0.0.1:8000/health` → 200 con la base
   de datos arriba. No detener la base de datos de producción para probar el
   503: eso lo cubren los tests (`tests/test_health.py`,
   `tests/test_edge_uvicorn.py`).

## 11. Rollback

| Qué | Cómo |
|---|---|
| Cambio de la app (docs, `/health`, límite de cuerpo) | Volver al commit anterior (`git revert` o el release previo, anotado antes de desplegar) y reiniciar `artesa-nfc.service`. No hay migraciones. |
| Una regla de Cloudflare | Desactivarla o eliminarla desde el dashboard (efecto inmediato); se aplicaron de una en una, así que la causa se aísla. |
| Cambio de flags de Uvicorn | Restaurar la unit anterior, `systemctl daemon-reload` y reiniciar el servicio. |
| Nginx | No aplica: no está desplegado. |

Notas: un monitor que espere `200` de `/health` con la base de datos caída (el
comportamiento anterior) verá ahora `503`; desplegar la app antes de configurar
monitores. Si un bloqueo del borde afecta tráfico legítimo, desactivar primero
la regla A (la más amplia).

## 12. Fuera del alcance de este cambio

No se corrigen aquí (siguen abiertos): la validación de CORS de producción que
acepta orígenes extra (N-01); el `500` no controlado sin `no-store`/CORS y el
`503` general en rutas de datos (N-02); los constraints de base de datos
(F-12); CSP y HSTS; el volumen de logs y su retención (`SECURITY.md` §12.3).
