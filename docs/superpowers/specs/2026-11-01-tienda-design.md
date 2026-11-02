# Tienda en línea de ArtesaNFC — diseño

Fecha: 2026-11-01. Proyecto 2 de 3 (calculadora → **tienda** → página nueva).
Estado: borrador para aprobación del PO.

## 1. Intención
Que la gente compre las piezas certificadas en artesanfc.com: ver precio, apartar, pagar, cotizar el
envío, pedir factura y recibir la pieza, sin regateo, con el artesano cobrando su precio completo.
Cada pieza es única: una venta la quita de la tienda.

## 2. Metas, no-metas, anti-metas
**Metas:** precios visibles de las piezas marcadas «A la venta»; carrito; pago con Mercado Pago;
envío cotizado por destino (México e internacional) y cobrado aparte; factura a quien la pida;
pedidos en Gestión; la venta se registra sola y la pieza pasa a «Vendida».

**No-metas (v1):** cuentas de comprador (se compra como invitado), cupones, varias monedas (todo
en MXN), devoluciones automáticas, timbrado automático de CFDI, reseñas (proyecto 3).

**Anti-metas:** vender dos veces la misma pieza; que los datos de tarjeta pasen por nuestro
servidor; precios calculados en el navegador; datos personales del comprador en la auditoría o en
los registros.

## 3. Decisiones del PO (2026-11-01)
- Compra en el sitio, con carrito, cobro y envíos: tienda propia sobre el sistema actual («la A»).
- Procesador: **Mercado Pago**. Aún no hay cuenta; el PO la abrirá. Se desarrolla con credenciales de prueba.
- Envío aparte, cotizado por destino (país o estado) con un cotizador de paqueterías.
- Origen del envío **por pieza**: taller de Artesa o con el artesano. A veces el artesano lleva el
  paquete y a veces Artesa pasa por él.
- Factura (CFDI) **a quien la pida**.
- Precio al público = el de la calculadora (proyecto 1), redondeado a la centena.

## 4. Enfoques considerados
- **A. Tienda propia** sobre el backend actual, con Mercado Pago Checkout Pro y la API de Envia.com. **Elegida.**
  Piezas, apartado, venta y «vendida» viven en un solo lugar; usa la calculadora; diseño libre; sin
  mensualidad. Costo: más trabajo propio.
- B. Plataforma (Shopify o Tiendanube) sincronizada desde Gestión. Carrito y apps listos, pero con
  mensualidad, riesgo de vender dos veces al sincronizar piezas únicas, diseño limitado y datos repartidos.
- C. Ligas de pago por pieza más WhatsApp. Rápida, pero sin apartado, cotización ni pedidos.

## 5. Cómo funciona

### 5.1 Datos nuevos
- `piece`: `for_sale` (bool, falso por omisión); `ship_weight_g`, `ship_length_cm`, `ship_width_cm` y
  `ship_height_cm` (ya empacada); `ship_origin` (`artesa` | `artesano`).
- `artisan`: dirección de envío (privada, solo en Gestión; nunca en la API pública).
- Ajustes de tienda: dirección del taller de Artesa, minutos de apartado (20 por omisión) y
  contacto para los correos.
- `shop_order`: folio público corto, token secreto para consultar el estado, estado, comprador
  (nombre, correo, teléfono), dirección de entrega, datos fiscales (opcionales), totales en
  centavos (piezas, envío, total), id de preferencia y de pago de Mercado Pago, y fechas.
- `shop_order_item`: pieza, precio congelado al crear el pedido, opción de envío elegida
  (paquetería, servicio, precio, días, id de cotización), `hold_expires_at`, origen, guía y estado
  del envío.
- Índice único parcial: una pieza solo puede estar en **un** renglón con apartado vigente o pagado.

### 5.2 Estados del pedido
`pendiente_pago` → `pagado` → `enviado` (todas sus guías) → `entregado`.
Desvíos: `expirado` (venció el apartado sin pago), `cancelado` y `reembolsado`. Cada cambio queda
en la auditoría, sin datos personales.

### 5.3 Flujo de compra
1. **Tienda:** la API pública lista solo piezas publicadas, `for_sale`, con precio de lista y sin
   apartado vigente ni venta. Muestra el precio, que hoy nunca es público.
2. **Carrito** (en el navegador, sin cuenta): solo ids de piezas.
3. **Cotizar envío:** el comprador da país, código postal y dirección. El servidor cotiza **por
   pieza** desde su origen (cada pieza va en su propio paquete) y regresa 2 o 3 opciones por pieza.
4. **Crear pedido:** el servidor vuelve a leer precios y disponibilidad (nunca confía en el
   navegador) y aparta las piezas por N minutos (renglones con `hold_expires_at`; el índice único
   impide dos apartados). Guarda la cotización elegida y crea la **preferencia de Checkout Pro**,
   con `external_reference` = pedido, `notification_url`, `back_urls` y vencimiento igual al apartado.
5. **Pago:** el comprador paga en la página de Mercado Pago.
6. **Confirmación:** llega el webhook de Mercado Pago. El servidor valida la firma (`x-signature`)
   y **consulta el pago a la API** (no confía en el cuerpo). Si está aprobado y el monto coincide:
   - el pedido pasa a `pagado`;
   - se llama `sales.register(channel="en_linea")` por cada pieza, que queda «Vendida»;
   - se manda correo al comprador y aviso a Artesa.

   Es idempotente: el mismo aviso dos veces no duplica nada.
7. **Pago tardío:** si el apartado ya venció pero la pieza sigue libre, se acepta. Si otra persona
   ya la compró, se **reembolsa** automáticamente por la API y se avisa al comprador y a Artesa.
8. **Regreso:** las `back_urls` llevan a una página con el estado del pedido, consultado con el token.
9. Un barrido periódico (o al consultar) marca `expirado` los pedidos vencidos y libera las piezas.

### 5.4 Envíos (Envia.com)
- La cotización se hace del lado del servidor, con peso y medidas empacadas, origen (taller o
  artesano) y destino.
- Una pieza sin peso o sin medidas no se puede vender en línea; Gestión lo avisa al marcarla «A la venta».
- Después del pago, Gestión muestra por pieza «Generar guía» (manual en v1, porque a veces hay que
  empacar o recoger antes) y «quién la lleva»: el artesano, pasamos por ella o recolección.
- Si la cotización vence antes de generar la guía y el precio cambió, se avisa en Gestión. La
  diferencia la absorbe Artesa; al comprador no se le cobra de nuevo.

### 5.5 Factura
- Casilla «Necesito factura» con RFC, razón social, régimen fiscal, código postal fiscal y uso del
  CFDI (se valida el formato).
- Se guarda en el pedido. Gestión muestra «Factura pendiente» y permite marcarla emitida con su folio.
- El timbrado automático (Facturapi o similar) llega cuando el contador defina el régimen.
- Con el modelo intermediario, Artesa factura su comisión; la parte del artesano la define el contador.

### 5.6 Gestión
- Ficha de la pieza: «A la venta», peso y medidas empacada, origen del envío.
- Ficha del artesano: dirección de envío.
- Página **Pedidos** (permiso Ventas):
  - lista con filtros por estado;
  - detalle con comprador, dirección, piezas, envíos, guías, factura, pagos e historia del pedido;
  - acciones: generar guía, marcar enviado o entregado, marcar factura emitida, y cancelar y
    reembolsar (solo el dueño).

### 5.7 Correos
Con el `mailer` SMTP que ya existe: confirmación de compra, pieza enviada (con guía) y reembolso.
Aviso interno a Artesa por cada pedido pagado.

## 6. Restricciones
- **Secretos nuevos** (`MERCADOPAGO_ACCESS_TOKEN`, `MERCADOPAGO_WEBHOOK_SECRET`, `ENVIA_API_KEY`):
  se agregan a la configuración validada (`app/core/config.py`) **y** a `ALLOWED_ENV_KEYS` de
  `ops/release_probe.py`; nunca en el repo ni en los registros. Sin ellos, la tienda se apaga sola
  y las piezas se ven sin botón de compra.
- **CORS:** solo artesanfc.com (y el sitio nuevo cuando exista). La validación de arranque en
  producción rechaza otros orígenes (incidente del 2026-11-01): cualquier origen nuevo es un cambio de código.
- **Límite de peticiones** también para los POST de la tienda; hoy solo cubre los GET.
- Migraciones aditivas y registradas en `ops/migration-classes.json`.
- Todo el dinero en centavos enteros, en MXN.
- **Webhook** público sin Cloudflare Access (como el resto de `/api/v1`), verificado por firma y por consulta.
- **Textos legales** (términos, aviso de privacidad, devoluciones, envíos): los da el PO. Sin ellos
  no se abre la tienda al público.

## 7. Lo que queda al constructor
- Nombres exactos de tablas y rutas.
- Forma del barrido de expirados (tarea programada o perezoso).
- Diseño visual de Pedidos, siguiendo Gestión.
- La vitrina mínima de compra en artesanfc.com, que el proyecto 3 rediseñará.
