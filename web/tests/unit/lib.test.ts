import { describe, expect, it } from "vitest";
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
