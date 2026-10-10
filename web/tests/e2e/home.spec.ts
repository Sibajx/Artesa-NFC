import { expect, test } from "@playwright/test";
import { mockApi, reply } from "./support";

test.describe("home", () => {
  test("has at most four blocks and only asks for the hero campaign and the site images", async ({
    page,
  }) => {
    const api = await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "Cada pieza guarda quién la hizo.",
    );
    await expect(page.locator("main > section")).toHaveCount(4);
    await expect(page.locator('[data-provisional="hero"]')).toBeVisible();
    await expect(page.getByRole("link", { name: "Explorar piezas" })).toHaveAttribute(
      "href",
      "/piezas/",
    );
    await page.waitForTimeout(500);
    // The only requests are the hero's (P-028) and the site images' (P-029);
    // both answer 404 here, so the built-in hero and image stay.
    expect(api.requests.filter((r) => r !== "GET /hero" && r !== "GET /site-images")).toEqual([]);
    await expect(page.locator('[data-provisional="hero"]')).toBeVisible();
    await expect(page.locator(".collection__media .placeholder-note")).toBeVisible();
  });

  test("shows the season's poster and video when Gestión has one for today", async ({ page }) => {
    const sources: string[] = [];
    page.on("request", (r) => {
      if (/\/media\/hero\//.test(r.url())) sources.push(new URL(r.url()).pathname);
    });
    await mockApi(page, {
      "/hero": reply(200, {
        data: {
          slug: "dia-de-muertos",
          name: "Día de Muertos",
          reason: "date",
          video: {
            mp4: "/media/hero/dia-de-muertos/ab12.mp4",
            webm: "/media/hero/dia-de-muertos/ab12.webm",
          },
          poster: "/media/hero/dia-de-muertos/ab12.jpg",
        },
      }),
    });
    await page.goto("/");
    await expect(page.locator(".hero__poster img")).toHaveAttribute(
      "src",
      /\/media\/hero\/dia-de-muertos\/ab12\.jpg$/,
    );
    await expect(page.locator('[data-provisional="hero"]')).toHaveCount(0);
    // The video asks for the season's file (the mock answers 404, so it
    // is then dropped and the poster stays).
    await expect
      .poll(() => sources.some((p) => p.endsWith("/ab12.webm") || p.endsWith("/ab12.mp4")))
      .toBe(true);
  });

  test("replaces the collection photo when Gestión has one, and drops the provisional label", async ({
    page,
  }) => {
    await mockApi(page, {
      "/site-images": reply(200, {
        data: {
          "collection-entry": {
            avif: "/media/sitio/collection-entry/ab12.avif",
            webp: "/media/sitio/collection-entry/ab12.webp",
            jpg: "/media/sitio/collection-entry/ab12.jpg",
            width: 1200,
            height: 1500,
          },
        },
      }),
    });
    // A 1x1 PNG answers every format; the browser sniffs the bytes.
    const png = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
      "base64",
    );
    await page.route("http://127.0.0.1:8000/media/sitio/**", (route) =>
      route.fulfill({
        status: 200,
        headers: { "Access-Control-Allow-Origin": "*" },
        contentType: "image/png",
        body: png,
      }),
    );
    await page.goto("/");
    const image = page.locator(".collection__media img");
    await expect(image).toHaveAttribute("src", /\/media\/sitio\/collection-entry\/ab12\.jpg$/);
    await expect(page.locator(".collection__media .placeholder-note")).toHaveCount(0);
  });

  test("keeps the provisional photo when the replacement file is missing", async ({ page }) => {
    await mockApi(page, {
      "/site-images": reply(200, {
        data: {
          "collection-entry": {
            avif: "/media/sitio/collection-entry/gone.avif",
            webp: "/media/sitio/collection-entry/gone.webp",
            jpg: "/media/sitio/collection-entry/gone.jpg",
            width: 1200,
            height: 1500,
          },
        },
      }),
    });
    await page.goto("/");
    await page.waitForTimeout(800); // the mock answers 404 for the media
    await expect(page.locator(".collection__media .placeholder-note")).toBeVisible();
  });

  test("plays the muted hero video when motion is allowed, with a pause control", async ({
    page,
  }) => {
    await page.goto("/");
    const video = page.locator("[data-hero-video]");
    await expect(video).toHaveClass(/is-playing/, { timeout: 10_000 });
    expect(await video.evaluate((v: HTMLVideoElement) => v.muted && v.loop && v.playsInline)).toBe(
      true,
    );
    const toggle = page.getByRole("button", { name: "Pausar video" });
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Reproducir video" })).toBeVisible();
    expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  });

  test("never loads the video with prefers-reduced-motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    const videoRequests: string[] = [];
    page.on("request", (r) => {
      if (r.url().endsWith(".webm")) videoRequests.push(r.url());
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.waitForTimeout(1000);
    expect(await page.locator("[data-hero-video] source").count()).toBe(0);
    expect(videoRequests).toEqual([]);
    await expect(page.locator(".hero__poster img")).toBeVisible();
    // Reveal animations are off: below-the-fold content is already visible.
    await expect(page.locator("html")).not.toHaveClass(/reveal-ready/);
  });

  test("never loads the video with Save-Data", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "connection", {
        value: { saveData: true, effectiveType: "4g" },
      });
    });
    await page.goto("/");
    await page.waitForTimeout(800);
    expect(await page.locator("[data-hero-video] source").count()).toBe(0);
  });
});

test.describe("home without JavaScript", () => {
  test.use({ javaScriptEnabled: false });

  test("communicates fully", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "Cada pieza guarda quién la hizo.",
    );
    for (const name of [
      "La artesanía, primero.",
      "Piezas con nombre propio.",
      "Para artesanos, coleccionistas y proyectos culturales.",
    ]) {
      await expect(page.getByRole("heading", { name })).toBeVisible();
    }
    await expect(page.locator(".hero__poster img")).toBeVisible();
  });
});
