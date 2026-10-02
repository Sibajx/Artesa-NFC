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
