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

Motion: a <style> inside the SVG animates the greca (drawing itself), the
seal (stamp-in, NFC waves, spinning ring), the title band (foil shine), a
light that runs around the frame, drifting motes and a slow sheen. It lives
under ``prefers-reduced-motion: no-preference`` and the resting state is the
complete static drawing, so print, reduced motion and viewers without CSS
animation see the same sheet. CSS animation runs inside <img>; scripts do not.

The seal is a tesseract (a 4D hypercube) turning in three nested layers, one
per production stage (chip, packing, shipping). Its rotation is projected
here, in Python, into a fixed number of frames and played with one SMIL
<animate> on ``d``: real 4D motion with no script. Reduced motion (and any
viewer that ignores CSS) shows the first frame instead.
"""
from __future__ import annotations

import math
import random
import re
import textwrap
from functools import lru_cache
from xml.sax.saxutils import escape

TEMPLATES = ("clasico", "greca", "constelacion", "telar", "amate", "barro", "mitla", "alebrije")
# Templates that draw a band at the top and bottom of the sheet (the title starts lower).
_BANDED = ("telar", "barro", "mitla", "alebrije")
VARIANTS = ("claro", "oscuro")
# Phase 5b: where the team's artwork goes.
ART_PLACEMENTS = ("sello", "encabezado", "fondo")
HEX_RE = re.compile(r"#[0-9a-f]{6}")
W, H = 1200, 1600
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "'Helvetica Neue', Arial, sans-serif"

_FALLBACK = ["#1d1915", "#c9761c", "#efe4cf", "#5c3f28"]

# Resting state = the finished drawing; everything below only plays when the
# viewer allows motion. Rings and shine start invisible (opacity 0 attribute).
_MOTION = (
    "@media (prefers-reduced-motion:no-preference){"
    ".m-greca{animation:m-draw .7s ease-out both}"
    ".m-wave{transform-box:fill-box;transform-origin:center;animation:m-wave 3.6s ease-out 1.4s infinite}"
    ".m-wave2{animation-delay:2.6s}.m-wave3{animation-delay:3.8s}"
    ".m-shine{animation:m-shine 7s ease-in-out infinite}"
    ".m-stamp{transform-box:fill-box;transform-origin:center;animation:m-stamp 1.1s cubic-bezier(.2,.9,.25,1.15) .9s both}"
    ".m-ring{animation:m-spin 54s linear infinite}.m-ticks{animation:m-spinr 90s linear infinite}"
    ".m-glow{transform-box:fill-box;transform-origin:center;animation:m-glow 4.2s ease-in-out infinite}"
    ".m-chip{transform-box:fill-box;transform-origin:center;animation:m-chip 2.4s ease-in-out infinite}"
    ".m-layer-a{animation:m-spin 38s linear infinite}.m-layer-b{animation:m-spinr 26s linear infinite}"
    ".m-layer-c{animation:m-spin 17s linear infinite}"
    ".m-flow{opacity:1;animation:m-flow 9s linear infinite}.m-flow2{animation-delay:-4.5s}"
    ".m-mote{animation:m-mote 11s ease-in infinite}"
    ".m-sheen{opacity:1;animation:m-sheen 12s ease-in-out infinite}"
    ".m-rise{animation:m-rise .9s cubic-bezier(.2,.8,.2,1) both}"
    ".m-holo{opacity:1;animation:m-holo 8s ease-in-out 2.4s infinite}"
    ".m-light{animation:m-light 9s ease-in-out infinite}"
    ".m-tess-static{display:none}.m-tess-live{display:inline}"
    "@keyframes m-draw{from{stroke-dasharray:1;stroke-dashoffset:1}to{stroke-dasharray:1;stroke-dashoffset:0}}"
    "@keyframes m-wave{0%{opacity:.55;transform:scale(1)}100%{opacity:0;transform:scale(1.55)}}"
    "@keyframes m-shine{0%{transform:translateX(-360px)}30%,100%{transform:translateX(1400px)}}"
    "@keyframes m-stamp{0%{opacity:0;transform:scale(1.55) rotate(-8deg)}55%{opacity:1}100%{opacity:1;transform:none}}"
    "@keyframes m-spin{to{transform:rotate(360deg)}}@keyframes m-spinr{to{transform:rotate(-360deg)}}"
    "@keyframes m-glow{0%,100%{opacity:.55;transform:scale(.94)}50%{opacity:1;transform:scale(1.06)}}"
    "@keyframes m-chip{0%,100%{opacity:.7;transform:scale(.8)}50%{opacity:1;transform:scale(1.25)}}"
    "@keyframes m-flow{to{stroke-dashoffset:-50}}"
    "@keyframes m-mote{0%{opacity:0;transform:translateY(0)}18%{opacity:.85}100%{opacity:0;transform:translateY(-560px)}}"
    "@keyframes m-rise{from{opacity:0;transform:translateY(26px)}to{opacity:1;transform:none}}"
    "@keyframes m-holo{0%{transform:translateX(0)}38%,100%{transform:translateX(2300px)}}"
    "@keyframes m-light{0%,100%{opacity:.4}14%,46%{opacity:1}}"
    "@keyframes m-sheen{0%{transform:translateX(0)}40%,100%{transform:translateX(2300px)}}"
    "}"
)


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


def _edition_label(edition: object) -> str:
    """"PIEZA 3 DE 10" for a numbered edition, else the one-of-a-kind line."""
    if isinstance(edition, dict) and isinstance(edition.get("number"), int) and isinstance(edition.get("total"), int):
        return f"PIEZA {edition['number']} DE {edition['total']}"
    return "PIEZA ÚNICA"


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
    shine = (f'<clipPath id="band-clip"><rect x="{x}" y="{y}" width="{w}" height="{h}"/></clipPath>'
             f'<linearGradient id="band-foil" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
             f'<stop offset=".5" stop-color="#fff" stop-opacity=".75"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>'
             f'<g clip-path="url(#band-clip)"><rect class="m-shine" x="{x - 120}" y="{y}" width="120" height="{h}" '
             f'fill="url(#band-foil)" opacity="0.0"/></g>')
    return cells + shine + f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{outline}" stroke-width="1.5"/>'


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


def _mix(color: str, other: str, amount: float) -> str:
    """``color`` moved ``amount`` (0 to 1) towards ``other``, both "#rrggbb"."""
    a, b = _rgb(color), _rgb(other)
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


_TESS_FRAMES = 32


@lru_cache(maxsize=None)
def _tesseract_frames() -> tuple[str, ...]:
    """One full turn of a tesseract as SVG path data, ``_TESS_FRAMES`` frames.

    The 16 vertices of a 4D hypercube are turned in three 4D planes (speeds 1,
    2 and 1, so the last frame equals the first), pushed to 3D and then to 2D
    with perspective, and scaled so the largest reach over the whole turn is
    +-100. The 32 edges are one path per frame, in whole numbers."""
    verts = [tuple(1.0 if (i >> b) & 1 else -1.0 for b in range(4)) for i in range(16)]
    edges = [(i, i ^ (1 << b)) for i in range(16) for b in range(4) if i < i ^ (1 << b)]

    def turn(u: float, v: float, a: float) -> tuple[float, float]:
        return u * math.cos(a) - v * math.sin(a), u * math.sin(a) + v * math.cos(a)

    turns: list[list[tuple[float, float]]] = []
    for f in range(_TESS_FRAMES):
        t = 2 * math.pi * f / _TESS_FRAMES
        points = []
        for x, y, z, w in verts:
            x, w = turn(x, w, t)
            z, w = turn(z, w, 2 * t)
            x, y = turn(x, y, t)
            k4 = 3.0 / (3.0 - w)  # 4D -> 3D
            x, y, z = x * k4, y * k4, z * k4
            y, z = turn(y, z, 0.5)  # a fixed tilt, so depth is readable
            x, z = turn(x, z, 0.6)
            k3 = 9.0 / (9.0 - z)  # 3D -> 2D
            points.append((x * k3, y * k3))
        turns.append(points)
    reach = max(max(abs(x), abs(y)) for points in turns for x, y in points) or 1.0
    frames = []
    for points in turns:
        frames.append("".join(
            f"M{round(points[a][0] / reach * 100)} {round(points[a][1] / reach * 100)}"
            f"L{round(points[b][0] / reach * 100)} {round(points[b][1] / reach * 100)}" for a, b in edges))
    return tuple(frames)


def _seal_ring_text(radius: float) -> tuple[str, float]:
    """The words around the seal and the size that fits them on the circle."""
    full = radius >= 80
    text = ("ARTESANFC · PIEZA ORIGINAL · CHIP · EMPAQUE · ENVÍO · " if full
            else "ARTESANFC · CHIP · EMPAQUE · ENVÍO · ")
    return text, round(min(17.0, 2 * math.pi * radius / (len(text) * 0.72)), 1)


def _seal_tesseract(cx: float, cy: float, r: float, roles: dict[str, str], paper: str, dark: bool) -> str:
    """The ArtesaNFC seal: a stamp-like disc in the piece's dominant colour, words
    around it (the three production stages among them), and a tesseract of
    three nested layers (chip inside, packing, shipping outside) turning in the
    middle. The layers are the stages; the dominant colour is ``roles["accent"]``."""
    accent = roles["accent"]
    tint = _mix(accent, "#ffffff", 0.55)
    line = tint if dark else accent
    ring_r = r - (46 if r >= 100 else 30)
    core_r = ring_r - 24
    k = core_r / 100
    text, size = _seal_ring_text(ring_r)
    circumference = 2 * math.pi * ring_r
    frames = _tesseract_frames()
    values = ";".join((*frames, frames[0]))
    origin = f'style="transform-origin:{cx:.0f}px {cy:.0f}px"'
    ticks = "".join(
        f'<line x1="{cx + (r - 13) * math.cos(a):.1f}" y1="{cy + (r - 13) * math.sin(a):.1f}" '
        f'x2="{cx + (r - 6) * math.cos(a):.1f}" y2="{cy + (r - 6) * math.sin(a):.1f}"/>'
        for a in (2 * math.pi * i / 60 for i in range(60)))

    def layers(path_id: str, classes: tuple[str, str, str]) -> str:
        out = []
        for cls, scale, width, opacity, mirror in zip(classes, (1.0, 0.66, 0.36), (2.6, 2.2, 1.9), (0.95, 0.8, 1.0), ("", " scale(-1 1)", "")):
            layer = (f'<g class="{cls}" {origin}><use href="#{path_id}" stroke-width="{width / (k * scale):.2f}" '
                     f'opacity="{opacity}" transform="translate({cx:.1f} {cy:.1f}) scale({k * scale:.4f}){mirror}"/></g>')
            # Live layers light up in turn, the way the piece goes through production: chip, packing, shipping.
            out.append(f'<g class="m-light" style="animation-delay:{(2 - len(out)) * 3}s">{layer}</g>' if cls else layer)
        return "".join(out)

    return "".join([
        f'<defs><radialGradient id="seal-glow"><stop offset="0" stop-color="{accent}" stop-opacity=".55"/>'
        f'<stop offset="1" stop-color="{accent}" stop-opacity="0"/></radialGradient>'
        f'<path id="tess-rest" d="{frames[0]}" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path id="tess-live" d="{frames[0]}" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">'
        f'<animate attributeName="d" dur="30s" repeatCount="indefinite" calcMode="linear" values="{values}"/></path>'
        f'<path id="seal-ring" d="M{cx - ring_r:.1f},{cy:.1f} a{ring_r:.1f},{ring_r:.1f} 0 1,1 {2 * ring_r:.1f},0 '
        f'a{ring_r:.1f},{ring_r:.1f} 0 1,1 {-2 * ring_r:.1f},0"/></defs>',
        '<g class="m-stamp">',
        f'<circle class="m-glow" cx="{cx}" cy="{cy}" r="{r - 8:.1f}" fill="url(#seal-glow)"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{paper}" stroke="{accent}" stroke-width="6"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{ring_r - 20:.1f}" fill="none" stroke="{accent}" stroke-width="1.5" stroke-opacity=".7"/>',
        f'<g class="m-ticks" {origin} stroke="{accent}" stroke-width="2" stroke-opacity=".75">{ticks}</g>',
        f'<g class="m-ring" {origin}><text font-family="{SANS}" font-size="{size}" font-weight="600" fill="{accent}">'
        f'<textPath href="#seal-ring" textLength="{circumference - 4:.0f}" lengthAdjust="spacing">{_t(text)}</textPath></text></g>',
        # The still frame, shown at rest and by anything that ignores CSS.
        f'<g class="m-tess-static" color="{line}">{layers("tess-rest", ("", "", ""))}</g>',
        # The turning one: hidden by default, shown by the motion rules.
        f'<g class="m-tess-live" color="{line}" display="none">{layers("tess-live", ("m-layer-a", "m-layer-b", "m-layer-c"))}</g>',
        f'<circle class="m-chip" cx="{cx}" cy="{cy}" r="{max(4.0, core_r * 0.08):.1f}" fill="{tint if dark else accent}"/>',
        # The ribbon across the foot of the stamp.
        f'<g transform="rotate(-6 {cx} {cy + r:.0f})"><rect x="{cx - r * 0.62:.1f}" y="{cy + r - r * 0.135:.1f}" width="{r * 1.24:.1f}" '
        f'height="{r * 0.27:.1f}" rx="5" fill="{paper}" stroke="{accent}" stroke-width="3"/>'
        f'<text x="{cx}" y="{cy + r + r * 0.07:.1f}" text-anchor="middle" font-family="{SANS}" font-size="{round(r / 5.2)}" '
        f'font-weight="700" letter-spacing="{round(r / 22)}" fill="{accent}">ORIGINAL</text></g>',
        '</g>',
    ])


def _frame_light(roles: dict[str, str], dark: bool, inset: float = 48) -> str:
    """Two lights running along the frame, always (the pattern repeats every 50% of the length)."""
    accent = roles["accent"]
    core = _mix(accent, "#ffffff", 0.6) if dark else accent
    rect = (f'x="{inset}" y="{inset}" width="{W - 2 * inset}" height="{H - 2 * inset}" pathLength="100" fill="none" '
            f'stroke-linecap="round" stroke-dasharray="9 41" opacity="0"')
    return (f'<rect class="m-flow" {rect} stroke="{accent}" stroke-width="16" stroke-opacity=".22"/>'
            f'<rect class="m-flow" {rect} stroke="{core}" stroke-width="4"/>'
            f'<rect class="m-flow m-flow2" {rect} stroke="{accent}" stroke-width="16" stroke-opacity=".22"/>'
            f'<rect class="m-flow m-flow2" {rect} stroke="{core}" stroke-width="4"/>')


def _motes(seed: int, roles: dict[str, str], dark: bool) -> str:
    """Specks of light drifting up the sheet. Invisible at rest."""
    rng = random.Random(seed * 7919 + 13)
    color = _mix(roles["accent"], "#ffffff", 0.5) if dark else roles["accent"]
    parts = []
    for _ in range(16):
        parts.append(f'<circle class="m-mote" cx="{rng.randint(110, W - 110)}" cy="{rng.randint(H - 360, H - 120)}" '
                     f'r="{rng.choice((2, 2.5, 3, 4))}" fill="{color}" opacity="0" '
                     f'style="animation-duration:{rng.randint(8, 15)}s;animation-delay:-{rng.randint(0, 14)}s"/>')
    return "".join(parts)


def _rise(out: list[str], start: int, step: int) -> None:
    """Wrap what was appended since ``start`` so it rises into place, ``step`` beats after the first."""
    if len(out) > start:
        out[start:] = [f'<g class="m-rise" style="animation-delay:{step * 0.14:.2f}s">{"".join(out[start:])}</g>']


def _holo(name_lines: list[str], y: float, colors: list[str]) -> str:
    """A band of changing colours that glides over the piece's name, clipped to its letters."""
    cs = [_mix(c, "#ffffff", 0.25) for c in colors[:4]]
    cs += cs[: max(0, 3 - len(cs))]
    height = 104 * 1.05 * len(name_lines) + 60
    return (f'<clipPath id="name-clip">{_text_block(name_lines, W / 2, y, 104, 1.05, text_anchor="middle", font_family=SERIF)}</clipPath>'
            f'<linearGradient id="holo"><stop offset="0" stop-color="{cs[0]}" stop-opacity="0"/>'
            f'<stop offset=".28" stop-color="{cs[0]}" stop-opacity=".95"/><stop offset=".48" stop-color="#ffffff" stop-opacity=".98"/>'
            f'<stop offset=".66" stop-color="{cs[1]}" stop-opacity=".95"/><stop offset=".84" stop-color="{cs[2]}" stop-opacity=".9"/>'
            f'<stop offset="1" stop-color="{cs[2]}" stop-opacity="0"/></linearGradient>'
            f'<g clip-path="url(#name-clip)"><g transform="skewX(-18)"><rect class="m-holo" x="-760" y="{y - 100:.0f}" width="620" '
            f'height="{height:.0f}" fill="url(#holo)" opacity="0"/></g></g>')


def _sheen(roles: dict[str, str], dark: bool) -> str:
    """A slow diagonal band of light across the whole sheet. Invisible at rest."""
    color, peak = ("#ffffff", ".16") if dark else (roles["accent"], ".10")
    return (f'<linearGradient id="sheen"><stop offset="0" stop-color="{color}" stop-opacity="0"/>'
            f'<stop offset=".5" stop-color="{color}" stop-opacity="{peak}"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient>'
            f'<g transform="skewX(-20)"><rect class="m-sheen" x="-700" y="0" width="520" height="{H}" fill="url(#sheen)" opacity="0"/></g>')


def _greca(colors: list[str], roles: dict[str, str], rng: random.Random, inset: float) -> str:
    """A step-fret (greca, as in Mitla) running around the sheet."""
    unit = rng.choice((28, 32, 36))
    color = roles["accent"]
    def fret(x: float, y: float, horizontal: bool) -> str:
        u = unit / 4
        pts = [(0, 4), (0, 0), (4, 0), (4, 3), (2, 3), (2, 2)] if horizontal else [(4, 0), (0, 0), (0, 4), (3, 4), (3, 2), (2, 2)]
        return "M" + " L".join(f"{x + px * u:.1f},{y + py * u:.1f}" for px, py in pts)

    # One <path> per fret, each drawing itself with a delay that follows its
    # position, so the frame is traced around the sheet instead of fading in.
    def draw(d: str, progress: float) -> str:
        return (f'<path class="m-greca" pathLength="1" style="animation-delay:{progress * 2.6:.2f}s" d="{d}" '
                f'fill="none" stroke="{color}" stroke-width="5" stroke-linejoin="miter" stroke-linecap="square"/>')

    parts = []
    x = inset
    while x + unit <= W - inset:
        t = (x - inset) / (W - 2 * inset)
        parts.append(draw(fret(x, inset, True), t * 0.35))
        parts.append(draw(fret(x, H - inset - unit, True), 0.65 + (1 - t) * 0.35))
        x += unit
    y = inset + unit
    while y + unit <= H - inset - unit:
        t = (y - inset) / (H - 2 * inset)
        parts.append(draw(fret(W - inset - unit, y, False), 0.35 + t * 0.3))
        parts.append(draw(fret(inset, y, False), 0.65 + (1 - t) * 0.3))
        y += unit
    return "".join(parts)


def _telar(colors: list[str], roles: dict[str, str], rng: random.Random, y: float, h: float) -> str:
    """A woven band like the embroidered bands of a huipil: a row of stepped
    diamonds between two stripes, in the palette's colours. Static: the greca
    template is the one that draws itself."""
    unit = h * 0.9
    x0, x1 = 100.0, W - 100.0
    count = max(1, int((x1 - x0) // unit))
    start = x0 + ((x1 - x0) - count * unit) / 2
    cycle = [roles["accent"], roles["mid"], roles["dark"]] + [c for c in colors if c not in (roles["accent"], roles["mid"], roles["dark"])]
    offset = rng.randrange(len(cycle))
    parts = [f'<rect x="{x0}" y="{y}" width="{x1 - x0}" height="5" fill="{roles["accent"]}"/>',
             f'<rect x="{x0}" y="{y + h - 5}" width="{x1 - x0}" height="5" fill="{roles["accent"]}"/>']
    mid = y + h / 2
    half = (h - 18) / 2
    for i in range(count):
        cx = start + unit * (i + 0.5)
        color = cycle[(i + offset) % len(cycle)]
        parts.append(f'<path d="M{cx:.1f},{mid - half:.1f} L{cx + half:.1f},{mid:.1f} L{cx:.1f},{mid + half:.1f} '
                     f'L{cx - half:.1f},{mid:.1f} Z" fill="{color}"/>')
        parts.append(f'<circle cx="{cx:.1f}" cy="{mid:.1f}" r="{half * 0.28:.1f}" fill="{roles["light"] if color != roles["light"] else roles["dark"]}"/>')
    return "".join(parts)


def _frame(ink: str, accent: str) -> str:
    return (f'<rect x="48" y="48" width="{W - 96}" height="{H - 96}" fill="none" stroke="{ink}" stroke-width="3"/>'
            f'<rect x="66" y="66" width="{W - 132}" height="{H - 132}" fill="none" stroke="{accent}" stroke-width="1.5"/>')


def _amate(roles: dict[str, str], rng: random.Random, ink: str) -> str:
    """Bark paper: short pale fibres over the whole sheet and a hand-torn edge."""
    fibre = roles["mid"]
    parts = []
    for _ in range(170):
        x, y = rng.uniform(75, W - 75), rng.uniform(75, H - 75)
        a = rng.uniform(-0.5, 0.5) + (0 if rng.random() < 0.7 else math.pi / 2)
        n = rng.uniform(40, 130)
        x2 = min(max(x + n * math.cos(a), 75), W - 75)  # fibres stay inside the torn edge
        y2 = min(max(y + n * math.sin(a), 75), H - 75)
        parts.append(f'<line x1="{x:.0f}" y1="{y:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                     f'stroke="{fibre}" stroke-opacity="{rng.uniform(.06, .16):.2f}" stroke-width="{rng.choice((2, 3, 4))}" stroke-linecap="round"/>')
    pts = []
    def jag(x: float, y: float) -> str:
        return f"{x + rng.uniform(-9, 9):.0f},{y + rng.uniform(-9, 9):.0f}"
    for x in range(56, W - 56, 36):
        pts.append(jag(x, 56))
    for y in range(56, H - 56, 36):
        pts.append(jag(W - 56, y))
    for x in range(W - 56, 56, -36):
        pts.append(jag(x, H - 56))
    for y in range(H - 56, 56, -36):
        pts.append(jag(56, y))
    parts.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="{ink}" stroke-width="3" stroke-linejoin="round"/>')
    parts.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="{roles["accent"]}" stroke-width="1.5" '
                 f'stroke-dasharray="2 9" transform="translate(12 12) scale({(W - 24) / W:.4f} {(H - 24) / H:.4f})"/>')
    return "".join(parts)


def _barro(roles: dict[str, str], rng: random.Random, y: float, h: float) -> str:
    """Incised pottery: a zig-zag cut between two lines, with a row of dots."""
    x0, x1 = 100.0, W - 100.0
    step = rng.choice((22, 26, 30))
    mid = y + h / 2
    pts, x, up = [], x0, True
    while x <= x1:
        pts.append(f"{x:.0f},{(y + 10) if up else (y + h - 10):.0f}")
        x += step
        up = not up
    parts = [f'<rect x="{x0}" y="{y}" width="{x1 - x0}" height="4" fill="{roles["mid"]}"/>',
             f'<rect x="{x0}" y="{y + h - 4}" width="{x1 - x0}" height="4" fill="{roles["mid"]}"/>',
             f'<polyline points="{" ".join(pts)}" fill="none" stroke="{roles["accent"]}" stroke-width="5" stroke-linejoin="round"/>']
    xd = x0 + step / 2
    while xd < x1:
        parts.append(f'<circle cx="{xd:.0f}" cy="{mid:.0f}" r="3.2" fill="{roles["dark"]}" fill-opacity=".7"/>')
        xd += step * 2
    return "".join(parts)


def _mitla(roles: dict[str, str], rng: random.Random, y: float, h: float) -> str:
    """Stepped fret panels of Mitla: two interlocking staircases per tile, in two colours."""
    unit = h
    x0, x1 = 100.0, W - 100.0
    count = max(1, int((x1 - x0) // unit))
    start = x0 + ((x1 - x0) - count * unit) / 2
    a, b = roles["accent"], roles["mid"]
    s = unit / 6
    parts = [f'<rect x="{x0}" y="{y}" width="{x1 - x0}" height="3" fill="{roles["dark"]}"/>',
             f'<rect x="{x0}" y="{y + h}" width="{x1 - x0}" height="3" fill="{roles["dark"]}"/>']
    for i in range(count):
        x = start + i * unit
        flip = (i + rng.randrange(2)) % 2
        up = [(0, 6), (0, 4), (2, 4), (2, 2), (4, 2), (4, 0), (6, 0)]
        pts = [(px, 6 - py) if flip else (px, py) for px, py in up]
        parts.append("<path d=\"M" + " L".join(f"{x + px * s:.1f},{y + py * s:.1f}" for px, py in pts) +
                     f'" fill="none" stroke="{a}" stroke-width="{s * .55:.1f}" stroke-linejoin="miter" stroke-linecap="square"/>')
        pts2 = [(6 - px, 6 - py) if flip else (6 - px, py) for px, py in up]
        parts.append("<path d=\"M" + " L".join(f"{x + px * s:.1f},{y + py * s:.1f}" for px, py in pts2) +
                     f'" fill="none" stroke="{b}" stroke-width="{s * .55:.1f}" stroke-linejoin="miter" stroke-linecap="square"/>')
    return "".join(parts)


def _alebrije(colors: list[str], roles: dict[str, str], rng: random.Random, y: float, h: float) -> str:
    """Saturated painted pattern: triangles, dots and stripes cycling through every palette colour."""
    x0, x1 = 100.0, W - 100.0
    unit = h * 0.7
    count = max(1, int((x1 - x0) // unit))
    start = x0 + ((x1 - x0) - count * unit) / 2
    off = rng.randrange(len(colors))
    parts = []
    for i in range(count):
        x = start + i * unit
        c1, c2 = colors[(i + off) % len(colors)], colors[(i + off + 1) % len(colors)]
        if i % 2:
            parts.append(f'<path d="M{x:.1f},{y + h:.1f} L{x + unit / 2:.1f},{y:.1f} L{x + unit:.1f},{y + h:.1f} Z" fill="{c1}"/>')
            parts.append(f'<circle cx="{x + unit / 2:.1f}" cy="{y + h * .66:.1f}" r="{unit * .11:.1f}" fill="{c2}"/>')
        else:
            parts.append(f'<path d="M{x:.1f},{y:.1f} L{x + unit:.1f},{y:.1f} L{x + unit / 2:.1f},{y + h:.1f} Z" fill="{c2}"/>')
            parts.append(f'<circle cx="{x + unit / 2:.1f}" cy="{y + h * .34:.1f}" r="{unit * .11:.1f}" fill="{c1}"/>')
    parts.append(f'<rect x="{x0}" y="{y - 10}" width="{x1 - x0}" height="5" fill="{roles["accent"]}"/>')
    parts.append(f'<rect x="{x0}" y="{y + h + 5}" width="{x1 - x0}" height="5" fill="{roles["accent"]}"/>')
    return "".join(parts)


def _story_lines(story: object) -> list[tuple[str, str]]:
    """(label, value) rows of the "Historia y oficio" block, only when it is on."""
    if not isinstance(story, dict) or not story.get("enabled"):
        return []
    rows = [("OFICIO", story.get("technique")), ("MATERIALES", story.get("materials")), ("LUGAR", story.get("place"))]
    return [(label, str(value).strip()) for label, value in rows if isinstance(value, str) and value.strip()]


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
           f'<style>{_MOTION}</style>',
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
    elif template == "telar":
        out.append(f'<rect x="48" y="48" width="{W - 96}" height="{H - 96}" fill="none" stroke="{ink}" stroke-width="3"/>')
        out.append(f'<rect x="66" y="66" width="{W - 132}" height="{H - 132}" fill="none" stroke="{roles["accent"]}" stroke-width="1.5"/>')
        out.append(_telar(colors, roles, rng, 96, 52))
        out.append(_telar(colors, roles, rng, H - 190, 52))
    elif template == "amate":
        out.append(_amate(roles, rng, ink))
    elif template in ("barro", "mitla", "alebrije"):
        out.append(_frame(ink, roles["accent"]))
        for by in (96, H - 190):
            out.append(_barro(roles, rng, by, 52) if template == "barro" else
                       _mitla(roles, rng, by, 52) if template == "mitla" else
                       _alebrije(colors, roles, rng, by, 52))
    else:
        out.append(_frame(ink, roles["accent"]))
        out.append(_corners(roles["accent"], rng))

    dense = template == "constelacion"
    top = 560 if dense else 330
    if header_art:
        y0 = 110 if dense else (170 if template in _BANDED else 96)
        out.append(_image(art_uri, 300, y0, W - 600, top - 90 - y0))
    out.append(_motes(int(params.get("seed") or 1), roles, variant == "oscuro"))
    out.append(_frame_light(roles, variant == "oscuro"))
    beat = len(out)
    out.append(_band(colors, 300, top - 70, W - 600, 12, ink))
    out.append(f'<text x="{W / 2}" y="{top}" text-anchor="middle" font-family="{SANS}" font-size="26" '
               f'letter-spacing="7" fill="{ink}">{_t((params.get("title") or "Certificado original").upper())}</text>')
    _rise(out, beat, 0)
    name_lines = _lines(params.get("piece_name") or "", 18, 2)
    beat = len(out)
    out.append(_text_block(name_lines, W / 2, top + 130, 104, 1.05, text_anchor="middle",
                           font_family=SERIF, fill=ink))
    out.append(_holo(name_lines, top + 130, colors))
    _rise(out, beat, 1)
    y = top + 130 + 104 * 1.05 * (len(name_lines) - 1) + 80
    beat = len(out)
    out.append(f'<text x="{W / 2}" y="{y:.0f}" text-anchor="middle" font-family="{SERIF}" font-style="italic" '
               f'font-size="40" fill="{ink}">por {_t(params.get("artisan_name"))}</text>')
    _rise(out, beat, 2)

    quote = (params.get("quote") or "").strip()
    below = y + 20  # where the next block may start (baseline of the last line so far)
    if quote:
        q_lines = _lines(f"“{quote}”", 40, 3 if dense else 4)
        beat = len(out)
        out.append(_text_block(q_lines, W / 2, y + 120, 34, 1.4, text_anchor="middle",
                               font_family=SERIF, font_style="italic", fill=roles["mid"] if variant == "claro" else ink))
        below = y + 120 + 34 * 1.4 * (len(q_lines) - 1)
        _rise(out, beat, 3)

    seal_y, seal_r = (H - 470, 106) if dense else (H - 545, 168)
    # "Historia y oficio": a few centred rows between the quote and the seal. Rows
    # that would touch the seal are left out, so a long name or quote never overlaps it.
    story_y, story_gap = below + 70, 40
    beat = len(out)
    for label, value in _story_lines(params.get("story")):
        if story_y > seal_y - seal_r - 28:
            break
        # One plain line per row (label and value in the same text run), so every viewer centres it the same way.
        text = _lines(f"{label} · {value}", 46, 1)[0]
        out.append(f'<text x="{W / 2}" y="{story_y:.0f}" text-anchor="middle" font-family="{SANS}" font-size="26" '
                   f'letter-spacing="1" fill="{ink}">{_t(text)}</text>')
        story_y += story_gap
    _rise(out, beat, 4)
    if art is not None and placement == "sello":
        # The artwork inside the seal's rings, clipped to a circle.
        out.append(f'<clipPath id="art-seal"><circle cx="{W / 2}" cy="{seal_y}" r="{seal_r - 16}"/></clipPath>')
        out.append(f'<circle cx="{W / 2}" cy="{seal_y}" r="{seal_r}" fill="none" stroke="{roles["accent"]}" stroke-width="6"/>')
        side = 2 * (seal_r - 16)
        out.append(_image(art_uri, W / 2 - side / 2, seal_y - side / 2, side, side, extra=' clip-path="url(#art-seal)"'))
    else:
        out.append(_seal_tesseract(W / 2, seal_y, seal_r, roles, paper, variant == "oscuro"))

    # NFC waves leaving the seal: invisible at rest, they only exist in motion.
    for n, extra in enumerate(("", " m-wave2", " m-wave3")):
        out.append(f'<circle class="m-wave{extra}" cx="{W / 2}" cy="{seal_y}" r="{seal_r}" fill="none" '
                   f'stroke="{roles["accent"]}" stroke-width="3" opacity="0"/>')

    facts = [("Código", params.get("public_code") or "")]
    if approved_by:
        facts.append(("Aprobado por", f"{approved_by}{' · ' + approved_on if approved_on else ''}"))
    fy = H - 300
    beat = len(out)
    for label, value in facts:
        out.append(f'<text x="{W / 2 - 20}" y="{fy}" text-anchor="end" font-family="{SANS}" font-size="22" '
                   f'letter-spacing="3" fill="{roles["accent"] if variant == "oscuro" else roles["mid"]}">{_t(label.upper())}</text>')
        out.append(f'<text x="{W / 2 + 20}" y="{fy}" font-family="{SANS}" font-size="26" fill="{ink}">{_t(value)}</text>')
        fy += 44
    out.append(f'<text x="{W / 2}" y="{H - 110}" text-anchor="middle" font-family="{SANS}" font-size="22" '
               f'letter-spacing="4" fill="{ink}" fill-opacity="0.7">ARTESANFC.COM · {_edition_label(params.get("edition"))}</text>')
    _rise(out, beat, 6)

    out.append(_sheen(roles, variant == "oscuro"))
    if watermark:
        out.append(f'<text x="{W / 2}" y="{H / 2}" text-anchor="middle" font-family="{SANS}" font-size="150" '
                   f'font-weight="700" fill="{roles["accent"]}" fill-opacity="0.13" '
                   f'transform="rotate(-30 {W / 2} {H / 2})">{_t(watermark)}</text>')
    out.append("</svg>")
    return "".join(out)
