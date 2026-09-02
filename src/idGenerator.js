// Genera IDs con el formato REGION-AÑO-TIPO-CONSECUTIVO
// ej. IST-2026-HUI-0001
// Ver docs/diccionario-datos.md para el significado de cada código.

const TIPOS_VALIDOS = ["HUI", "VES", "GUA", "REB", "OTR"];
const REGIONES_VALIDAS = ["IST", "VAL", "MIX", "SIE", "COS", "CAN", "PAP"];

export async function generarId(db, region, tipo, anio) {
  region = String(region).toUpperCase();
  tipo = String(tipo).toUpperCase();

  if (!REGIONES_VALIDAS.includes(region)) {
    throw new Error(`Región inválida: ${region}. Usa una de: ${REGIONES_VALIDAS.join(", ")}`);
  }
  if (!TIPOS_VALIDOS.includes(tipo)) {
    throw new Error(`Tipo inválido: ${tipo}. Usa uno de: ${TIPOS_VALIDOS.join(", ")}`);
  }

  const prefijo = `${region}-${anio}-${tipo}-`;

  const { results } = await db
    .prepare("SELECT id FROM piezas WHERE id LIKE ?1 ORDER BY id DESC LIMIT 1")
    .bind(`${prefijo}%`)
    .all();

  let consecutivo = 1;
  if (results.length > 0) {
    const ultimoNumero = parseInt(results[0].id.split("-").pop(), 10);
    consecutivo = ultimoNumero + 1;
  }

  const consecutivoStr = String(consecutivo).padStart(4, "0");
  return `${prefijo}${consecutivoStr}`;
}
