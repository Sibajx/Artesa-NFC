import { expect, test } from "@playwright/test";
import { mockApi, reply, TOKEN } from "./support";

// P-026 G3: /autorizacion/#token shows everything that is published about the
// artisan and takes one of three answers.
const OPEN = {
  status: "open",
  full_name: "Rigoberto Ramírez Robles",
  artistic_name: null,
  place: "Cuilápam de Guerrero, Oaxaca",
  biography: "Talla máscaras de zompantle.",
  history: "Empezó en 2007.",
  techniques: ["Tallado en madera", "Pintura y decoración"],
  languages: [],
  public_contact: { telefono: "+52 951 000 0000" },
  portrait: null,
  pieces: [
    { name: "El Negrito", cover: null },
    { name: "El Viejito", cover: null },
  ],
  confirming: false,
  expires_at: "2026-10-19T12:00:00Z",
};

async function open(page: import("@playwright/test").Page, decision: (body: unknown) => unknown) {
  const bodies: unknown[] = [];
  const api = await mockApi(page, {
    "/artisan-authorizations/resolve": reply(200, OPEN),
    "/artisan-authorizations/decision": async (route, request) => {
      const body = request.postDataJSON();
      bodies.push(body);
      await reply(200, decision(body))(route);
    },
  });
  await page.goto(`/autorizacion/#${TOKEN}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("¿nos das permiso?");
  return { api, bodies };
}

test.describe("/autorizacion/#token", () => {
  test("shows the history, contact and pieces, not only the biography", async ({ page }) => {
    await open(page, () => ({ status: "recorded" }));
    await expect(page.getByText("Empezó en 2007.")).toBeVisible();
    await expect(page.getByTestId("auth-contact")).toContainText("+52 951 000 0000");
    await expect(page.getByText("El Negrito")).toBeVisible();
    await expect(page.getByText("Tallado en madera · Pintura y decoración")).toBeVisible();
  });

  test("asking for changes needs a comment and sends it", async ({ page }) => {
    const { bodies } = await open(page, () => ({ status: "recorded" }));
    await page.getByRole("button", { name: "Quiero cambios" }).click();
    await page.getByRole("button", { name: "Enviar cambios" }).click();
    await expect(page.getByRole("alert")).toContainText("Escribe qué te gustaría cambiar");
    expect(bodies).toEqual([]);
    await page.getByRole("textbox").fill("Mi grupo se llama Topos Azteca");
    await page.getByRole("button", { name: "Enviar cambios" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "Gracias, ya recibimos tus cambios.",
    );
    expect(bodies).toEqual([
      { token: TOKEN, decision: "changes", comment: "Mi grupo se llama Topos Azteca" },
    ]);
  });

  test("declining says the profile is taken down", async ({ page }) => {
    const { bodies } = await open(page, () => ({ status: "recorded" }));
    await page.getByRole("button", { name: "No autorizo", exact: true }).click();
    await expect(page.getByText("retiramos de inmediato")).toBeVisible();
    await page.getByRole("button", { name: "No autorizo, retirar mi información" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "Entendido. Ya no aparece en artesanfc.com.",
    );
    expect(bodies).toEqual([{ token: TOKEN, decision: "decline", comment: null }]);
  });

  test("authorizing sends no comment", async ({ page }) => {
    const { bodies } = await open(page, () => ({ status: "recorded" }));
    await page.getByRole("button", { name: "Sí, autorizo" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("¡Gracias! Quedó autorizado.");
    expect(bodies).toEqual([{ token: TOKEN, decision: "authorize", comment: null }]);
  });
});
