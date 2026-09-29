import { expect, test } from "@playwright/test";
import { piece, pieceWithout3d } from "../fixtures/contract";
import { mockApi, reply } from "./support";

test.describe("/piezas", () => {
  test("shows photographs first and marks pieces with 3D, without loading any 3D", async ({
    page,
  }) => {
    const heavy: string[] = [];
    page.on("request", (r) => {
      if (/model-viewer|\.glb/.test(r.url())) heavy.push(r.url());
    });
    await mockApi(page);
    await page.goto("/piezas/");
    await expect(page.getByRole("heading", { level: 1, name: "Piezas" })).toBeVisible();
    const cards = page.locator(".piece-card");
    await expect(cards).toHaveCount(2);
    await expect(cards.first().getByRole("heading", { name: piece.name })).toBeVisible();
    await expect(page.getByRole("button", { name: `Ver en 3D: ${piece.name}` })).toBeVisible();
    await expect(
      page.getByRole("button", { name: `Ver en 3D: ${pieceWithout3d.name}` }),
    ).toHaveCount(0);
    // The 404 image fell back to the neutral surface; the card still works.
    await expect(cards.first().locator("[data-media-fallback] img")).toBeVisible();
    expect(heavy).toEqual([]);
  });

  test("opens the viewer on demand in a dialog and returns focus", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/piezas/");
    const open = page.getByRole("button", { name: `Ver en 3D: ${piece.name}` });
    await open.click();
    const dialog = page.getByRole("dialog", { name: piece.name });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator(".piece-viewer__status")).toContainText("Arrastra para girar", {
      timeout: 20_000,
    });
    expect(api.requests).toContain(`MEDIA /media/test/pieces/mascara-prueba/model.glb`);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(open).toBeFocused();
  });

  test("empty list", async ({ page }) => {
    await mockApi(page, { "/pieces": reply(200, { data: [], meta: { total: 0 } }) });
    await page.goto("/piezas/");
    await expect(page.getByText("Todavía no hay piezas publicadas.")).toBeVisible();
  });

  test("API error then retry", async ({ page }) => {
    let calls = 0;
    await mockApi(page, {
      "/pieces": (route) => {
        calls += 1;
        return calls === 1
          ? reply(503, { error: { code: "x", message: "y" } })(route)
          : reply(200, { data: [], meta: {} })(route);
      },
    });
    await page.goto("/piezas/");
    await expect(
      page.locator('[data-state="unavailable"][data-reason="server_error"]'),
    ).toBeVisible();
    await page.getByRole("button", { name: "Reintentar" }).click();
    await expect(page.getByText("Todavía no hay piezas publicadas.")).toBeVisible();
  });

  test("API down (network)", async ({ page }) => {
    await mockApi(page, { "/pieces": (route) => route.abort("connectionrefused") });
    await page.goto("/piezas/");
    await expect(page.locator('[data-state="unavailable"][data-reason="network"]')).toBeVisible();
  });

  test("rate limited", async ({ page }) => {
    await mockApi(page, {
      "/pieces": reply(429, { error: { code: "rate_limited", message: "x" } }),
    });
    await page.goto("/piezas/");
    await expect(page.getByText("Recibimos demasiadas solicitudes seguidas.")).toBeVisible();
  });
});

test.describe("/piezas/{slug}", () => {
  test("renders the piece and its public passport from the API", async ({ page }) => {
    await mockApi(page);
    await page.goto(`/piezas/${piece.slug}/`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(piece.name);
    await expect(page).toHaveTitle(`${piece.name} — ArtesaNFC`);
    await expect(page.locator('meta[name="robots"]')).toHaveCount(0);
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
      "href",
      /\/piezas\/mascara-prueba\/$/,
    );
    const passport = page.locator(".passport");
    await expect(passport).toContainText(piece.public_code);
    await expect(passport).toContainText("Pieza registrada en el catálogo público");
    await expect(passport.locator('[data-row="community"]')).toContainText("Localidad de prueba");
    await expect(passport.locator('[data-row="materials"]')).toContainText(
      "madera de prueba, pigmento de prueba",
    );
    await expect(passport.locator('[data-row="dimensions"]')).toContainText(
      "30 cm alto × 20 cm ancho × 15 cm fondo",
    );
    // The public page never claims or hints at a certificate (API_CONTRACT.md §5).
    await expect(passport).not.toContainText("Certificado válido");
    await expect(page.getByRole("link", { name: /Creada por Artesano de Prueba/ })).toHaveAttribute(
      "href",
      "/artesanos/artesano-prueba/",
    );
    await expect(
      page.getByRole("heading", { name: "Otras piezas de Artesano de Prueba" }),
    ).toBeVisible();
    await expect(page.getByRole("heading", { level: 3, name: pieceWithout3d.name })).toBeVisible();
  });

  test("3D loads only on request, by keyboard, and never blocks the page", async ({ page }) => {
    const heavy: string[] = [];
    page.on("request", (r) => {
      if (/\.glb/.test(r.url())) heavy.push(r.url());
    });
    await mockApi(page);
    await page.goto(`/piezas/${piece.slug}/`);
    const launch = page.getByRole("button", { name: /Ver en 3D · 4.2 MB/ });
    await expect(launch).toBeVisible();
    await expect(page.getByRole("link", { name: /Creada por/ })).toBeVisible();
    expect(heavy).toEqual([]);
    await launch.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator(".piece-viewer__status")).toContainText("Arrastra para girar", {
      timeout: 20_000,
    });
    expect(heavy.length).toBe(1);
    await expect(page.locator("model-viewer")).toHaveAttribute("touch-action", "pan-y");
    expect(await page.locator("model-viewer").getAttribute("auto-rotate")).toBeNull();
    await page.getByRole("button", { name: "Restablecer vista" }).click();
  });

  test("a broken model falls back to the photograph", async ({ page }) => {
    await mockApi(page);
    await page.route("http://127.0.0.1:8000/media/**/*.glb", (route) =>
      route.fulfill({ status: 404, body: "" }),
    );
    await page.goto(`/piezas/${piece.slug}/`);
    await page.getByRole("button", { name: /Ver en 3D/ }).click();
    await expect(page.locator(".piece-viewer__status")).toContainText(
      "No se pudo cargar el modelo 3D",
      { timeout: 20_000 },
    );
    await expect(page.locator(".piece-viewer__stage img")).toBeVisible();
    await expect(page.getByRole("button", { name: "Reintentar 3D" })).toBeVisible();
  });

  test("unknown slug → 'no disponible' + noindex", async ({ page }) => {
    await mockApi(page);
    await page.goto("/piezas/no-existe/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Pieza no disponible");
    await expect(page.getByRole("heading", { level: 1 })).toBeFocused();
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
  });

  test("service down → retryable, distinct from 'not found'", async ({ page }) => {
    await mockApi(page, {
      [`/pieces/${piece.slug}`]: reply(500, { error: { code: "internal_error", message: "x" } }),
    });
    await page.goto(`/piezas/${piece.slug}`);
    await expect(page.locator('[data-state="unavailable"]')).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("No pudimos cargar la pieza.");
    await expect(page.getByRole("button", { name: "Reintentar" })).toBeVisible();
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
  });

  test("a request that never answers times out", async ({ page }) => {
    test.setTimeout(20_000);
    await mockApi(page, { [`/pieces/${piece.slug}`]: () => new Promise<void>(() => undefined) });
    await page.goto(`/piezas/${piece.slug}/`);
    await expect(page.locator('[data-state="unavailable"][data-reason="timeout"]')).toBeVisible({
      timeout: 12_000,
    });
  });

  test("nested and unknown paths answer 404", async ({ page }) => {
    for (const path of ["/piezas/a/b/", "/no-existe"]) {
      const response = await page.goto(path);
      expect(response?.status()).toBe(404);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("Esta página no existe.");
    }
  });
});

test.describe("detail without JavaScript", () => {
  test.use({ javaScriptEnabled: false });
  // Chromium's script-disabled emulation does not render <noscript>, so the
  // notice is checked in the served HTML and the page is checked for leaks.
  test("serves only a neutral notice and no entity content", async ({ page, request }) => {
    const html = await (await request.get(`/piezas/${piece.slug}/`)).text();
    expect(html).toMatch(
      /<noscript>[\s\S]*Esta página necesita JavaScript para mostrar la pieza\.[\s\S]*<\/noscript>/,
    );
    expect(html).not.toContain(piece.slug);
    await page.goto(`/piezas/${piece.slug}/`);
    await expect(page.locator("main")).not.toContainText(piece.name);
  });
});
