#!/usr/bin/env python3
"""Copias reducidas (WebP) de las fotos de piezas y artesanos para la galería.

La API sirve las fotos a tamaño completo (1200×1600 JPG, ~220 KB). En la
galería se ven a 300-700 px, así que el navegador bajaba y decodificaba de más
y el scroll se trababa. Este script, que corre al publicar:

  1. pide a la API pública las piezas y artesanos publicados,
  2. baja cada foto /media/... (no las de /media/sitio/, que ya traen AVIF),
  3. guarda copias WebP de 480 y 960 px en <destino>/assets/fotos/,
  4. escribe <destino>/assets/js/fotos.js con el mapa ruta → copias, que
     render.js usa para el srcset. Una foto que no esté en el mapa (publicada
     después del último despliegue) se sigue viendo con la original.

    python3 -I galeria/scripts/fotos.py <carpeta de la copia a publicar>
"""

import hashlib
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

API = "https://api.artesanfc.com"
WIDTHS = (480, 960)
QUALITY = 78
MEDIA_RE = re.compile(r'"(/media/(?!sitio/)[^"?#]+\.(?:jpe?g|png|webp))"', re.IGNORECASE)


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json, image/*",
                                               "Origin": "https://artesanfc.com",
                                               "User-Agent": "artesanfc-galeria-publicar"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def list_all(path: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(20):
        url = f"{API}/api/v1{path}" + (f"?cursor={urllib.parse.quote(cursor)}" if cursor else "")
        body = json.loads(get(url))
        out.extend(body.get("data") or [])
        cursor = (body.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return out


def media_paths() -> list[str]:
    texts = []
    for kind in ("pieces", "artisans"):
        for item in list_all(f"/{kind}"):
            slug = item.get("slug")
            texts.append(json.dumps(item))
            if isinstance(slug, str) and re.fullmatch(r"[a-z0-9-]{1,120}", slug):
                texts.append(get(f"{API}/api/v1/{kind}/{slug}").decode())
    found = set()
    for t in texts:
        found.update(m for m in MEDIA_RE.findall(t) if ".." not in m)
    return sorted(found)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("uso: fotos.py <carpeta de la copia a publicar>")
    root = Path(sys.argv[1])
    out_dir = root / "assets" / "fotos"
    out_dir.mkdir(parents=True, exist_ok=True)
    mapa: dict[str, list] = {}
    total_antes = total_despues = 0
    for path in media_paths():
        raw = get(API + urllib.parse.quote(path))
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        key = hashlib.sha256(raw).hexdigest()[:12]  # por contenido: si cambia la foto, cambia el nombre
        variants = []
        for width in WIDTHS:
            if image.width <= width:
                continue
            copy = image.resize((width, round(image.height * width / image.width)), Image.Resampling.LANCZOS)
            name = f"{key}-{width}.webp"
            copy.save(out_dir / name, "WEBP", quality=QUALITY, method=6)
            variants.append([width, f"assets/fotos/{name}"])
        if variants:
            mapa[path] = variants
            total_antes += len(raw)
            total_despues += (out_dir / Path(variants[0][1]).name).stat().st_size
    js = ("// Generado por scripts/fotos.py al publicar. No editar a mano.\n"
          f"export const FOTOS = {json.dumps(mapa, indent=1)};\n")
    (root / "assets" / "js" / "fotos.js").write_text(js)
    print(f"fotos: {len(mapa)} reducidas ({total_antes // 1024} KB originales → "
          f"{total_despues // 1024} KB a {WIDTHS[0]} px)")


if __name__ == "__main__":
    main()
