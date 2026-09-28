# ArtesaNFC — Guía de captura de contenido (piloto: máscara de Cuilápam)

Para quien vaya con el artesano. Objetivo: fotos, video y capturas 3D **reales**, con **autorización firmada**, que sirvan para la web sin rehacerlas.

> **Antes de cualquier foto:** leer y firmar la autorización (`docs/plantillas/AUTORIZACION_PUBLICACION.md`). Sin firma, el material se guarda pero **no se publica**.

## 1. Qué llevar

- Celular con buena cámara (o cámara) con **batería y espacio**; limpiar el lente.
- Tela o cartulina **lisa y neutra** (gris claro, beige o negro mate) de ~1 × 1.5 m.
- Si se puede: tripié de celular, una base giratoria (o un plato que gire) para la pieza.
- Cinta métrica (para las **dimensiones** de la pieza).
- Una libreta o nota de voz para los **datos de la pieza** (§6).

## 2. Configuración del teléfono

- **Desactivar la ubicación en la cámara** (evita guardar el GPS de la casa o el taller).
- Resolución máxima, sin filtros, sin "modo retrato" ni belleza, sin zoom digital.
- HDR automático está bien. Nada de flash directo.
- **Nunca enviar el material por WhatsApp** (lo comprime y lo arruina). Pasarlo por cable, Google Drive/Fotos en calidad original o AirDrop.

## 3. Fotos de la pieza

Luz: **natural, suave y lateral** (junto a una ventana o a la sombra en exterior; nunca sol directo). La pieza sobre la tela neutra.

| Rol | Cuántas | Cómo |
|---|---|---|
| `hero` (principal) | 2–3 | Frente, a la altura de la pieza, llenando ~70 % del cuadro. Vertical (4:5) y horizontal |
| `gallery` | 4–6 | Tres cuartos izquierda/derecha, perfil, reverso, desde arriba |
| `detail` | 4–6 | Muy de cerca: textura de la madera, pintura, vetas, marcas de herramienta, amarres |
| `process` | lo que haya | El artesano trabajando: **manos, herramientas, material**. Rostros solo con autorización |
| escala | 1 | La pieza con la cinta métrica o en manos (para dar tamaño) |

Enfocar tocando la pieza en la pantalla. Revisar que no salgan borrosas (hacer zoom al revisar).

## 4. Retrato y taller del artesano

- 2–3 retratos (con autorización): luz suave, de frente y trabajando.
- 3–5 del taller y del entorno **sin** elementos que identifiquen la dirección (números de casa, letreros de calle, placas).

## 5. Video para la portada (hero)

- **8–12 s por toma**, varias tomas; **sin hablar** (el sitio va sin audio).
- Tomas lentas y estables (apoyado o tripié): manos tallando o pintando, virutas, pinceles, la pieza girando despacio, la pieza terminada al final.
- Grabar **horizontal y también vertical** (el sitio usa ambas).
- 4K o 1080p, 24–30 fps. Sin estabilización "extrema", sin cambios bruscos de luz.
- Preferir manos y procesos a rostros (y rostros solo con autorización).

## 6. Datos de la pieza (para la ficha)

Anotar, **tal como lo diga el artesano** (no inventar ni completar):

- Nombre de la pieza y su significado o uso.
- Materiales (madera, pigmentos, fibras).
- Técnica; tiempo aproximado de elaboración.
- Año (o fecha) de elaboración.
- Dimensiones: alto × ancho × fondo (cm).
- Historia corta de la pieza; historia/tradición del oficio.
- Nombre con el que quiere aparecer el artesano (nombre completo y/o artístico), comunidad y municipio **escritos como él o ella lo prefiera**.
- Lenguas que habla y si autoriza mostrarlas.

## 7. Captura para 3D (fotogrametría)

Sirve para el modelo 3D de la máscara. Con celular y una app como **Polycam** o **RealityScan** (o fotos para procesar después):

1. Pieza sobre la base giratoria, fondo liso, luz pareja **sin sombras duras**; no usar flash.
2. **80–150 fotos**: tres vueltas completas (alta ~30°, a la altura y baja ~-15°), una foto cada ~10°, **traslape de ~70 %** entre fotos consecutivas.
3. Luego darle la vuelta a la pieza (para el reverso/interior) y repetir una vuelta.
4. Todas nítidas, misma exposición; no mover la luz a mitad de la captura.
5. Superficies muy brillantes u oscuras salen peor: evitar reflejos.
6. Guardar las fotos **originales** además del modelo que genere la app.

Objetivo final (lo prepara el equipo): GLB de 2–8 MB, texturas de 1024–2048 px.

## 8. Entrega

- Todo en una carpeta por pieza con el material original (sin comprimir).
- La autorización firmada (foto o escaneo) en la misma entrega.
- El equipo lo guarda en `media/originales/{artesano}/{pieza}/` (`docs/MEDIA.md`) y procesa lo publicable.
