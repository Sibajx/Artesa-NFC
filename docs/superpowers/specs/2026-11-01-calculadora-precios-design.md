# Calculadora de precio al público (Gestión) — diseño

Fecha: 2026-11-01. Aprobado por el PO en conversación («sí, me late así, hazlo»).
Proyecto 1 de 3 (calculadora → tienda → página nueva).

## Por qué
Artesa vende piezas de artesanos: el artesano recibe su precio completo, sin regateo, y Artesa
suma su comisión, el cobro con tarjeta y los impuestos. Hoy eso se calcula a mano. Se quiere una
cifra aproximada en Gestión, redondeada a la centena de arriba, sin dar precios exagerados.

## Decisiones del PO
- La comisión es del 15 % al 25 % según la pieza y el monto.
- El envío se cobra aparte (lo cotizará la tienda; fuera de aquí).
- El precio final se redondea a la centena superior.
- Régimen fiscal en evaluación (RESICO persona moral o física): las tasas deben ser editables.
- Modelo recomendado y aceptado: cobro con tarjeta e IVA **encima** del precio; ISR **estimado y
  restado de la comisión**; el redondeo queda a favor de Artesa; interruptor
  «intermediario / revendedor» para cuando el contador decida.
- Venta directa del artesano: cuota de ~$500 encima de su precio (solo como referencia).

## Cálculo (todo en centavos; sin flotantes)
A = lo que recibe el artesano; c = % de comisión (de la pieza o, si no hay, por tramos de monto).
C = A·c. R = A·(ret_isr + ret_iva) si hay retenciones de plataforma (se suman encima para que el
artesano reciba A completo). t = % del cobro con tarjeta, f = cuota fija por cobro.
- Intermediario (el IVA solo va sobre la comisión; el IVA del cobro con tarjeta es costo):
  P = (A + R + C·(1+iva) + f·(1+iva)) / (1 − t·(1+iva))
- Revendedor (IVA sobre todo el precio; el IVA del cobro con tarjeta es acreditable):
  P = (A + R + C + f) / (1/(1+iva) − t)
Precio público = P redondeado hacia arriba al múltiplo configurado (100 pesos por omisión).
Lo que el redondeo agrega (sin IVA) se suma a la comisión bruta G. ISR estimado = G·isr.
«Te quedan» = G − ISR (antes de deducciones). Se muestra el desglose completo.

## Alcance
- Backend: tabla `pricing_settings` (una fila, configuración JSON validada), dos columnas nuevas
  en `piece` (`artisan_price_cents`, `commission_pct`), servicio puro de cálculo, rutas
  `GET/PUT /pricing/settings` (leer: Ventas; editar: dueño) y `POST /pricing/quote` (Ventas).
  Todo cambio de ajustes queda en auditoría. Nada de esto sale en la API pública.
- Gestión: apartado «Precio al público» en la ficha de la pieza (precio del artesano, comisión,
  desglose, «Usar como precio de lista») y pantalla «Ajustes de precios» (solo dueño).

## No es parte de esto
Carrito, cobro, cotización de envíos, página pública. Asesoría fiscal: las tasas por omisión
son aproximadas y se avisa en pantalla que deben confirmarse con un contador.
