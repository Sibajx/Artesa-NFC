// Generates the PROVISIONAL media used until real, authorised photography and
// video exist. Every output is abstract (material-toned gradients and grain,
// no depicted object, person or place) and carries a burned-in "PROVISIONAL"
// label, so it can never be mistaken for documentary content.
//
// Usage: node scripts/generate-placeholders.mjs
// Needs: sharp (installed with Astro). The optional video step needs an
// ffmpeg binary with the image2pipe demuxer and libvpx (VP8); set FFMPEG=...
// (Playwright's bundled ffmpeg works). Outputs go to public/media/placeholders/.
import { spawnSync } from "node:child_process";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { existsSync } from "node:fs";
import sharp from "sharp";

const OUT = new URL("../public/media/placeholders/", import.meta.url);
await mkdir(OUT, { recursive: true });

function textureSvg({ width, height, from, to, label, seed = 3 }) {
  const fontSize = Math.round(Math.min(width, height) * 0.028);
  return Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="${from}"/>
      <stop offset="1" stop-color="${to}"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.62" cy="0.42" r="0.55">
      <stop offset="0" stop-color="#c58a5a" stop-opacity="0.38"/>
      <stop offset="1" stop-color="#c58a5a" stop-opacity="0"/>
    </radialGradient>
    <filter id="grain" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="${seed}"/>
      <feColorMatrix type="saturate" values="0"/>
      <feComponentTransfer><feFuncA type="linear" slope="0.10"/></feComponentTransfer>
    </filter>
    <filter id="fibers" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="0.004 0.06" numOctaves="3" seed="${seed + 7}"/>
      <feColorMatrix type="saturate" values="0"/>
      <feComponentTransfer><feFuncA type="linear" slope="0.16"/></feComponentTransfer>
    </filter>
  </defs>
  <rect width="100%" height="100%" fill="url(#g)"/>
  <rect width="100%" height="100%" fill="url(#glow)"/>
  <rect width="100%" height="100%" filter="url(#fibers)"/>
  <rect width="100%" height="100%" filter="url(#grain)"/>
  ${
    label
      ? `<text x="${Math.round(width * 0.96)}" y="${Math.round(height - height * 0.05)}" text-anchor="end"
      font-family="DejaVu Sans, Arial, sans-serif" font-size="${fontSize}" font-weight="700"
      letter-spacing="${Math.round(fontSize * 0.18)}" fill="#f7f7f4" fill-opacity="0.72">${label}</text>`
      : ""
  }
</svg>`);
}

async function still(name, { width, height, from, to, label, seed }) {
  const base = sharp(textureSvg({ width, height, from, to, label, seed }));
  const png = await base.png().toBuffer();
  await sharp(png)
    .avif({ quality: 45, effort: 6 })
    .toFile(new URL(`${name}.avif`, OUT).pathname);
  await sharp(png)
    .webp({ quality: 70 })
    .toFile(new URL(`${name}.webp`, OUT).pathname);
  await sharp(png)
    .jpeg({ quality: 72, mozjpeg: true })
    .toFile(new URL(`${name}.jpg`, OUT).pathname);
  console.log(`still ${name} ${width}x${height}`);
}

const CLAY_DARK = { from: "#2a1a12", to: "#0e0c0a" };
const CLAY_LIGHT = { from: "#b88962", to: "#6d4a33" };
const PAPER = { from: "#e9e1d2", to: "#cbbca3" };

// Hero poster: desktop (16:9) and mobile (9:16) framings. No burned-in label:
// with object-fit: cover it collides with the hero text, and Hero.astro always
// renders an HTML "Imagen y video provisionales" label over these assets.
// Other stills keep a burned-in label bottom-right.
await still("hero-poster-desktop", {
  width: 1920,
  height: 1080,
  ...CLAY_DARK,
  label: "",
  seed: 3,
});
await still("hero-poster-mobile", {
  width: 900,
  height: 1600,
  ...CLAY_DARK,
  label: "",
  seed: 3,
});
// Collection entry and generic piece/artisan fallbacks (4:5).
await still("collection-entry", {
  width: 1200,
  height: 1500,
  ...CLAY_LIGHT,
  label: "IMAGEN PROVISIONAL",
  seed: 11,
});
await still("media-fallback", { width: 800, height: 1000, ...PAPER, label: "", seed: 5 });

// Placeholder hero video (no burned-in label, see the hero poster note above):
// slow, seamless ping-pong drift across an abstract
// texture, ~9 s, muted by construction (no audio track), VP8/WebM.
const ffmpeg = process.env.FFMPEG;
if (!ffmpeg || !existsSync(ffmpeg)) {
  console.log("FFMPEG not set: skipping placeholder video.");
} else {
  for (const variant of [
    { name: "hero-desktop", width: 1280, height: 720 },
    { name: "hero-mobile", width: 720, height: 1280 },
  ]) {
    const scale = 1.18;
    const srcW = Math.round(variant.width * scale);
    const srcH = Math.round(variant.height * scale);
    const source = await sharp(
      textureSvg({ width: srcW, height: srcH, ...CLAY_DARK, label: "", seed: 3 }),
    )
      .png()
      .toBuffer();
    const fps = 24;
    const frames = 9 * fps;
    // Frames are concatenated into one MJPEG stream file (ffmpeg builds
    // without the pipe protocol can still read it through image2pipe).
    const stream = join(tmpdir(), `artesanfc-${variant.name}.mjpeg`);
    const chunks = [];
    for (let i = 0; i < frames; i += 1) {
      // Ping-pong with an eased curve: frame 0 and the last frame match.
      const t = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / frames);
      const left = Math.round((srcW - variant.width) * t);
      const top = Math.round((srcH - variant.height) * (0.3 + 0.4 * t));
      chunks.push(
        await sharp(source)
          .extract({ left, top, width: variant.width, height: variant.height })
          .jpeg({ quality: 88 })
          .toBuffer(),
      );
    }
    await writeFile(stream, Buffer.concat(chunks));
    const result = spawnSync(
      ffmpeg,
      [
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "image2pipe",
        "-framerate",
        String(fps),
        "-c:v",
        "mjpeg",
        "-i",
        stream,
        "-an",
        "-c:v",
        "libvpx",
        "-b:v",
        "450k",
        "-crf",
        "30",
        "-auto-alt-ref",
        "0",
        "-pix_fmt",
        "yuv420p",
        new URL(`${variant.name}.webm`, OUT).pathname,
      ],
      { stdio: "inherit" },
    );
    await rm(stream, { force: true });
    if (result.status !== 0) throw new Error(`ffmpeg exited with ${result.status}`);
    console.log(`video ${variant.name} ${variant.width}x${variant.height}`);
  }
}
