// Genera el HTML del certificado público a partir de los datos reales
// de una pieza y su artesana. Usa la misma paleta de artesanfc.com.

export function renderCertificado(pieza, artesana) {
  const colores = pieza.colores ? JSON.parse(pieza.colores) : [];

  return `<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${escapeHtml(pieza.nombre_prenda)} · Certificado ${pieza.id} · Artesa NFC</title>
<style>
  :root {
    --negro:#080808; --negro-3:#1C1C1C;
    --naranja2:#FF7A30; --magenta2:#F2359A; --verde2:#3CD43C;
    --blanco:#F2F2F2; --blanco2:#B8B8B8; --amarillo:#E8C020; --dim:#484848;
  }
  * { margin:0; padding:0; box-sizing:border-box }
  body { background:var(--negro); color:var(--blanco); font-family:'Georgia','Times New Roman',serif; padding:40px 20px; }
  .wrap { max-width:640px; margin:0 auto; }
  .eyebrow { font-size:11px; letter-spacing:3px; text-transform:uppercase; color:var(--verde2); }
  h1 { font-size:clamp(26px,6vw,40px); margin:12px 0 6px; }
  .tipo { color:var(--blanco2); font-style:italic; margin-bottom:24px; }
  .badge-autentico { display:inline-block; padding:8px 18px; border-radius:40px; border:1px solid rgba(60,212,60,.35); background:rgba(40,180,40,.15); color:var(--verde2); font-size:12px; letter-spacing:1px; margin-bottom:32px; }
  .card { background:var(--negro-3); border:1px solid rgba(255,255,255,.08); border-radius:14px; padding:24px; margin-bottom:20px; }
  .card h2 { font-size:13px; letter-spacing:1.5px; text-transform:uppercase; color:var(--naranja2); margin-bottom:10px; }
  .card p { line-height:1.6; color:var(--blanco2); }
  .cert-val { font-size:20px; letter-spacing:1px; color:var(--amarillo); margin:6px 0; }
  .paleta { display:flex; gap:8px; margin-top:10px; }
  .swatch { width:28px; height:28px; border-radius:6px; border:1px solid rgba(255,255,255,.15); }
  footer { text-align:center; color:var(--dim); font-size:11px; letter-spacing:1px; margin-top:40px; }
</style>
</head>
<body>
<div class="wrap">
  <span class="eyebrow">Certificado de autenticidad</span>
  <h1>${escapeHtml(pieza.nombre_prenda)}</h1>
  <p class="tipo">${escapeHtml(pieza.tecnica || "")}</p>
  <span class="badge-autentico">✓ Pieza auténtica verificada</span>

  <div class="card">
    <h2>Número de autenticidad</h2>
    <p class="cert-val">ARTESA-NFC · ${pieza.id}</p>
    <p>Registrado el ${pieza.fecha_registro}</p>
  </div>

  <div class="card">
    <h2>Artesana</h2>
    <p><strong>${escapeHtml(artesana.nombre)}</strong></p>
    <p>${escapeHtml(artesana.localidad)}, ${escapeHtml(artesana.municipio)}, ${escapeHtml(artesana.estado)}</p>
    ${artesana.lengua ? `<p>Hablante de ${escapeHtml(artesana.lengua)}</p>` : ""}
    ${artesana.bio ? `<p style="margin-top:10px">${escapeHtml(artesana.bio)}</p>` : ""}
  </div>

  ${pieza.historia ? `<div class="card"><h2>Historia de la pieza</h2><p>${escapeHtml(pieza.historia)}</p></div>` : ""}

  <div class="card">
    <h2>Detalles</h2>
    ${pieza.tela ? `<p>Tela: ${escapeHtml(pieza.tela)}</p>` : ""}
    ${pieza.horas_bordado ? `<p>${pieza.horas_bordado} horas de bordado</p>` : ""}
    ${pieza.semanas_trabajo ? `<p>${pieza.semanas_trabajo} semanas de trabajo</p>` : ""}
    <p>${pieza.hecho_a_mano ? "100% hecho a mano" : ""} ${pieza.pieza_unica ? "· Pieza única" : ""}</p>
    ${colores.length ? `<div class="paleta">${colores.map((c) => `<div class="swatch" style="background:${escapeHtml(c)}"></div>`).join("")}</div>` : ""}
  </div>

  <footer>artesanfc.com · Patrimonio cultural de Oaxaca protegido digitalmente</footer>
</div>
</body>
</html>`;
}

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
