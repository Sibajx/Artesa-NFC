// Pure helpers of the home's collection carousel (kept apart so they are tested without a browser).

/**
 * A link is only followed when it is a page of this site (one slash, then a path) or an https
 * address. Anything else (javascript:, data:, protocol-relative //host, backslashes, spaces)
 * makes the entry fall back to the default link: the API validates too, this is the second lock.
 */
export function safeLink(href: unknown, fallback = "/piezas/"): string {
  if (typeof href !== "string") return fallback;
  const value = href.trim();
  if (/^\/(?!\/)[^\s\\]*$/.test(value) || /^https:\/\/[^\s\\/][^\s\\]*$/.test(value)) return value;
  return fallback;
}

/** The index after moving ``dir`` (+1 or -1) from ``index`` among ``count`` slides, wrapping around. */
export function nextIndex(index: number, dir: number, count: number): number {
  if (count <= 0) return 0;
  return (((index + dir) % count) + count) % count;
}
