// ArtesaNFC — client-side metadata for the neutral shells (F-08). The shell
// is served for many URLs, so title/description/canonical are only set after
// a valid 200; every other state is marked noindex.

export function setIndexable(indexable: boolean): void {
  let meta = document.querySelector<HTMLMetaElement>('meta[name="robots"]');
  if (indexable) {
    meta?.remove();
    return;
  }
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "robots";
    document.head.appendChild(meta);
  }
  meta.content = "noindex";
}

export function setMetadata(
  title: string,
  description: string | null,
  canonicalPath: string,
): void {
  document.title = `${title} — ArtesaNFC`;
  if (description) {
    document.querySelector('meta[name="description"]')?.setAttribute("content", description);
  }
  let link = document.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (!link) {
    link = document.createElement("link");
    link.rel = "canonical";
    document.head.appendChild(link);
  }
  link.href = window.location.origin + canonicalPath;
}
