import { expect, test } from "@playwright/test";
import { mockApi } from "./support";

test.describe("home", () => {
  test("has at most four blocks and makes no API request", async ({ page }) => {
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
    expect(api.requests).toEqual([]);
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
