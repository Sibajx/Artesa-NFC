// PassportPanel — the digital passport of a piece: its public identity card.
//
// Two variants, one layout:
// - "public" (/piezas/{slug}): public registry data only. It says the same
//   thing about every piece regarding authenticity, because the public piece
//   must not reveal whether a certificate or NFC tag exists
//   (API_CONTRACT.md §5, ADR-005/006).
// - "certificate" (/c/{token}, after `authentic`): adds the certificate
//   status, version, issue date and curated notes (§7 allowlist).
// Rows without data are omitted; nothing is invented. Never shows a token,
// token hash, NFC UID or internal id — none of them exist in these payloads.
import type { ReactNode } from "react";
import {
  availabilityLabel,
  displayName,
  formatCreation,
  formatDate,
  formatDimensions,
  joinPresent,
} from "@/lib/format";
import { artisanPath } from "@/lib/routes";
import type { Artisan, CertificateAuthentic, Piece } from "@/lib/types";

interface Props {
  piece: Piece;
  /** Full artisan when available (adds community and region). */
  artisan: Artisan | null;
  certificate?: Pick<CertificateAuthentic, "authenticity" | "authenticity_metadata">;
}

interface Row {
  label: string;
  value: ReactNode;
  id: string;
}

export function PassportPanel({ piece, artisan, certificate }: Props) {
  const location = artisan?.location ?? null;
  const rows: Row[] = [];
  const push = (id: string, label: string, value: ReactNode | null | undefined) => {
    if (value !== null && value !== undefined && value !== "") rows.push({ id, label, value });
  };

  push("code", "Código público", <span className="passport__code">{piece.public_code}</span>);
  push("piece", "Pieza", piece.name);
  push(
    "artisan",
    "Artesano",
    <a href={artisanPath(piece.artisan.slug)}>{displayName(piece.artisan)}</a>,
  );
  push("community", "Comunidad", location?.locality);
  push(
    "region",
    "Región",
    location
      ? joinPresent(
          [
            location.municipality !== location.locality ? location.municipality : null,
            location.state,
          ],
          ", ",
        )
      : null,
  );
  push("origin", "Origen", piece.origin);
  push("technique", "Técnica", piece.technique);
  push("materials", "Materiales", piece.materials.length > 0 ? piece.materials.join(", ") : null);
  push("date", piece.creation_date ? "Fecha" : "Año", formatCreation(piece));
  push("dimensions", "Dimensiones", formatDimensions(piece.dimensions));
  push("availability", "Disponibilidad", availabilityLabel(piece.availability_status));

  if (certificate) {
    push(
      "cert-version",
      "Versión del certificado",
      String(certificate.authenticity.certificate_version),
    );
    push("cert-issued", "Emitido", formatDate(certificate.authenticity.issued_at));
  }

  return (
    <section
      className="passport"
      aria-labelledby="passport-title"
      data-variant={certificate ? "certificate" : "public"}
    >
      <header className="passport__header">
        <p className="eyebrow">
          {certificate ? "Certificado de autenticidad" : "Pasaporte digital"}
        </p>
        <h2 id="passport-title" className="heading-2">
          {certificate ? "Certificado válido" : "Registro de la pieza"}
        </h2>
      </header>

      <p className="passport__status" data-status={certificate ? "authentic" : "registered"}>
        <span className="passport__status-mark" aria-hidden="true">
          {certificate ? "✓" : "●"}
        </span>
        {certificate
          ? "Certificado verificado por ArtesaNFC para esta pieza."
          : "Pieza registrada en el catálogo público de ArtesaNFC."}
      </p>

      <dl className="passport__rows">
        {rows.map((row) => (
          <div key={row.id} className="passport__row" data-row={row.id}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>

      {certificate?.authenticity_metadata.notes && (
        <p className="passport__notes">{certificate.authenticity_metadata.notes}</p>
      )}

      {!certificate && (
        <p className="passport__footnote muted">
          La autenticidad de una pieza física se verifica acercando un teléfono a su etiqueta NFC.
          Esta página pública no certifica la pieza por sí misma.
        </p>
      )}
    </section>
  );
}
