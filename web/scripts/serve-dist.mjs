// Production-like static server for web/dist (Node stdlib only), used by the
// e2e suite and Lighthouse. It applies the Cloudflare Pages subset this site
// relies on, read from the REAL dist/_redirects and dist/_headers:
//
//   * _redirects: 200 rewrites / 3xx redirects, first match wins, `*` splats
//     and `:name` placeholders (one non-empty segment). A 200 rewrite applies
//     even when a file exists at the requested path (Wrangler 4.135.0,
//     docs/QA_PRIVATE_ROUTE.md §5).
//   * _headers: every matching block applies; a header set by several blocks
//     is joined with ", " (as Wrangler does).
//   * HTML: /dir -> 308 /dir/; /dir/ -> dir/index.html; /x.html also served
//     at /x; unknown path -> /404.html with status 404 (Pages' behaviour when
//     a top-level 404.html exists; no SPA fallback).
//
// It is NOT a Cloudflare emulator; `npx wrangler pages dev dist` is the
// higher-fidelity cross-check. Request paths (they may carry the /c/ bearer
// token) are never logged.
//
// Usage: node scripts/serve-dist.mjs [--root dist] [--port 4321] [--host 127.0.0.1]
import { createServer } from "node:http";
import { readFileSync, statSync } from "node:fs";
import { extname, join, normalize, resolve, sep } from "node:path";
import { parseArgs } from "node:util";

const { values } = parseArgs({
  options: {
    root: { type: "string", default: "dist" },
    port: { type: "string", default: "4321" },
    host: { type: "string", default: "127.0.0.1" },
  },
});
const ROOT = resolve(values.root);

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".avif": "image/avif",
  ".png": "image/png",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".webm": "video/webm",
  ".mp4": "video/mp4",
  ".glb": "model/gltf-binary",
  ".txt": "text/plain; charset=utf-8",
};
const RESERVED = new Set(["/_headers", "/_redirects"]);

function patternToRegex(pattern) {
  let source = "";
  for (const part of pattern.split(/(\*|:[A-Za-z_]\w*)/)) {
    if (part === "*") source += "(?<splat>.*)";
    else if (part.startsWith(":")) source += `(?<${part.slice(1)}>[^/]+)`;
    else source += part.replace(/[.+?^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${source}$`);
}

function loadRedirects() {
  return readFileSync(join(ROOT, "_redirects"), "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => {
      const [from, to, status = "302"] = line.split(/\s+/);
      return { regex: patternToRegex(from), to, status: Number(status) };
    });
}

function loadHeaders() {
  const blocks = [];
  let current = null;
  for (const raw of readFileSync(join(ROOT, "_headers"), "utf8").split("\n")) {
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    if (!/^\s/.test(raw)) {
      current = { regex: patternToRegex(raw.trim()), headers: [] };
      blocks.push(current);
    } else if (current) {
      const i = raw.indexOf(":");
      current.headers.push([raw.slice(0, i).trim(), raw.slice(i + 1).trim()]);
    }
  }
  return blocks;
}

const redirects = loadRedirects();
const headerBlocks = loadHeaders();

function fileFor(pathname) {
  const safe = normalize(pathname).replace(/^(\.\.[/\\])+/, "");
  const full = join(ROOT, safe);
  if (!full.startsWith(ROOT + sep) && full !== ROOT) return null;
  try {
    const stat = statSync(full);
    if (stat.isFile()) return full;
    if (stat.isDirectory()) {
      const index = join(full, "index.html");
      if (statSync(index).isFile())
        return pathname.endsWith("/") ? index : { redirect: `${pathname}/` };
    }
  } catch {
    // fall through
  }
  if (!extname(pathname)) {
    try {
      const html = `${full}.html`;
      if (statSync(html).isFile()) return html;
    } catch {
      // fall through
    }
  }
  return null;
}

createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  let pathname;
  try {
    pathname = decodeURIComponent(url.pathname);
  } catch {
    pathname = url.pathname;
  }
  const headers = {};
  for (const block of headerBlocks) {
    if (block.regex.test(url.pathname))
      for (const [k, v] of block.headers) headers[k] = headers[k] ? `${headers[k]}, ${v}` : v;
  }

  let target = pathname;
  let status = 200;
  for (const rule of redirects) {
    const match = rule.regex.exec(url.pathname);
    if (!match) continue;
    let to = rule.to;
    for (const [name, value] of Object.entries(match.groups ?? {})) {
      to = to.replace(name === "splat" ? ":splat" : `:${name}`, value);
    }
    if (rule.status === 200) {
      target = to;
    } else {
      res.writeHead(rule.status, { ...headers, Location: to });
      res.end();
      return;
    }
    break;
  }

  let file = RESERVED.has(target) ? null : fileFor(target);
  if (file && typeof file === "object") {
    res.writeHead(308, { ...headers, Location: file.redirect + url.search });
    res.end();
    return;
  }
  if (!file) {
    file = join(ROOT, "404.html");
    status = 404;
  }
  const body = readFileSync(file);
  res.writeHead(status, {
    "Content-Type": TYPES[extname(file)] ?? "application/octet-stream",
    ...headers,
  });
  res.end(req.method === "HEAD" ? undefined : body);
}).listen(Number(values.port), values.host, () => {
  console.log(`serving ${ROOT} on http://${values.host}:${values.port}`);
});
