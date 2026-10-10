import { describe, expect, it } from "vitest";
import { createApiClient } from "../../src/lib/api";
import { apiConfigFor, resolveApiBase, resolveMediaUrl } from "../../src/lib/api-config";
import {
  formatBytes,
  formatCreation,
  formatDimensions,
  formatPlace,
  availabilityLabel,
} from "../../src/lib/format";
import { imagesWithRoles, pickHeroImage, pickModel } from "../../src/lib/media";
import { ARTISAN_PREFIX, PIECE_PREFIX, slugFromPath, tokenFromPath } from "../../src/lib/routes";
import { heroImage, model3d, piece } from "../fixtures/contract";

describe("api-config (same allowlist as frontend/assets/js/api-config.js)", () => {
  it.each([
    ["localhost", "http://127.0.0.1:8000/api/v1"],
    ["127.0.0.1", "http://127.0.0.1:8000/api/v1"],
    ["artesanfc.com", "https://api.artesanfc.com/api/v1"],
    ["ARTESANFC.COM", "https://api.artesanfc.com/api/v1"],
    ["staging.artesanfc.com", "https://api.artesanfc.com/api/v1"],
  ])("%s → %s", (host, base) => expect(resolveApiBase(host)).toBe(base));

  it.each([
    "www.artesanfc.com",
    "artesanfc.pages.dev",
    "artesanfc.com.evil.example",
    "",
    "[::1]",
    "constructor",
    "__proto__",
  ])("%s is unresolved", (host) => expect(resolveApiBase(host)).toBeNull());

  it("resolves media against the API origin only", () => {
    const prod = apiConfigFor("artesanfc.com");
    expect(resolveMediaUrl("/media/a.jpg", prod)).toBe("https://api.artesanfc.com/media/a.jpg");
    expect(resolveMediaUrl("https://cdn.example/a.jpg", prod)).toBe("https://cdn.example/a.jpg");
    expect(resolveMediaUrl("javascript:alert(1)", prod)).toBeNull();
    expect(resolveMediaUrl("//evil.example/a.jpg", prod)).toBeNull();
    expect(resolveMediaUrl("/media/a.jpg", apiConfigFor("x.pages.dev"))).toBeNull();
  });
});

describe("routes", () => {
  it.each([
    ["/piezas/mascara-01", "mascara-01"],
    ["/piezas/mascara-01/", "mascara-01"],
    ["/piezas/m%C3%A1scara", "máscara"],
    ["/piezas/", null],
    ["/piezas/a/b", null],
    ["/piezas/a%2Fb", null],
    ["/piezas/%E0%A4%A", null],
    ["/artesanos/x", null],
  ])("slug of %s", (path, slug) => expect(slugFromPath(path, PIECE_PREFIX)).toBe(slug));

  it("reads artisan slugs", () =>
    expect(slugFromPath("/artesanos/ana/", ARTISAN_PREFIX)).toBe("ana"));

  it.each([
    ["/c/abcDEF_123-x", "abcDEF_123-x"],
    ["/c/", null],
    ["/c/abc/", null],
    ["/c/abc/def", null],
    ["/c/abc%20def", null],
    ["/c/" + "a".repeat(257), null],
    ["/x/abc", null],
  ])("token of %s", (path, token) => expect(tokenFromPath(path)).toBe(token));
});

describe("format", () => {
  it("formats dimensions without inventing missing ones", () => {
    expect(formatDimensions({ height: 30, width: 20.5, depth: null, unit: "cm" })).toBe(
      "30 cm alto × 20.5 cm ancho",
    );
    expect(formatDimensions({ height: null, width: null, depth: null, unit: "cm" })).toBeNull();
    expect(formatDimensions(null)).toBeNull();
  });

  it("prefers the full date and keeps calendar dates in UTC", () => {
    expect(formatCreation({ creation_date: "2024-03-01", creation_year: 2024 })).toBe(
      "1 de marzo de 2024",
    );
    expect(formatCreation({ creation_date: null, creation_year: 2024 })).toBe("2024");
    expect(formatCreation({ creation_date: null, creation_year: null })).toBeNull();
  });

  it("builds community / municipality / region", () => {
    expect(
      formatPlace({ locality: "A", municipality: "A", state: "Oaxaca", country: "México" }),
    ).toBe("A, Oaxaca");
    expect(formatPlace({ locality: "A", municipality: "B", state: null, country: null })).toBe(
      "A, B",
    );
    expect(
      formatPlace({ locality: null, municipality: null, state: null, country: null }),
    ).toBeNull();
  });

  it("labels known availability values and ignores unknown ones", () => {
    expect(availabilityLabel("reserved")).toBe("Reservada");
    expect(availabilityLabel("future_value")).toBeNull();
  });

  it("formats sizes", () => {
    expect(formatBytes(4_404_019)).toBe("4.2 MB");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(null)).toBeNull();
  });
});

describe("media", () => {
  it("picks hero and model by type/role and position", () => {
    expect(pickHeroImage(piece.media)).toBe(heroImage);
    expect(pickModel(piece.media)).toBe(model3d);
    expect(pickModel([heroImage])).toBeNull();
    const detail = { ...heroImage, role: "detail", position: 5 };
    const gallery = { ...heroImage, role: "gallery", position: 2 };
    expect(imagesWithRoles([detail, heroImage, gallery], ["gallery", "detail"], heroImage)).toEqual(
      [gallery, detail],
    );
  });
});

import { paletteOf, paletteRoles } from "@/lib/palette";

describe("palette (ADR-030 phase 4)", () => {
  it("accepts 3-5 hex colours and ignores anything else", () => {
    expect(paletteOf({ palette: ["#5C3F28", "#c9761c", "#efe4cf"] })).toEqual([
      "#5c3f28",
      "#c9761c",
      "#efe4cf",
    ]);
    expect(paletteOf({ palette: ["#111111", "#222222"] })).toEqual([]);
    expect(paletteOf({ palette: "red" })).toEqual([]);
    expect(paletteOf({ palette: ["#111111", "red", "#333333", "#444444"] })).toEqual([
      "#111111",
      "#333333",
      "#444444",
    ]);
    expect(paletteOf(null)).toEqual([]);
  });

  it("picks accent, dark and light roles", () => {
    const roles = paletteRoles(["#5c3f28", "#f2a33a", "#efe4cf", "#7a7a7a"]);
    expect(roles).toEqual({ accent: "#f2a33a", dark: "#5c3f28", light: "#efe4cf" });
    expect(paletteRoles(["#111111"])).toBeNull();
  });
});

describe("getHero (P-028)", () => {
  const config = apiConfigFor("artesanfc.com");
  const clientFor = (status: number, body: unknown) =>
    createApiClient({
      config,
      fetch: async () => new Response(JSON.stringify(body), { status }),
    });
  const campaign = {
    slug: "dia-de-muertos",
    name: "Día de Muertos",
    reason: "date",
    video: { mp4: "/media/hero/dia-de-muertos/a.mp4", webm: null },
    poster: "/media/hero/dia-de-muertos/a.jpg",
  };

  it("returns the campaign, or null when none applies", async () => {
    expect(await clientFor(200, { data: campaign }).getHero()).toEqual({
      kind: "ok",
      data: { data: campaign },
    });
    expect(await clientFor(200, { data: null }).getHero()).toEqual({
      kind: "ok",
      data: { data: null },
    });
  });

  it("rejects a body that is not the contract and treats errors as unavailable", async () => {
    expect(await clientFor(200, { data: { slug: "x" } }).getHero()).toEqual({
      kind: "unavailable",
      reason: "malformed",
    });
    expect(await clientFor(503, {}).getHero()).toEqual({
      kind: "unavailable",
      reason: "server_error",
    });
  });
});
