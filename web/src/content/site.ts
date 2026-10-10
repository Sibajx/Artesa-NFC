// ArtesaNFC — editorial copy and the media manifest of the static pages.
//
// This is NOT a data store for artisans, pieces or certificates (ADR-019):
// those come only from the public API. It holds the home copy and the hero /
// collection media, each flagged `provisional` until a real, authorised asset
// replaces it. A provisional asset is always labelled on screen.

export interface ImageSource {
  readonly avif?: string;
  readonly webp?: string;
  readonly jpg: string;
  readonly width: number;
  readonly height: number;
}

export interface ResponsiveImage {
  readonly desktop: ImageSource;
  readonly mobile?: ImageSource;
  readonly alt: string;
  readonly provisional: boolean;
}

export interface VideoSource {
  readonly src: string;
  readonly type: string;
}

export interface HeroMedia {
  readonly poster: ResponsiveImage;
  /** Landscape framing. Empty = poster only (the message never depends on video). */
  readonly desktopVideo: readonly VideoSource[];
  /** Portrait framing for narrow screens; falls back to desktopVideo if empty. */
  readonly mobileVideo: readonly VideoSource[];
  readonly provisional: boolean;
}

const P = "/media/placeholders";

// PROVISIONAL (default of the migration brief): abstract texture video,
// ~9 s, no audio track, seamless loop. Replace with the documentary clip.
export const heroMedia: HeroMedia = {
  poster: {
    desktop: {
      avif: `${P}/hero-poster-desktop.avif`,
      webp: `${P}/hero-poster-desktop.webp`,
      jpg: `${P}/hero-poster-desktop.jpg`,
      width: 1920,
      height: 1080,
    },
    mobile: {
      avif: `${P}/hero-poster-mobile.avif`,
      webp: `${P}/hero-poster-mobile.webp`,
      jpg: `${P}/hero-poster-mobile.jpg`,
      width: 900,
      height: 1600,
    },
    alt: "Imagen provisional: textura abstracta en tonos de barro y madera.",
    provisional: true,
  },
  desktopVideo: [{ src: `${P}/hero-desktop.webm`, type: "video/webm" }],
  mobileVideo: [{ src: `${P}/hero-mobile.webm`, type: "video/webm" }],
  provisional: true,
};

export const collectionEntryImage: ResponsiveImage = {
  desktop: {
    avif: `${P}/collection-entry.avif`,
    webp: `${P}/collection-entry.webp`,
    jpg: `${P}/collection-entry.jpg`,
    width: 1200,
    height: 1500,
  },
  alt: "Imagen provisional: textura abstracta en tonos de barro.",
  provisional: true,
};

// Neutral fallback when an API image is missing or fails to load. Not a
// placeholder of any real piece: an empty paper-toned surface.
export const MEDIA_FALLBACK = {
  avif: `${P}/media-fallback.avif`,
  webp: `${P}/media-fallback.webp`,
  jpg: `${P}/media-fallback.jpg`,
  width: 800,
  height: 1000,
} as const;

// Home copy — PROVISIONAL defaults (brief: minimal text; phrase and CTA to be
// confirmed by the Product Owner). No claim here states a fact about a real
// artisan, piece or community.
export const homeCopy = {
  hero: {
    eyebrow: "Oaxaca · piezas únicas",
    title: "Cada pieza guarda quién la hizo.",
    subtitle: "Origen, oficio y autoría de piezas artesanales únicas, contados desde la pieza.",
    cta: { label: "Explorar piezas", href: "/piezas/" },
  },
  manifesto: {
    eyebrow: "Manifiesto",
    title: "La artesanía, primero.",
    body: "Detrás de cada pieza hay un territorio, un material, un proceso y una persona. ArtesaNFC documenta ese origen y lo guarda en la propia pieza: basta acercar el teléfono para leerlo.",
    reader: {
      label: "Ejemplo de lectura NFC",
      caption: "Acerca tu teléfono a la pieza",
      // What the example phone shows after the reading — and only that.
      screenTitle: "Pieza verificada",
      screenSubtitle: "Certificado de autenticidad",
      sceneAlt:
        "Ejemplo: el teléfono lee el chip de la pieza, su pantalla se enciende y muestra una máscara y el texto Pieza verificada, Certificado de autenticidad.",
    },
    points: [
      { term: "Origen", description: "La comunidad y el material de donde viene la pieza." },
      { term: "Oficio", description: "La técnica, el tiempo y el proceso de su elaboración." },
      { term: "Autoría", description: "Quién la creó, con su nombre y su historia." },
    ],
  },
  collection: {
    eyebrow: "Colección",
    title: "Piezas con nombre propio.",
    body: "Cada pieza tiene su ficha: artesano, comunidad, materiales, técnica y, cuando existe, un modelo para recorrerla en 3D.",
    cta: { label: "Ver la colección", href: "/piezas/" },
    secondary: { label: "Conocer a los artesanos", href: "/artesanos/" },
    // Illustrative certificate card: it shows what a certificate contains,
    // never data of a real piece (always labelled "Ejemplo").
    certificate: {
      label: "Certificado de autenticidad",
      example: "Ejemplo",
      title: "Pieza única",
      rows: [
        { term: "Autoría", value: "Registrada" },
        { term: "Origen", value: "Documentado" },
        { term: "Chip NFC", value: "Vinculado" },
      ],
      serial: "ANFC · 0000 · 0000",
    },
  },
  closing: {
    eyebrow: "Colaborar",
    title: "Para artesanos, coleccionistas y proyectos culturales.",
    audiences: [
      {
        term: "Artesanos",
        description: "Documentar su obra y acompañar cada pieza con su historia.",
      },
      {
        term: "Coleccionistas",
        description: "Conocer el origen y verificar la autenticidad de una pieza.",
      },
      {
        term: "Proyectos culturales",
        description: "Archivos, exposiciones e investigación en torno al oficio.",
      },
    ],
    // No public contact channel exists yet (docs: "Muy pronto habilitaremos
    // un canal directo"). Do not invent an address: see web/README.md.
    contactNote: "Muy pronto habilitaremos un canal directo de contacto.",
  },
} as const;

// /nosotros — "Colectivo" (2026-10). Team data given and authorised by each
// member (see Artesa_Brain/02_Producto/Equipo.md). A `null` link is shown as
// "por confirmar"; a member without photo shows initials.
export type ChannelKind =
  "email" | "instagram" | "facebook" | "tiktok" | "whatsapp" | "linkedin" | "github" | "web";

export interface Channel {
  readonly kind: ChannelKind;
  readonly label: string;
  /** Full URL (mailto:, https://wa.me/…) or null while not confirmed. */
  readonly href: string | null;
}

export interface Member {
  readonly name: string;
  readonly role: string;
  readonly bio: string;
  /** Initials for the avatar while there is no authorised photo. */
  readonly initials: string;
  readonly photo: string | null;
  readonly channels: readonly Channel[];
  readonly provisional: boolean;
}

export const aboutCopy = {
  hero: {
    eyebrow: "Nosotros · Colectivo",
    title: "Quienes conectamos cada pieza con su origen.",
    lead: "ArtesaNFC une el oficio de artesanas y artesanos de Oaxaca con una identidad digital verificable, para que cada pieza cuente quién la hizo, dónde y cómo.",
  },
  mission: {
    eyebrow: "Misión",
    title: "La artesanía con nombre propio.",
    body: "Documentamos el origen, el oficio y la autoría de piezas únicas y los dejamos al alcance de quien las sostiene: basta acercar el teléfono. La tecnología acompaña; la pieza y su autor son los protagonistas.",
    values: [
      {
        term: "Origen",
        description: "Cada pieza conserva su comunidad, su material y su historia.",
      },
      { term: "Autoría", description: "El nombre de quien la creó viaja con ella." },
      {
        term: "Confianza",
        description: "Un certificado verificable por NFC respalda su autenticidad.",
      },
    ],
  },
  team: {
    eyebrow: "El equipo",
    title: "Las personas detrás del proyecto.",
    members: [
      {
        name: "Alexis Ramírez Sibaja",
        role: "CEO",
        bio: "CEO de ArtesaNFC y estudiante de Ingeniería en Innovación Tecnológica (UABJO). Junto a su equipo convirtió una idea en plataforma real, diseñando y construyendo la web, la Gestión y las finanzas. Ingeniería inversa, persuasión, temple bajo presión y elocuencia al cerrar tratos.",
        initials: "A",
        photo: "/media/equipo/alexis.jpg",
        channels: [
          { kind: "email", label: "Correo", href: "mailto:armzsibaja@gmail.com" },
          { kind: "github", label: "GitHub", href: "https://github.com/Sibajx" },
        ],
        provisional: false,
      },
      {
        name: "Diego Sánchez",
        role: "CTO y Desarrollador",
        bio: "Entusiasta de la ciberseguridad y del desarrollo de software. Le apasionan el backend, las bases de datos, el desarrollo web y las aplicaciones. En ArtesaNFC lidera la tecnología y construye la plataforma que protege la autenticidad de cada pieza.",
        initials: "D",
        photo: null,
        channels: [
          { kind: "email", label: "Correo", href: "mailto:diego.sanchez.030604@gmail.com" },
          { kind: "instagram", label: "Instagram", href: "https://www.instagram.com/deigosd" },
          { kind: "github", label: "GitHub", href: "https://github.com/Diego1Sanchez" },
        ],
        provisional: false,
      },
      {
        name: "Mario Uriel Juárez Rosales",
        role: "CFO",
        bio: "Nacido en Oaxaca, Valles Centrales. Estudia Ingeniería en Innovación Tecnológica con especialidad en Energías Alternas. Experto en lógica y matemáticas avanzadas; en ArtesaNFC lleva el modelo financiero, las métricas y la contabilidad.",
        initials: "U",
        photo: "/media/equipo/uriel.jpg",
        channels: [
          { kind: "instagram", label: "Instagram", href: "https://www.instagram.com/uriel_1826/" },
          { kind: "github", label: "GitHub", href: "https://github.com/uriel2R" },
        ],
        provisional: false,
      },
      {
        name: "María Soledad Cruz Martínez",
        role: "Directora de Marketing (CMO)",
        bio: "Originaria de la Sierra Norte de Oaxaca, estudia Ingeniería en Innovación Tecnológica con enfoque en Energías Alternas en la UABJO. Le interesan la energía fotovoltaica, la eólica y la automatización de sistemas. Perseverante y curiosa, aprende de cada error.",
        initials: "S",
        photo: "/media/equipo/sol.jpg",
        channels: [
          { kind: "email", label: "Correo", href: "mailto:solcruzmartinez19@gmail.com" },
          { kind: "instagram", label: "Instagram", href: "https://www.instagram.com/kyr41906" },
          {
            kind: "linkedin",
            label: "LinkedIn",
            href: "https://www.linkedin.com/in/cruz-m%C3%A1rtinez-mar%C3%ADa-soledad-4423393b5",
          },
        ],
        provisional: false,
      },
      {
        name: "Hariel Davin Nicolás Bautista",
        role: "COO",
        bio: "Hábil en manufactura y herramientas, con certificaciones internacionales en infraestructura, hardware e instalaciones eléctricas y electrónicas. En ArtesaNFC cierra tratos, lleva la logística y las entregas, y capacita a los artesanos para preparar sus piezas para el NFC.",
        initials: "H",
        photo: null,
        channels: [
          { kind: "email", label: "Correo", href: "mailto:nicolashariel2103@gmail.com" },
          { kind: "instagram", label: "Instagram", href: "https://www.instagram.com/hariel__03" },
        ],
        provisional: false,
      },
    ] satisfies Member[],
  },
  company: {
    eyebrow: "Contacto",
    title: "Hablemos.",
    body: "Para artesanos, coleccionistas y proyectos culturales. Escríbenos por el canal que prefieras.",
    channels: [
      { kind: "email", label: "Correo", href: null },
      { kind: "instagram", label: "Instagram", href: null },
      { kind: "facebook", label: "Facebook", href: null },
      { kind: "whatsapp", label: "WhatsApp", href: null },
      { kind: "tiktok", label: "TikTok", href: null },
    ] satisfies Channel[],
  },
} as const;
