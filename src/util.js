// Utilidades compartidas por el Worker

export function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

// Verifica el header  Authorization: Bearer <ADMIN_TOKEN>
// El valor real de ADMIN_TOKEN se configura como secreto en Cloudflare,
// nunca se escribe en el código ni en wrangler.toml.
export function isAuthorized(request, env) {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "").trim();
  return Boolean(env.ADMIN_TOKEN) && token === env.ADMIN_TOKEN;
}
