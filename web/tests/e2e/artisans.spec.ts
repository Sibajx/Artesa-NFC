import { expect, test } from "@playwright/test";
import { artisan, piece } from "../fixtures/contract";
import { mockApi } from "./support";

test("/artesanos lists artisans", async ({ page }) => {
  await mockApi(page);
  await page.goto("/artesanos/");
  await page.getByRole("link", { name: /Artesano de Prueba/ }).click();
  await expect(page).toHaveURL(/\/artesanos\/artesano-prueba\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(artisan.full_name);
  await expect(page.getByText("Localidad de prueba, Municipio de prueba, Oaxaca")).toBeVisible();
  await expect(page.getByRole("heading", { level: 3, name: piece.name })).toBeVisible();
});

test("/artesanos/{slug} unknown → no disponible", async ({ page }) => {
  await mockApi(page);
  await page.goto("/artesanos/nadie");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Perfil no disponible");
});
