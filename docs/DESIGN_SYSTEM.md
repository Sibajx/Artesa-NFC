# ArtesaNFC — Design System v1

**Estado:** Draft para Issue #2  
**Dominio:** `artesanfc.com`  
**Propietario principal:** ChatGPT / UX-Frontend  
**Aprobación final:** Alexis

## 1. Objetivo

El sistema visual de ArtesaNFC debe presentar la artesanía como protagonista y utilizar la tecnología como una capa discreta de descubrimiento, identidad y autenticidad.

Debe sentirse como una combinación de:
- galería editorial;
- archivo cultural contemporáneo;
- experiencia digital inmersiva;
- plataforma tecnológica discreta;
- escaparate de piezas únicas.

## 2. Principios visuales

### 2.1 La pieza manda
La interfaz debe ceder protagonismo al color, textura, material, fotografía, proceso, artesano y detalles físicos.

### 2.2 Mostrar antes que explicar
Primero imagen, movimiento, textura, objeto y persona; después texto, datos y explicación técnica.

### 2.3 Minimalismo funcional
La reducción de elementos debe mejorar claridad, enfoque, ritmo y navegación.

### 2.4 Tecnología silenciosa
NFC, trazabilidad y autenticidad aparecen mediante interacción, transición, microanimación y cambio de atmósfera.

### 2.5 Identidad propia por pieza
Cada pieza puede usar acentos de color, fondos, composición, galería y medios interactivos sin romper la identidad general de ArtesaNFC.

## 3. Paleta base

```text
Warm White  #F5F3EE
Carbon      #111111
Ink         #171717
Soft White  #F7F7F4
Graphite    #66635E
```

Regla: el color debe provenir principalmente de la artesanía.

Variables por pieza:

```text
--piece-accent
--piece-accent-secondary
--piece-bg
--piece-text
```

## 4. Tipografía

### 4.1 Tipografía v1 aprobada

```text
Display: Instrument Serif
UI / Texto: Manrope
Estado: Aprobado para implementación inicial
```

Esta combinación se considera la base tipográfica de ArtesaNFC v1.

**Instrument Serif** se utilizará para:
- Hero.
- Nombres de piezas.
- Nombres de artesanos.
- Frases manifiesto.
- Titulares editoriales.

**Manrope** se utilizará para:
- Navegación.
- Botones.
- Descripciones.
- Metadatos.
- Formularios.
- Certificados.
- Footer.
- Información técnica.

Regla de consistencia:

> Las piezas pueden personalizar color, atmósfera, fotografía, composición y medios interactivos, pero no deben cambiar arbitrariamente las familias tipográficas.

La elección tipográfica no es irreversible. Cualquier cambio futuro deberá responder a una razón clara de marca, legibilidad o producto y documentarse en `DESIGN_SYSTEM.md` y `DECISIONS.md`.

Escala orientativa:

```text
Display XL   clamp(4rem, 9vw, 9rem)
Display L    clamp(3rem, 6vw, 6rem)
Heading 1    clamp(2.2rem, 4vw, 4rem)
Heading 2    clamp(1.7rem, 3vw, 2.8rem)
Body L       1.25rem
Body         1rem
Small        0.875rem
Micro        0.75rem
```

## 5. Layout

Grid recomendado:

```text
Desktop: 12 columnas
Tablet:   8 columnas
Mobile:   4 columnas
```

Márgenes:

```text
Desktop: 48–72 px
Tablet:   32–48 px
Mobile:   18–24 px
```

Ritmo vertical:

```text
Sección normal: 96–160 px
Sección inmersiva: 100vh o más
Microsección: 48–72 px
```

## 6. Navegación

Estructura:

```text
ARTESANFC                                ☰
```

Menú:

```text
EXPLORAR
ARTESANOS
TECNOLOGÍA
NOSOTROS
CONTACTO
```

Los certificados no aparecen.

Comportamiento:
- logo y menú visibles al entrar;
- logo se desvanece progresivamente al bajar;
- menú se oculta/funde al bajar;
- menú reaparece al subir;
- sobre fondo claro: negro;
- sobre fondo oscuro: blanco.

## 7. Hero

- Video a pantalla completa o casi completa.
- Manos, materiales, proceso, textura y piezas.
- Movimiento lento.
- Texto mínimo.
- Video sin audio por defecto.
- Poster, fallback y versión móvil.
- Respetar `prefers-reduced-motion`.

## 8. Botones y enlaces

Preferir enlaces editoriales:

```text
Explorar pieza →
Conocer al artesano →
Descubrir →
```

Evitar sombras, gradientes decorativos y exceso de CTAs.

## 9. Tarjetas

### Piezas
Prioridad:
1. imagen;
2. nombre;
3. origen/artesano;
4. acción discreta.

### Artesanos
Prioridad:
1. retrato/escena de trabajo;
2. nombre;
3. localidad;
4. técnica/descripción breve;
5. acceso al perfil.

Animación: fade, reveal, desplazamiento vertical suave o zoom muy leve.

## 10. Página de artesano

Ruta:

```text
/artesanos/{slug}
```

Flujo:

```text
Retrato / escena de trabajo
↓
Nombre + comunidad
↓
Historia
↓
Proceso / técnica
↓
Galería
↓
Piezas creadas
↓
Contacto/redes opcionales
```

Cada pieza enlaza a `/piezas/{slug}`.

## 11. Página de pieza

Ruta:

```text
/piezas/{slug}
```

Estructura:

```text
Hero de pieza
↓
Nombre / origen
↓
Galería principal
↓
Interacción especial
↓
Historia
↓
Materiales / técnica
↓
Artesano
↓
Otras piezas relacionadas
```

Siempre mostrar:

```text
Creada por [Nombre del artesano] →
```

Permitido personalizar:
- acento;
- fondo;
- galería;
- 3D/360;
- video;
- macrofotografía;
- textura.

No permitido:
- romper navegación;
- cambiar tipografía arbitrariamente;
- ocultar información esencial;
- romper accesibilidad.

## 12. 3D / 360

Ideal para máscaras, escultura, barro y objetos con volumen.

Desktop:
```text
drag → rotate
scroll/controls → zoom
```

Mobile:
```text
swipe → rotate
pinch → zoom
```

Reglas:
- carga diferida;
- placeholder estático;
- fallback de imagen;
- límite de peso;
- controles simples;
- no bloquear scroll;
- sin autorrotación permanente.

## 13. Certificados

Estructura estable:

```text
CERTIFICADO DE AUTENTICIDAD

Pieza
Código público
Artesano
Origen
Técnica
Materiales
Fecha / año
Registro
Estado
Datos de verificación
```

Puede adaptarse visualmente a la pieza, pero nunca mostrar secretos internos ni usar token/UID como “prueba visible”.

## 14. Animación

Duraciones:

```text
micro:       120–220 ms
UI:          220–400 ms
editorial:   500–900 ms
escena:      900–1600 ms
```

Permitido:
- fade;
- reveal;
- parallax sutil;
- scale leve;
- scrub narrativo.

Evitar:
- scroll secuestrado;
- movimiento excesivo;
- mareo;
- animaciones sin propósito.

## 15. Sección tecnología

Transición visual recomendada:

```text
fondo claro
↓
fondo oscuro
```

Contenido conceptual:

```text
IDENTIDAD DIGITAL
NFC
AUTENTICIDAD
TRAZABILIDAD
REGISTRO
```

## 16. Home — flujo base

```text
01 HERO / VIDEO
02 MANIFIESTO ARTESANFC
03 EXPERIENCIA NFC
04 PIEZAS DESTACADAS
05 ARTESANOS
06 CÓMO FUNCIONA
07 TECNOLOGÍA
08 HISTORIA / NOSOTROS
09 CONTACTO
10 FOOTER
```

## 17. Responsive

Mobile-first real.

Prioridades:
- lectura clara;
- imágenes protagonistas;
- video optimizado;
- 3D lazy;
- menú accesible;
- targets táctiles de mínimo 44×44 px.

## 18. Accesibilidad

Obligatorio:
- contraste suficiente;
- teclado;
- `focus-visible`;
- alt text;
- labels;
- HTML semántico;
- `prefers-reduced-motion`;
- no depender solo del color;
- zoom del navegador funcional.

## 19. Performance

- imágenes responsivas;
- AVIF/WebP cuando convenga;
- lazy loading;
- poster de video;
- modelos 3D diferidos;
- evitar frameworks pesados sin justificación;
- JavaScript modular;
- animaciones con transform/opacity cuando sea posible.

## 20. Convenciones CSS futuras

```text
frontend/assets/css/
├── tokens.css
├── base.css
├── layout.css
├── components.css
├── animations.css
└── pages.css
```

Tokens iniciales:

```css
:root {
  --bg: #F5F3EE;
  --surface-dark: #111111;
  --text: #171717;
  --text-inverse: #F7F7F4;
  --text-muted: #66635E;

  --space-xs: 0.5rem;
  --space-sm: 1rem;
  --space-md: 2rem;
  --space-lg: 4rem;
  --space-xl: 8rem;

  --piece-accent: currentColor;
  --piece-accent-secondary: currentColor;
}
```

## 21. Referencias de dirección

### A Bath House
Tomar:
- ritmo;
- narrativa;
- escala;
- fotografía;
- atmósfera.

### Vorrath Woodworks
Tomar:
- presentación de obra;
- claridad;
- relación oficio ↔ producto;
- galería;
- confianza.

### ArtesaNFC
Construir identidad propia con:

```text
CULTURA
+
OFICIO
+
PIEZA
+
PERSONA
+
TECNOLOGÍA
+
AUTENTICIDAD
```

## 22. Decisiones pendientes

- Logo final y variantes.
- Diseño definitivo del menú abierto.
- Composición exacta del hero.
- Tratamiento del buscador.
- Footer final.
- Reloj/opening si se conserva.
- Nivel máximo de personalización de certificados.
- Librería de animaciones.
- Tecnología exacta del visor 3D.

## 23. Definition of Done — Issue #2

La Issue #2 se considera terminada cuando:
- existe `docs/DESIGN_SYSTEM.md`;
- define color, tipografía, layout y espaciado;
- define navegación y hero;
- define comportamiento del logo y menú;
- define páginas de artesanos y piezas;
- define reglas de personalización;
- define animaciones;
- contempla 3D/360;
- contempla responsive y accesibilidad;
- separa reglas globales de identidad por pieza;
- Alexis aprueba la versión inicial.

