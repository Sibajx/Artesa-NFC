# Artesa NFC

Plataforma de certificación digital con chips NFC para artesanías, prendas y
piezas de arte cultural del estado de Oaxaca. Cada pieza se registra con un
ID único; al escanear su chip NFC, el comprador es dirigido a un certificado
de autenticidad generado dinámicamente con los datos reales de esa pieza.

**Dominio:** artesanfc.com (Cloudflare)

## Arquitectura

| Componente | Tecnología | Función |
|---|---|---|
| Sitio | Cloudflare Pages | Hospeda el sitio estático (`/public`), se despliega automáticamente en cada `push` a `main` |
| Base de datos | Cloudflare D1 (SQLite) | Almacena artesanas y piezas registradas |
| API / certificados | Cloudflare Pages Functions | Registra piezas nuevas y genera cada certificado dinámicamente en `/cert/[id]` |
| Chips | NFC (NTAG213/215/216) | Cada chip guarda la URL única de su certificado |

## Estructura del repositorio

```
artesanfc/
├── public/                  Sitio estático servido por Cloudflare Pages
│   └── index.html
├── db/
│   └── schema.sql            Esquema de la base de datos D1
├── docs/
│   └── diccionario-datos.md  Documentación de cada campo y convención de IDs
├── wrangler.toml              Configuración de Cloudflare Pages/D1
└── .gitignore
```

## Estado del proyecto

- [x] Fase 1 — Diseño del esquema de datos (`db/schema.sql`, `docs/diccionario-datos.md`)
- [x] Fase 2 — Estructura del repositorio lista para conectar a Cloudflare Pages
- [ ] Fase 3 — Crear y vincular la base de datos D1
- [ ] Fase 4 — Endpoint de registro de piezas
- [ ] Fase 5 — Generador dinámico de certificados (`/cert/[id]`)
- [ ] Fase 6 — Adaptación de la plantilla visual del certificado
- [ ] Fase 7 — Programación y prueba de chips NFC
- [ ] Fase 8 — Prueba de extremo a extremo y lanzamiento

## Cómo desplegar (Fase 2)

1. Crea un repositorio nuevo en GitHub y sube el contenido de esta carpeta.
2. En el panel de Cloudflare: **Workers & Pages → Crear proyecto → Conectar a Git**.
3. Selecciona el repositorio. En la configuración de build:
   - **Framework preset:** None
   - **Build command:** (vacío — es un sitio estático, no requiere build)
   - **Build output directory:** `public`
4. Despliega. Cloudflare te dará una URL temporal (`*.pages.dev`) para verificar que el sitio cargó bien.
5. En **Custom domains**, agrega `artesanfc.com` (y, cuando llegue la Fase 5, el subdominio `cert.artesanfc.com`).
6. A partir de aquí, cada `git push` a `main` vuelve a desplegar el sitio automáticamente.
