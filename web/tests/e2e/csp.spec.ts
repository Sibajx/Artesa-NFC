import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { piece } from "../fixtures/contract";
import { API_ORIGIN, TOKEN, mockApi } from "./support";

// The Content-Security-Policy (astro.config.mjs, security.csp) must never block
// anything the site does: every page, the certificate view and the 3D viewer
// with a Draco-compressed model, whose decoders are self-hosted (/decoders/).
const DRACO_CUBE = readFileSync(new URL("../fixtures/cube-draco.glb", import.meta.url));

function watchCsp(page: Page): string[] {
  const violations: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) violations.push(m.text());
  });
  return violations;
}

test("no page triggers a CSP violation", async ({ page }) => {
  const violations = watchCsp(page);
  await mockApi(page);
  for (const path of [
    "/",
    "/piezas/",
    `/piezas/${piece.slug}`,
    "/artesanos/",
    `/artesanos/${piece.artisan.slug}`,
    "/nosotros/",
    "/no-existe",
    `/c/${TOKEN}`,
  ]) {
    await page.goto(path, { waitUntil: "networkidle" });
  }
  expect(violations).toEqual([]);
});

test("3D viewer decodes a Draco model with self-hosted decoders under the CSP", async ({
  page,
}) => {
  const violations = watchCsp(page);
  const thirdParty: string[] = [];
  const decoders: string[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.pathname.startsWith("/decoders/")) decoders.push(url.pathname);
    if (
      !["localhost", "127.0.0.1"].includes(url.hostname) &&
      !url.protocol.startsWith("blob") &&
      !url.protocol.startsWith("data")
    )
      thirdParty.push(r.url());
  });
  await mockApi(page);
  await page.route(`${API_ORIGIN}/media/**/*.glb`, (route) =>
    route.fulfill({
      status: 200,
      headers: { "Access-Control-Allow-Origin": "*", "Content-Type": "model/gltf-binary" },
      body: DRACO_CUBE,
    }),
  );
  await page.goto(`/piezas/${piece.slug}`);
  await page
    .getByRole("button", { name: /Ver en 3D/ })
    .first()
    .click();
  await expect(page.locator(".piece-viewer__status").first()).toContainText("Arrastra para girar", {
    timeout: 30_000,
  });
  expect(decoders.some((p) => p.startsWith("/decoders/draco/"))).toBe(true);
  expect(thirdParty).toEqual([]);
  expect(violations).toEqual([]);
});
