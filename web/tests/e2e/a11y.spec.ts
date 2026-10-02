import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { piece } from "../fixtures/contract";
import { mockApi, TOKEN } from "./support";

const pages = [
  ["home", "/", "h1"],
  ["piezas", "/piezas/", ".piece-card"],
  ["pieza", `/piezas/${piece.slug}/`, ".passport"],
  ["artesanos", "/artesanos/", ".artisan-list"],
  ["artesano", `/artesanos/${piece.artisan.slug}/`, ".stats"],
  ["nosotros", "/nosotros/", ".crew-card"],
  ["certificado", `/c/${TOKEN}`, ".passport"],
  ["404", "/no-existe", "h1"],
] as const;

for (const [name, path, ready] of pages) {
  test(`axe: ${name} has no serious or critical violations`, async ({ page }) => {
    await mockApi(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(path);
    await expect(page.locator(ready).first()).toBeVisible();
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const serious = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(
      serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
    ).toEqual([]);
  });
}

test("keyboard: skip link, header navigation and focus-visible", async ({ page, isMobile }) => {
  test.skip(isMobile, "keyboard path checked on desktop");
  await mockApi(page);
  await page.goto("/piezas/");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Saltar al contenido principal" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
  await page.goto("/");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "ArtesaNFC, inicio" })).toBeFocused();
  const outline = await page.evaluate(
    () => getComputedStyle(document.activeElement as Element).outlineStyle,
  );
  expect(outline).not.toBe("none");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Piezas", exact: true }).first()).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/piezas\/$/);
});

test("no horizontal scroll on any page", async ({ page }) => {
  await mockApi(page);
  for (const [, path, ready] of pages) {
    await page.goto(path);
    await expect(page.locator(ready).first()).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
});
