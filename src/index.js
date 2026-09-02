// Worker principal de Artesa NFC.
// Sirve el sitio estático y maneja dos rutas dinámicas:
//   POST /api/registrar   -> registra una pieza nueva (requiere token)
//   GET  /cert/:id        -> genera el certificado público de esa pieza

import { renderCertificado } from "./certificado.js";
import { handleRegistrar } from "./registrar.js";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/api/registrar" && request.method === "POST") {
      return handleRegistrar(request, env);
    }

    const certMatch = path.match(/^\/cert\/([A-Za-z0-9-]+)\/?$/);
    if (certMatch && request.method === "GET") {
      const id = certMatch[1].toUpperCase();

      const pieza = await env.DB.prepare("SELECT * FROM piezas WHERE id = ?1").bind(id).first();
      if (!pieza) {
        return new Response("Certificado no encontrado", { status: 404 });
      }

      const artesana = await env.DB
        .prepare("SELECT * FROM artesanas WHERE id = ?1")
        .bind(pieza.artesana_id)
        .first();

      return new Response(renderCertificado(pieza, artesana), {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }

    // Cualquier otra ruta: servir el sitio estático de /public
    return env.ASSETS.fetch(request);
  },
};
