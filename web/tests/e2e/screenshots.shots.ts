// Manual visual review helper (not part of the suite: *.manual.ts is not
// matched by Playwright default testMatch; run it by path with --config). Usage:
//   SHOTS=/tmp/shots npx playwright test --config playwright.config.ts tests/e2e/screenshots.shots.ts
import { test } from "@playwright/test";
import { piece } from "../fixtures/contract";
import { mockApi, TOKEN } from "./support";

const OUT = process.env.SHOTS ?? "screenshots";
test.use({ testIdAttribute: "data-x" });

for (const [name, path] of [
  ["home", "/"],
  ["piezas", "/piezas/"],
  ["pieza", `/piezas/${piece.slug}/`],
  ["artesano", "/artesanos/artesano-prueba/"],
  ["certificado", `/c/${TOKEN}`],
  ["pieza-404", "/piezas/no-existe/"],
] as const) {
  test(`shot ${name}`, async ({ page }, info) => {
    await mockApi(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(path);
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}/${name}-${info.project.name}.png`, fullPage: true });
  });
}
