// Contract-shaped fixtures (docs/API_CONTRACT.md §4–§8). Fictitious data for
// tests only; never shipped in the site bundle.
import type {
  Artisan,
  CertificateAuthentic,
  MediaAsset,
  Piece,
  PieceSummary,
} from "../../src/lib/types";

export const heroImage: MediaAsset = {
  type: "image",
  role: "hero",
  url: "/media/test/pieces/mascara-prueba/hero.jpg",
  alt_text: "Fotografía de prueba",
  position: 0,
  format: { width: 800, height: 1000, mime_type: "image/jpeg" },
};

export const model3d: MediaAsset = {
  type: "model_3d",
  role: "model_3d",
  url: "/media/test/pieces/mascara-prueba/model.glb",
  alt_text: null,
  position: 1,
  format: { format: "glb", file_size_bytes: 4_404_019 },
};

export const artisanSummary = {
  slug: "artesano-prueba",
  full_name: "Artesano de Prueba",
  artistic_name: null,
};

export const piece: Piece = {
  slug: "mascara-prueba",
  public_code: "PIEZA-TEST-0001",
  name: "Máscara de prueba",
  description: "Descripción de prueba.",
  history: "Historia de prueba.",
  materials: ["madera de prueba", "pigmento de prueba"],
  technique: "Técnica de prueba",
  origin: "Origen de prueba, Oaxaca",
  creation_year: 2024,
  creation_date: null,
  dimensions: { height: 30, width: 20, depth: 15, unit: "cm" },
  visual_theme: null,
  availability_status: "available",
  artisan: artisanSummary,
  media: [heroImage, model3d],
};

export const pieceWithout3d: Piece = {
  ...piece,
  slug: "vasija-prueba",
  public_code: "PIEZA-TEST-0002",
  name: "Vasija de prueba",
  media: [{ ...heroImage, url: "/media/test/pieces/vasija-prueba/hero.jpg" }],
};

export const summary = (p: Piece): PieceSummary => ({
  slug: p.slug,
  name: p.name,
  public_code: p.public_code,
  availability_status: p.availability_status,
  cover_media: p.media.find((m) => m.role === "hero") ?? null,
});

export const artisan: Artisan = {
  ...artisanSummary,
  biography: "Biografía de prueba.",
  history: null,
  location: {
    locality: "Localidad de prueba",
    municipality: "Municipio de prueba",
    state: "Oaxaca",
    country: "México",
  },
  techniques: ["técnica de prueba"],
  languages: [],
  public_contact: null,
  media: [],
  pieces: [summary(piece), summary(pieceWithout3d)],
};

export const certificate: CertificateAuthentic = {
  authenticity: { status: "authentic", certificate_version: 1, issued_at: "2026-01-10T00:00:00Z" },
  piece,
  artisan,
  authenticity_metadata: { notes: "Nota de prueba." },
};
