# ArtesaNFC — Project Charter

**Estado:** Aprobado para Sprint 0  
**Dominio oficial:** `artesanfc.com`  
**Fecha de referencia:** 2026-09-16

## 1. Propósito

ArtesaNFC es una plataforma digital para presentar, documentar y autenticar piezas artesanales únicas, conectando la pieza física con una experiencia digital mediante tecnología NFC.

El proyecto no busca funcionar como un catálogo de producción masiva. Cada pieza se trata como una obra individual, vinculada a su artesano, su origen, su proceso, su historia y su identidad digital.

## 2. Propuesta de valor

ArtesaNFC combina tres capas:

1. **Descubrimiento público**
   - Presentación de ArtesaNFC como empresa.
   - Perfiles de artesanos.
   - Galerías y páginas públicas de piezas.
   - Contenido editorial, fotografía, video y visualización 3D/360 cuando aporte valor.

2. **Identidad de la pieza**
   - Cada pieza pública se vincula con el artesano que la creó.
   - Cada perfil de artesano muestra sus piezas.
   - La dirección visual de cada pieza puede adaptarse a su carácter, colores y materiales sin perder la identidad general de ArtesaNFC.

3. **Autenticidad privada**
   - El certificado no forma parte de la navegación pública.
   - El acceso principal se obtiene desde el NFC físico asociado a la pieza.
   - El certificado utiliza un token no secuencial e impredecible validado por el backend.

## 3. Principios del producto

- La artesanía es protagonista.
- ArtesaNFC no es un ecommerce masivo.
- Mostrar antes que explicar.
- Tecnología discreta.
- Seguridad por diseño.

## 4. Alcance inicial

### Incluido en el MVP

- Landing principal de ArtesaNFC.
- Directorio/perfiles públicos de artesanos.
- Páginas públicas de piezas.
- Relación bidireccional artesano ↔ pieza.
- API backend con FastAPI.
- Base de datos relacional.
- Certificado privado asociado a la pieza.
- Acceso al certificado mediante NFC.
- Flujo administrativo mínimo para registrar información.
- Fotografía, video y soporte selectivo para 3D/360.
- Diseño responsive.
- Logs y controles básicos de seguridad.
- Preparación para futuras transferencias de propiedad, sin activarlas en el MVP.

### Fuera del MVP

- Marketplace masivo.
- Carrito de compras.
- Inventario a gran escala.
- Sistema completo de propiedad legal.
- Transferencia de propiedad entre usuarios.
- Blockchain como requisito.
- Algoritmos criptográficos caseros para proteger tokens.
- Subdominio independiente por artesano.

## 5. Actores

### Visitante público

Puede conocer ArtesaNFC, descubrir artesanos y piezas, navegar entre ambos e interactuar con recursos 3D/360.

### Poseedor físico de la pieza

Puede escanear el NFC y consultar el certificado asociado.

### Administrador ArtesaNFC

Puede registrar artesanos, piezas, NFC, certificados, actualizar contenido y revocar certificados.

## 6. Relaciones principales

```text
ARTESANO 1 ───── N PIEZAS
PIEZA    1 ─── 0..1 CERTIFICADO
PIEZA    1 ─── 0..1 NFC
```

Para el MVP, cada pieza pertenece a un solo artesano.

## 7. Rutas públicas objetivo

```text
/
/artesanos
/artesanos/{slug}
/piezas
/piezas/{slug}
/nosotros
/contacto
```

## 8. Certificados

Ruta conceptual:

```text
/c/{token}
```

El certificado:

- usa un token aleatorio criptográficamente seguro;
- no es secuencial;
- no se deriva directamente del UID NFC;
- no se publica en sitemap;
- usa `noindex`;
- no se enlaza desde páginas públicas;
- se valida en backend;
- está sujeto a rate limiting;
- puede revocarse;
- puede tener una presentación visual adaptada a la pieza.

## 9. Identidad visual inicial

- Home predominantemente clara.
- Secciones oscuras usadas de forma intencional.
- Hero con video.
- Menú de tres líneas arriba a la derecha.
- Menú que se desvanece al bajar y reaparece al subir.
- Logo con presencia fuerte al inicio y desvanecimiento con el scroll.
- Botones mínimos.
- Estructura común + paleta propia por pieza.
- Animaciones editoriales suaves con momentos interactivos más experimentales.

## 10. Caso piloto

Primer caso recomendado:

**Artesano de Cuilápam de Guerrero + una máscara artesanal.**

Debe validar:

1. perfil público del artesano;
2. página pública de la máscara;
3. navegación bidireccional artesano ↔ pieza;
4. visualización 3D o 360;
5. NFC físico;
6. certificado privado;
7. recorrido completo de extremo a extremo.

## 11. Inventario actual

- Artesanos reales: 2.
- Piezas reales: 4.
- Información de artesanos: disponible en gran parte.
- Máscaras de Cuilápam: piezas físicas disponibles y contenido parcial documentado.
- Entrevista adicional pendiente para completar información.
- Tags NFC actuales: NTAG213, 13.56 MHz, ISO 14443A.
- Ya se han escrito URLs en tags de prueba.

## 12. Roles de trabajo

### Alexis — Product Owner

Visión, prioridades, decisiones finales, coordinación con artesanos, contenido y aceptación de entregables.

### ChatGPT — Diseño de producto / arquitectura frontend

UX/UI, arquitectura de información, storytelling, sistema de diseño, frontend, interacciones, responsive, accesibilidad y experiencia 3D/360 del lado cliente.

### Claude — Backend / infraestructura

FastAPI, modelos, schemas, base de datos, Alembic, autenticación administrativa, seguridad de certificados, Docker, Nginx, logs, despliegue y pruebas backend.

### Compartido

Contrato de API, integración, seguridad end-to-end, CI/CD y pruebas end-to-end.

## 13. Forma de trabajo

- `main`: producción estable.
- `develop`: integración.
- `feature/*`: funciones.
- `fix/*`: correcciones.
- `docs/*`: documentación.
- Cada cambio relevante debe corresponder a una Issue.
- Cambios compartidos se documentan antes de modificar contratos.
- Las decisiones importantes se registran en `docs/DECISIONS.md`.

## 14. Criterios de éxito del MVP

El MVP se considera funcional cuando:

- la Home explica y presenta ArtesaNFC;
- un visitante puede llegar a un artesano y sus piezas;
- desde una pieza puede volver al artesano;
- una pieza seleccionada puede visualizarse de forma interactiva;
- el NFC físico abre un certificado no enumerable;
- el backend valida correctamente el certificado;
- el certificado no aparece en navegación ni indexación pública;
- el sitio funciona correctamente en móvil y escritorio;
- agregar una nueva pieza reutiliza el sistema existente.
