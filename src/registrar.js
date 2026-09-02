// POST /api/registrar
// Registra una artesana (si no existe) y una pieza nueva, generando su ID único.
// Requiere header: Authorization: Bearer <ADMIN_TOKEN>

import { jsonResponse, isAuthorized } from "./util.js";
import { generarId } from "./idGenerator.js";

function slugify(texto) {
  return texto
    .toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "") // quita acentos
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

export async function handleRegistrar(request, env) {
  if (!isAuthorized(request, env)) {
    return jsonResponse({ error: "No autorizado" }, 401);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ error: "JSON inválido en el cuerpo de la solicitud" }, 400);
  }

  const { region, tipo, artesana, pieza } = body;

  if (!region || !tipo || !artesana || !pieza) {
    return jsonResponse(
      { error: "Faltan campos obligatorios: region, tipo, artesana, pieza" },
      400
    );
  }
  if (!pieza.nombre_prenda) {
    return jsonResponse({ error: "pieza.nombre_prenda es obligatorio" }, 400);
  }

  const db = env.DB;

  try {
    // 1. Resolver la artesana: reutilizar si ya existe, crearla si no
    let artesanaId = artesana.id;

    if (artesanaId) {
      const existente = await db
        .prepare("SELECT id FROM artesanas WHERE id = ?1")
        .bind(artesanaId)
        .first();

      if (!existente) {
        if (!artesana.nombre || !artesana.localidad || !artesana.municipio) {
          return jsonResponse(
            { error: `La artesana '${artesanaId}' no existe. Incluye nombre, localidad y municipio para crearla.` },
            400
          );
        }
        await crearArtesana(db, artesanaId, artesana);
      }
    } else {
      if (!artesana.nombre || !artesana.localidad || !artesana.municipio) {
        return jsonResponse(
          { error: "Sin artesana.id: incluye nombre, localidad y municipio para crear una nueva artesana." },
          400
        );
      }
      artesanaId = slugify(artesana.nombre);
      await crearArtesana(db, artesanaId, artesana);
    }

    // 2. Generar el ID único de la pieza
    const anio = new Date().getFullYear();
    const id = await generarId(db, region, tipo, anio);
    const urlCertificado = `https://${env.CERT_HOST || "artesanfc.com/cert"}/${id}`;

    // 3. Insertar la pieza
    await db
      .prepare(
        `INSERT INTO piezas (
           id, artesana_id, nombre_prenda, tipo, tecnica, tela, colores,
           horas_bordado, semanas_trabajo, hecho_a_mano, pieza_unica, historia,
           foto_principal_url, fotos_adicionales, url_certificado, fecha_creacion_pieza
         ) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16)`
      )
      .bind(
        id,
        artesanaId,
        pieza.nombre_prenda,
        tipo.toUpperCase(),
        pieza.tecnica || null,
        pieza.tela || null,
        pieza.colores ? JSON.stringify(pieza.colores) : null,
        pieza.horas_bordado || null,
        pieza.semanas_trabajo || null,
        pieza.hecho_a_mano === false ? 0 : 1,
        pieza.pieza_unica === false ? 0 : 1,
        pieza.historia || null,
        pieza.foto_principal_url || null,
        pieza.fotos_adicionales ? JSON.stringify(pieza.fotos_adicionales) : null,
        urlCertificado,
        pieza.fecha_creacion_pieza || null
      )
      .run();

    return jsonResponse({ ok: true, id, url_certificado: urlCertificado }, 201);
  } catch (err) {
    return jsonResponse({ error: err.message || "Error al registrar la pieza" }, 500);
  }
}

async function crearArtesana(db, id, artesana) {
  await db
    .prepare(
      `INSERT INTO artesanas (id, nombre, localidad, municipio, estado, lengua, bio, foto_url, comercio_justo)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)`
    )
    .bind(
      id,
      artesana.nombre,
      artesana.localidad,
      artesana.municipio,
      artesana.estado || "Oaxaca",
      artesana.lengua || null,
      artesana.bio || null,
      artesana.foto_url || null,
      artesana.comercio_justo === false ? 0 : 1
    )
    .run();
}
