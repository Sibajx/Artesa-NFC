"""ADR-030 phase 5: draws a designed original certificate as SVG.

One renderer for every place that shows it (Gestión's preview, the
artisan's review link, the buyer's unlocked original), so they always
match. Pure function of the design's params: a frozen version renders the
same forever.

Safety: every text is XML-escaped, colours are validated "#rrggbb", there
are no external references, scripts or fonts (the SVG is shown inside
<img>, where a web font would not load anyway). The team's artwork (phase
5b) is embedded as a data: URI built server-side from a stored, re-encoded
PNG/JPEG (services/certificate_art.py); the params only carry its hash.
"""
from __future__ import annotations

import math
import random
import re
import textwrap
from xml.sax.saxutils import escape

TEMPLATES = ("clasico", "greca", "constelacion")
VARIANTS = ("claro", "oscuro")
# Phase 5b: where the team's artwork goes.
ART_PLACEMENTS = ("sello", "encabezado", "fondo")
HEX_RE = re.compile(r"#[0-9a-f]{6}")
W, H = 1200, 1600
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "'Helvetica Neue', Arial, sans-serif"

_FALLBACK = ["#1d1915", "#c9761c", "#efe4cf", "#5c3f28"]


def _palette(params: dict) -> list[str]:
    colors = [c for c in params.get("palette") or [] if isinstance(c, str) and HEX_RE.fullmatch(c)]
    return colors if len(colors) >= 3 else _FALLBACK


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _luma(hex_color: str) -> float:
    r, g, b = _rgb(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _chroma(hex_color: str) -> int:
    rgb = _rgb(hex_color)
    return max(rgb) - min(rgb)


def _roles(colors: list[str]) -> dict[str, str]:
    by_luma = sorted(colors, key=_luma)
    accent = max(colors, key=_chroma)
    return {"dark": by_luma[0], "light": by_luma[-1], "accent": accent,
            "mid": next((c for c in colors if c not in (by_luma[0], by_luma[-1], accent)), accent)}


def _t(text: object) -> str:
    return escape(str(text or ""))


def _lines(text: str, width: int, limit: int) -> list[str]:
    lines = textwrap.wrap(text or "", width=width)
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = lines[-1].rstrip(" .,;") + "…"
    return lines


def _text_block(lines: list[str], x: float, y: float, size: int, leading: float, **attrs: str) -> str:
    extra = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    spans = "".join(f'<tspan x="{x}" dy="{0 if i == 0 else size * leading:.1f}">{_t(line)}</tspan>'
                    for i, line in enumerate(lines))
    return f'<text x="{x}" y="{y}" font-size="{size}" {extra}>{spans}</text>'


# --- ornaments ------------------------------------------------------------------------


def _band(colors: list[str], x: float, y: float, w: float, h: float, outline: str) -> str:
    step = w / len(colors)
    cells = "".join(f'<rect x="{x + i * step:.1f}" y="{y}" width="{step + 0.5:.1f}" height="{h}" fill="{c}"/>'
                    for i, c in enumerate(colors))
    # The outline keeps a colour equal to the paper visible.
    return cells + f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{outline}" stroke-width="1.5"/>'


def _corners(color: str, rng: random.Random) -> str:
    """Small seeded diamonds in the corners of the inner frame."""
    size = rng.choice((14, 18, 22))
    parts = []
    for cx, cy in ((66, 66), (W - 66, 66), (66, H - 66), (W - 66, H - 66)):
        parts.append(f'<path d="M{cx},{cy - size} L{cx + size},{cy} L{cx},{cy + size} L{cx - size},{cy} Z" fill="{color}"/>')
    return "".join(parts)


def _seal(cx: float, cy: float, r: float, roles: dict[str, str], rng: random.Random) -> str:
    petals = rng.choice((8, 10, 12, 16))
    inner = r * rng.uniform(0.45, 0.6)
    parts = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{roles["accent"]}" stroke-width="6"/>',
             f'<circle cx="{cx}" cy="{cy}" r="{r - 14}" fill="none" stroke="{roles["dark"]}" stroke-width="2"/>']
    for i in range(petals):
        a = 2 * math.pi * i / petals
        x1, y1 = cx + inner * math.cos(a), cy + inner * math.sin(a)
        x2, y2 = cx + (r - 22) * math.cos(a), cy + (r - 22) * math.sin(a)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{roles["mid"]}" stroke-width="3" stroke-linecap="round"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{inner - 8:.1f}" fill="{roles["accent"]}"/>')
    return "".join(parts)


def _greca(colors: list[str], roles: dict[str, str], rng: random.Random, inset: float) -> str:
    """A step-fret (greca, as in Mitla) running around the sheet."""
    unit = rng.choice((28, 32, 36))
    color = roles["accent"]
    path = []

    def fret(x: float, y: float, horizontal: bool) -> str:
        u = unit / 4
        pts = [(0, 4), (0, 0), (4, 0), (4, 3), (2, 3), (2, 2)] if horizontal else [(4, 0), (0, 0), (0, 4), (3, 4), (3, 2), (2, 2)]
        return "M" + " L".join(f"{x + px * u:.1f},{y + py * u:.1f}" for px, py in pts)

    x = inset
    while x + unit <= W - inset:
        path.append(fret(x, inset, True))
        path.append(fret(x, H - inset - unit, True))
        x += unit
    y = inset + unit
    while y + unit <= H - inset - unit:
        path.append(fret(inset, y, False))
        path.append(fret(W - inset - unit, y, False))
        y += unit
    return (f'<path d="{" ".join(path)}" fill="none" stroke="{color}" stroke-width="5" '
            f'stroke-linejoin="miter" stroke-linecap="square"/>')


def _constellation(colors: list[str], rng: random.Random, area: tuple[float, float, float, float]) -> str:
    x0, y0, x1, y1 = area
    points = [(rng.uniform(x0, x1), rng.uniform(y0, y1)) for _ in range(rng.randint(26, 38))]
    parts = []
    for i, (x, y) in enumerate(points):
        near = sorted(points, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)[1:3]
        for nx, ny in near:
            parts.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{nx:.1f}" y2="{ny:.1f}" '
                         f'stroke="{colors[i % len(colors)]}" stroke-opacity="0.35" stroke-width="1.5"/>')
    for i, (x, y) in enumerate(points):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rng.uniform(2.5, 7):.1f}" fill="{colors[i % len(colors)]}"/>')
    return "".join(parts)


# --- the sheet ------------------------------------------------------------------------


def _image(uri: str, x: float, y: float, w: float, h: float, *, slice_: bool = False, extra: str = "") -> str:
    fit = "xMidYMid slice" if slice_ else "xMidYMid meet"
    return (f'<image href="{_t(uri)}" x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" '
            f'preserveAspectRatio="{fit}"{extra}/>')


def render(params: dict, *, version: int, approved_by: str | None = None, approved_on: str | None = None,
           watermark: str | None = None, art_uri: str | None = None) -> str:
    """The certificate as an SVG document. ``watermark`` (e.g. "BORRADOR")
    is drawn across the sheet for anything not yet published. ``art_uri`` is
    the team's artwork for ``params["art"]``, as a data: URI."""
    template = params.get("template") if params.get("template") in TEMPLATES else "clasico"
    variant = "oscuro" if template == "constelacion" else (params.get("variant") if params.get("variant") in VARIANTS else "claro")
    colors = _palette(params)
    roles = _roles(colors)
    rng = random.Random(int(params.get("seed") or 1))
    paper, ink = (roles["dark"], roles["light"]) if variant == "oscuro" else (roles["light"], roles["dark"])
    if variant == "oscuro" and _luma(paper) > 60:
        paper = "#141110"
    if variant == "claro" and _luma(paper) < 200:
        paper = "#f6efe2"

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'role="img" aria-label="Certificado original de {_t(params.get("piece_name"))}">',
           f'<rect width="{W}" height="{H}" fill="{paper}"/>']
    art = params.get("art") if isinstance(params.get("art"), dict) and art_uri else None
    placement = art.get("placement") if art and art.get("placement") in ART_PLACEMENTS else "sello"
    if art and placement == "fondo":
        opacity = art.get("opacity") if isinstance(art.get("opacity"), (int, float)) else 0.15
        out.append(_image(art_uri, 0, 0, W, H, slice_=True, extra=f' opacity="{min(max(opacity, 0.05), 0.6):.2f}"'))

    header_art = art is not None and placement == "encabezado"
    if template == "constelacion" and not header_art:
        out.append(_constellation(colors, rng, (100, 100, W - 100, 430)))
    if template == "greca":
        out.append(_greca(colors, roles, rng, 56))
    else:
        out.append(f'<rect x="48" y="48" width="{W - 96}" height="{H - 96}" fill="none" stroke="{ink}" stroke-width="3"/>')
        out.append(f'<rect x="66" y="66" width="{W - 132}" height="{H - 132}" fill="none" stroke="{roles["accent"]}" stroke-width="1.5"/>')
        out.append(_corners(roles["accent"], rng))

    dense = template == "constelacion"
    top = 560 if dense else 330
    if header_art:
        y0 = 110 if dense else 96
        out.append(_image(art_uri, 300, y0, W - 600, top - 90 - y0))
    out.append(_band(colors, 300, top - 70, W - 600, 12, ink))
    out.append(f'<text x="{W / 2}" y="{top}" text-anchor="middle" font-family="{SANS}" font-size="26" '
               f'letter-spacing="7" fill="{ink}">{_t((params.get("title") or "Certificado original").upper())}</text>')
    name_lines = _lines(params.get("piece_name") or "", 18, 2)
    out.append(_text_block(name_lines, W / 2, top + 130, 104, 1.05, text_anchor="middle",
                           font_family=SERIF, fill=ink))
    y = top + 130 + 104 * 1.05 * (len(name_lines) - 1) + 80
    out.append(f'<text x="{W / 2}" y="{y:.0f}" text-anchor="middle" font-family="{SERIF}" font-style="italic" '
               f'font-size="40" fill="{ink}">por {_t(params.get("artisan_name"))}</text>')

    quote = (params.get("quote") or "").strip()
    if quote:
        q_lines = _lines(f"“{quote}”", 40, 3 if dense else 4)
        out.append(_text_block(q_lines, W / 2, y + 120, 34, 1.4, text_anchor="middle",
                               font_family=SERIF, font_style="italic", fill=roles["mid"] if variant == "claro" else ink))

    seal_y, seal_r = (H - 445, 88) if dense else (H - 540, 120)
    if art is not None and placement == "sello":
        # The artwork inside the seal's rings, clipped to a circle.
        out.append(f'<clipPath id="art-seal"><circle cx="{W / 2}" cy="{seal_y}" r="{seal_r - 16}"/></clipPath>')
        out.append(f'<circle cx="{W / 2}" cy="{seal_y}" r="{seal_r}" fill="none" stroke="{roles["accent"]}" stroke-width="6"/>')
        side = 2 * (seal_r - 16)
        out.append(_image(art_uri, W / 2 - side / 2, seal_y - side / 2, side, side, extra=' clip-path="url(#art-seal)"'))
    else:
        out.append(_seal(W / 2, seal_y, seal_r, roles, rng))

    facts = [("Código", params.get("public_code") or ""), ("Diseño", f"versión {version}")]
    if approved_by:
        facts.append(("Aprobado por", f"{approved_by}{' · ' + approved_on if approved_on else ''}"))
    fy = H - 300
    for label, value in facts:
        out.append(f'<text x="{W / 2 - 20}" y="{fy}" text-anchor="end" font-family="{SANS}" font-size="22" '
                   f'letter-spacing="3" fill="{roles["accent"] if variant == "oscuro" else roles["mid"]}">{_t(label.upper())}</text>')
        out.append(f'<text x="{W / 2 + 20}" y="{fy}" font-family="{SANS}" font-size="26" fill="{ink}">{_t(value)}</text>')
        fy += 44
    out.append(f'<text x="{W / 2}" y="{H - 110}" text-anchor="middle" font-family="{SANS}" font-size="22" '
               f'letter-spacing="4" fill="{ink}" fill-opacity="0.7">ARTESANFC.COM · PIEZA ÚNICA</text>')

    if watermark:
        out.append(f'<text x="{W / 2}" y="{H / 2}" text-anchor="middle" font-family="{SANS}" font-size="150" '
                   f'font-weight="700" fill="{roles["accent"]}" fill-opacity="0.13" '
                   f'transform="rotate(-30 {W / 2} {H / 2})">{_t(watermark)}</text>')
    out.append("</svg>")
    return "".join(out)
