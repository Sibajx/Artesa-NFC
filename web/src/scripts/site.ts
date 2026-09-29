// ArtesaNFC — site-wide progressive enhancement. Nothing here is required to
// read or navigate a page: without it the header simply stays visible and all
// content stays visible.

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function initHeader(): void {
  const header = document.querySelector<HTMLElement>(".site-header");
  if (!header) return;

  // Hide on scroll down, show on scroll up (ADR-015). Never hidden near the top.
  let lastY = window.scrollY;
  let ticking = false;
  window.addEventListener(
    "scroll",
    () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        const y = window.scrollY;
        const goingDown = y > lastY + 4;
        const goingUp = y < lastY - 4;
        if (y < 80 || goingUp) header.dataset.hidden = "false";
        else if (goingDown) header.dataset.hidden = "true";
        lastY = y;
        ticking = false;
      });
    },
    { passive: true },
  );

  // Pages that open on a dark hero: light-on-dark header while the hero is
  // under it, regular header afterwards.
  const darkZone = document.querySelector("[data-header-dark-zone]");
  if (header.dataset.initialTone === "dark" && darkZone && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      ([entry]) => {
        header.dataset.tone = entry?.isIntersecting ? "dark" : "light";
      },
      { rootMargin: "-64px 0px 0px 0px", threshold: 0 },
    );
    observer.observe(darkZone);
  }
}

function initReveal(): void {
  const targets = document.querySelectorAll<HTMLElement>("[data-reveal]");
  if (targets.length === 0 || reducedMotion.matches || !("IntersectionObserver" in window)) {
    return;
  }
  document.documentElement.classList.add("reveal-ready");
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-revealed");
          observer.unobserve(entry.target);
        }
      }
    },
    { rootMargin: "0px 0px -10% 0px" },
  );
  targets.forEach((target) => observer.observe(target));
}

initHeader();
initReveal();
