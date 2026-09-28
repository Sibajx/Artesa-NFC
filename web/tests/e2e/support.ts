import type { Page, Request, Route } from "@playwright/test";
import { artisan, certificate, piece, pieceWithout3d, summary } from "../fixtures/contract";
import { makeCubeGlb } from "../fixtures/glb";

export const API = "http://127.0.0.1:8000/api/v1";
export const API_ORIGIN = "http://127.0.0.1:8000";
export const TOKEN = "tok_PRIVATE-e2e-abcdefghijklmnopqrstuvwxyz0123";

type Handler = (route: Route, request: Request) => Promise<void> | void;

const cors = { "Access-Control-Allow-Origin": "*", "Content-Type": "application/json" };
export const reply = (status: number, body: unknown) => (route: Route) =>
  route.fulfill({ status, headers: cors, body: JSON.stringify(body) });

export interface MockApi {
  requests: string[];
}

// Installs contract-shaped API responses. `overrides` maps an API path
// ("/pieces", "/pieces/mascara-prueba", "/certificates/resolve", ...) to a
// handler; everything else under /api/v1 answers 404.
export async function mockApi(
  page: Page,
  overrides: Record<string, Handler> = {},
): Promise<MockApi> {
  const state: MockApi = { requests: [] };
  const defaults: Record<string, Handler> = {
    "/pieces": reply(200, { data: [summary(piece), summary(pieceWithout3d)], meta: { total: 2 } }),
    [`/pieces/${piece.slug}`]: reply(200, piece),
    [`/pieces/${pieceWithout3d.slug}`]: reply(200, pieceWithout3d),
    "/artisans": reply(200, {
      data: [{ slug: artisan.slug, full_name: artisan.full_name, artistic_name: null }],
      meta: { total: 1 },
    }),
    [`/artisans/${artisan.slug}`]: reply(200, artisan),
    "/certificates/resolve": reply(200, certificate),
  };
  const handlers = { ...defaults, ...overrides };
  await page.route(`${API}/**`, async (route, request) => {
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    state.requests.push(`${request.method()} ${path}`);
    if (request.method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          ...cors,
          "Access-Control-Allow-Methods": "GET, POST",
          "Access-Control-Allow-Headers": "Content-Type",
        },
      });
      return;
    }
    const handler = handlers[path];
    if (handler) await handler(route, request);
    else
      await reply(404, {
        error: { code: "not_found", message: "The requested resource does not exist." },
      })(route);
  });
  // Media: API-origin /media/* is not served today (API_CONTRACT.md §6.1);
  // images 404 (exercises the fallback) and the model gets the test cube.
  await page.route(`${API_ORIGIN}/media/**`, async (route, request) => {
    state.requests.push(`MEDIA ${new URL(request.url()).pathname}`);
    if (request.url().endsWith(".glb")) {
      await route.fulfill({
        status: 200,
        headers: { "Access-Control-Allow-Origin": "*", "Content-Type": "model/gltf-binary" },
        body: makeCubeGlb(),
      });
    } else {
      await route.fulfill({ status: 404, body: "" });
    }
  });
  return state;
}
