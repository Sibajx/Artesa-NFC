# Tienda en línea de ArtesaNFC — diseño

Fecha: 2026-11-01. Proyecto 2 de 3 (calculadora → **tienda** → página nueva).
Estado: aprobado por el PO con la propuesta del 2026-11-01 (ver §3.1); quedan decisiones del PO en §8.
Propuesta: `documentos/ArtesaNFC_Propuesta_Tienda_en_Linea_2026-11-01.docx` (fuera del repo).

## 1. Intención
Que la gente compre las piezas certificadas en artesanfc.com: ver precio, apartar, pagar, cotizar el
envío, pedir factura y recibir la pieza, sin regateo, con el artesano cobrando su precio completo.
Cada pieza es única: una venta la quita de la tienda.

## 2. Metas, no-metas, anti-metas
**Metas:** precios visibles de las piezas marcadas «A la venta»; carrito; pago por SPEI directo o con
tarjeta (Mercado Pago); envío cotizado por destino (México e internacional) y cobrado en el mismo
pago, o recoger gratis; factura a quien la pida;
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

### 3.1 Ajustes de la propuesta (2026-11-01, aceptados por el PO)
Meta: costo fijo mensual $0; solo se paga comisión cuando el comprador escoge tarjeta.
- **Cobro:** SPEI directo a la CLABE de Artesa como opción principal ($0) y **Mercado Pago solo para
  tarjeta** (y meses sin intereses). Sin OXXO ni efectivo por ahora.
- **Precio por transferencia:** puede llevar descuento sobre el de tarjeta (recomendado: descuento
  medio, p. ej. $7,600 vs $7,800). La cifra la decide el PO (§8); el sistema la toma de un ajuste.
- **Apartado:** 20 minutos con tarjeta; 24 horas con SPEI.
- **Confirmación del SPEI:** manual en v1. El dueño ve el depósito en su banco y pulsa «Pago recibido»
  en Gestión. Sin API de SPEI con conciliación (se evalúa si crece el volumen).
- **Factura:** se emite a mano en el servicio gratuito del SAT; la tienda solo guarda los datos
  fiscales y Gestión marca «Factura pendiente». Timbrado automático solo si crece el volumen.
- **Modelo fiscal supuesto:** intermediario (Artesa factura su comisión). Lo confirma el contador (§8).
- **Envío:** se cotiza y se cobra en el mismo pago (como Mercado Libre) con un cotizador de paqueterías
  sin mensualidad y saldo prepagado: **Envia.com o Skydropx** (el PO elige al abrir la cuenta). Se
  ofrece además **recoger gratis** en el taller o en ferias.
- **Correos:** Brevo, plan gratuito (ya en uso por el `mailer`).

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
- Ajustes de tienda: dirección del taller de Artesa, minutos de apartado con tarjeta (20 por
  omisión), horas de apartado con SPEI (24 por omisión), descuento por transferencia (en centavos
  o porcentaje; 0 = un solo precio), CLABE, banco y titular para el SPEI, lugares para recoger y
  contacto para los correos.
- `shop_order`: folio público corto, token secreto para consultar el estado, estado, **método de
  pago** (`spei` | `tarjeta`), comprador (nombre, correo, teléfono), dirección de entrega (vacía si
  recoge), datos fiscales (opcionales), totales en centavos (piezas, envío, total), id de
  preferencia y de pago de Mercado Pago (solo tarjeta), quién confirmó el SPEI y cuándo, y fechas.
- `shop_order_item`: pieza, precio congelado al crear el pedido, opción de envío elegida
  (paquetería, servicio, precio, días, id de cotización), `hold_expires_at`, origen, guía y estado
  del envío.
- Índice único parcial: una pieza solo puede estar en **un** renglón con apartado vigente o pagado.

### 5.2 Estados del pedido
`pendiente_pago` → `pagado` → `enviado` (todas sus guías) → `entregado`.
Si el comprador escogió recoger: `pagado` → `entregado` cuando se le da la pieza.
Desvíos: `expirado` (venció el apartado sin pago), `cancelado` y `reembolsado`. Cada cambio queda
en la auditoría, sin datos personales.

### 5.3 Flujo de compra
1. **Tienda:** la API pública lista solo piezas publicadas, `for_sale`, con precio de lista y sin
   apartado vigente ni venta. Muestra el precio, que hoy nunca es público.
2. **Carrito** (en el navegador, sin cuenta): solo ids de piezas.
3. **Cotizar envío:** el comprador da país, código postal y dirección. El servidor cotiza **por
   pieza** desde su origen (cada pieza va en su propio paquete) y regresa 2 o 3 opciones por pieza.
   Siempre aparece también «Recoger gratis» (taller o feria), con costo $0 y sin dirección.
4. **Escoger cómo pagar:** SPEI (precio con descuento, apartado 24 h) o tarjeta con Mercado Pago
   (precio de lista, apartado 20 min). El total se muestra completo, con envío, antes de pagar.
5. **Crear pedido:** el servidor vuelve a leer precios y disponibilidad (nunca confía en el
   navegador), aplica el precio del método elegido y aparta las piezas (renglones con
   `hold_expires_at` según el método; el índice único impide dos apartados). Guarda la cotización elegida.
   - **Tarjeta:** crea la **preferencia de Checkout Pro**, con `external_reference` = pedido,
     `notification_url`, `back_urls`, vencimiento igual al apartado y solo medios de tarjeta
     (excluye efectivo/OXXO y transferencias).
   - **SPEI:** muestra (y manda por correo) la CLABE, banco, titular, monto exacto y el **folio como
     concepto** del depósito, con la hora límite del apartado.
6. **Pago:** con tarjeta, en la página de Mercado Pago; con SPEI, desde la app de su banco.
7. **Confirmación SPEI (manual):** en Gestión → Pedidos el dueño pulsa «Pago recibido» tras ver el
   depósito (monto y folio). Se exige confirmar el monto; queda en la auditoría quién y cuándo. A
   partir de ahí sigue igual que la confirmación con tarjeta (venta registrada, correos).
8. **Confirmación con tarjeta:** llega el webhook de Mercado Pago. El servidor valida la firma (`x-signature`)
   y **consulta el pago a la API** (no confía en el cuerpo). Si está aprobado y el monto coincide:
   - el pedido pasa a `pagado`;
   - se llama `sales.register(channel="en_linea")` por cada pieza, que queda «Vendida»;
   - se manda correo al comprador y aviso a Artesa.

   Es idempotente: el mismo aviso dos veces no duplica nada.
9. **Pago tardío:** si el apartado ya venció pero la pieza sigue libre, se acepta. Si otra persona
   ya la compró, se **reembolsa** (con tarjeta, automáticamente por la API; con SPEI, Gestión
   muestra «Reembolso pendiente» y el dueño lo transfiere a mano) y se avisa al comprador y a Artesa.
10. **Regreso:** las `back_urls` (o el enlace del correo, con SPEI) llevan a una página con el estado
    del pedido, consultado con el token.
11. Un barrido periódico (o al consultar) marca `expirado` los pedidos vencidos y libera las piezas.

### 5.4 Envíos (Envia.com o Skydropx)
- La cotización se hace del lado del servidor, con peso y medidas empacadas, origen (taller o
  artesano) y destino, detrás de una interfaz `cotizador` con un adaptador por proveedor, para
  usar Envia.com o Skydropx según la cuenta que abra el PO (§8). Saldo prepagado, sin mensualidad.
- «Recoger gratis» no llama al cotizador ni genera guía.
- Una pieza sin peso o sin medidas no se puede vender en línea; Gestión lo avisa al marcarla «A la venta».
- Después del pago, Gestión muestra por pieza «Generar guía» (manual en v1, porque a veces hay que
  empacar o recoger antes) y «quién la lleva»: el artesano, pasamos por ella o recolección.
- Si la cotización vence antes de generar la guía y el precio cambió, se avisa en Gestión. La
  diferencia la absorbe Artesa; al comprador no se le cobra de nuevo.

### 5.5 Factura
- Casilla «Necesito factura» con RFC, razón social, régimen fiscal, código postal fiscal y uso del
  CFDI (se valida el formato).
- Se guarda en el pedido. Gestión muestra «Factura pendiente» y permite marcarla emitida con su folio.
- v1: el dueño emite la factura en el servicio gratuito del SAT y anota el folio en Gestión.
- El timbrado automático (Facturapi o similar) solo si crece el volumen y el contador define el régimen.
- Con el modelo intermediario, Artesa factura su comisión; la parte del artesano la define el contador.

### 5.6 Gestión
- Ficha de la pieza: «A la venta», peso y medidas empacada, origen del envío.
- Ficha del artesano: dirección de envío.
- Página **Pedidos** (permiso Ventas):
  - lista con filtros por estado y por método de pago, con «SPEI por confirmar» arriba;
  - detalle con comprador, dirección, piezas, envíos, guías, factura, pagos e historia del pedido;
  - acciones: «Pago recibido» (SPEI, solo el dueño), generar guía, marcar enviado o entregado
    (o entregado en mano si recoge), marcar factura emitida, y cancelar y reembolsar (solo el dueño).
- Ajustes de tienda (solo el dueño): CLABE y titular, descuento por transferencia, tiempos de
  apartado, dirección del taller y lugares para recoger.

### 5.7 Correos
Con el `mailer` SMTP que ya existe (Brevo gratuito): datos para el SPEI con hora límite,
confirmación de compra, pieza enviada (con guía) o lista para recoger, y reembolso.
Aviso interno a Artesa por cada pedido pagado.

## 6. Restricciones
- **CLABE:** no es secreta pero sí sensible; vive en los ajustes de tienda (no en el repo) y solo
  se muestra al comprador en un pedido SPEI ya creado, nunca en el catálogo.
- **Secretos nuevos** (`MERCADOPAGO_ACCESS_TOKEN`, `MERCADOPAGO_WEBHOOK_SECRET` y la clave del
  cotizador, `ENVIA_API_KEY` o `SKYDROPX_API_KEY`):
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

## 8. Decisiones pendientes del PO
Bloquean la apertura al público, no el desarrollo (se trabaja con credenciales de prueba):
- [ ] Precio por transferencia: un solo precio, descuento medio (recomendado) o precio justo.
- [ ] Abrir la cuenta de Mercado Pago y pasar las claves de prueba.
- [ ] Abrir la cuenta de Envia.com o Skydropx.
- [ ] Dar la CLABE donde se recibirán las transferencias.
- [ ] Pasar las preguntas fiscales al contador (régimen, intermediario, plataforma digital,
      artesanos sin RFC, cuota de venta directa).
- [ ] Redactar los textos legales: términos, aviso de privacidad, devoluciones y envíos.
- [ ] Registrar en Gestión peso y medidas empacada de las piezas a la venta.
