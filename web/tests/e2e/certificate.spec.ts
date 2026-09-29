import { expect, test } from "@playwright/test";
import { piece } from "../fixtures/contract";
import { mockApi, reply, TOKEN } from "./support";

test.describe("/c/{token}", () => {
  test("authentic: valid certificate, token never exposed", async ({ page }) => {
    const urls: string[] = [];
    const referers: string[] = [];
    page.on("request", (r) => {
      urls.push(r.url());
      const ref = r.headers()["referer"];
      if (ref) referers.push(ref);
    });
    const api = await mockApi(page);
    const response = await page.goto(`/c/${TOKEN}`);
    expect(response?.headers()["cache-control"]).toBe("no-store");
    // Pages joins the global and /c/* values; browsers apply the last valid
    // token, so the effective policy must be no-referrer.
    expect(response?.headers()["referrer-policy"]?.split(",").pop()?.trim()).toBe("no-referrer");
    expect(response?.headers()["x-robots-tag"]).toContain("noindex");
    await expect(page.getByRole("heading", { level: 2, name: "Certificado válido" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(piece.name);
    await expect(page.locator(".passport")).toContainText("Nota de prueba.");
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
    await expect(page.locator('meta[name="referrer"]')).toHaveAttribute("content", "no-referrer");
    const html = await page.content();
    expect(html).not.toContain(TOKEN);
    expect(urls.filter((u) => !u.includes("/c/")).some((u) => u.includes(TOKEN))).toBe(false);
    expect(referers.some((r) => r.includes(TOKEN))).toBe(false);
    expect(api.requests.filter((r) => r.startsWith("POST"))).toEqual([
      "POST /certificates/resolve",
    ]);
    await expect(page.getByRole("link", { name: "Ver la pieza" })).toHaveAttribute(
      "href",
      "/piezas/mascara-prueba/",
    );
  });

  test("unavailable (unknown/revoked/malformed are indistinguishable)", async ({ page }) => {
    await mockApi(page, {
      "/certificates/resolve": reply(200, { authenticity: { status: "unavailable" } }),
    });
    await page.goto(`/c/${TOKEN}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "No podemos confirmar este certificado.",
    );
    await expect(page.locator("body")).not.toContainText(/revocad|falsa|falso/i);
  });

  test("service error is not presented as 'unavailable'", async ({ page }) => {
    let calls = 0;
    await mockApi(page, {
      "/certificates/resolve": (route) => {
        calls += 1;
        return calls === 1
          ? reply(502, {})(route)
          : reply(200, { authenticity: { status: "unavailable" } })(route);
      },
    });
    await page.goto(`/c/${TOKEN}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "Verificación no disponible por ahora.",
    );
    await page.getByRole("button", { name: "Reintentar" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "No podemos confirmar este certificado.",
    );
  });

  test("malformed route makes no API call", async ({ page }) => {
    const api = await mockApi(page);
    await page.goto("/c/abc/def");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "No podemos confirmar este certificado.",
    );
    expect(api.requests).toEqual([]);
  });
});
