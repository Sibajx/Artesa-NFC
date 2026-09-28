// Integration against a REAL local API (no mocks). Opt-in: LIVE_API=1 with the
// backend on http://127.0.0.1:8000 (seeded demo data, CORS allowing the e2e
// origin). See web/README.md, "Validación contra la API real".
import { expect, test } from "@playwright/test";

test.skip(!process.env.LIVE_API, "set LIVE_API=1 with a local seeded backend");

test("list → detail → artisan → back, from the real API", async ({ page }) => {
  await page.goto("/piezas/");
  const cards = page.locator(".piece-card");
  await expect(cards.first()).toBeVisible();
  const count = await cards.count();
  expect(count).toBeGreaterThan(0);
  const first = cards.first().locator("a.piece-card__link");
  const name = (await first.locator("h2").textContent())?.trim() ?? "";
  await first.click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
  await expect(page.locator(".passport")).toContainText("Código público");
  await page.getByRole("link", { name: /Creada por/ }).click();
  await expect(page.locator(".artisan")).toBeVisible();
  await expect(page.getByRole("heading", { level: 3, name })).toBeVisible();
});

test("unknown slug from the real API → no disponible", async ({ page }) => {
  await page.goto("/piezas/no-existe-en-la-base/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Pieza no disponible");
});

test("unknown token from the real API → unavailable (not an error)", async ({ page }) => {
  await page.goto("/c/tokenInexistenteDePrueba_0123456789abcdefghijklmn");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "No podemos confirmar este certificado.",
  );
});

// Tokens issued (and one revoked) on the disposable local database, passed by
// environment; never committed. See web/README.md.
test("real authentic certificate", async ({ page }) => {
  const token = process.env.LIVE_TOKEN;
  test.skip(!token, "set LIVE_TOKEN");
  await page.goto(`/c/${token}`);
  await expect(page.getByRole("heading", { level: 2, name: "Certificado válido" })).toBeVisible();
  expect(await page.content()).not.toContain(token as string);
});

test("real revoked certificate → same public 'unavailable' state", async ({ page }) => {
  const token = process.env.LIVE_REVOKED_TOKEN;
  test.skip(!token, "set LIVE_REVOKED_TOKEN");
  await page.goto(`/c/${token}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "No podemos confirmar este certificado.",
  );
});
