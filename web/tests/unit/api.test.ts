import { afterEach, describe, expect, it, vi } from "vitest";
import { apiConfigFor } from "../../src/lib/api-config";
import { createApiClient } from "../../src/lib/api";
import { artisan, certificate, piece, summary } from "../fixtures/contract";

const local = apiConfigFor("localhost");
const json = (status: number, body: unknown) =>
  Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );

function client(fetchImpl: (url: string, init?: RequestInit) => Promise<Response>, timeoutMs = 50) {
  return createApiClient({ config: local, fetch: fetchImpl, timeoutMs });
}

afterEach(() => vi.restoreAllMocks());

describe("GET wrappers", () => {
  it("returns ok with a contract-shaped body", async () => {
    const calls: string[] = [];
    const api = client((url) => {
      calls.push(url);
      return json(200, piece);
    });
    await expect(api.getPiece("mascara-prueba")).resolves.toEqual({ kind: "ok", data: piece });
    expect(calls).toEqual(["http://127.0.0.1:8000/api/v1/pieces/mascara-prueba"]);
  });

  it("encodes the slug as one path segment", async () => {
    const calls: string[] = [];
    const api = client((url) => {
      calls.push(url);
      return json(404, {});
    });
    await api.getArtisan("a/b?c");
    expect(calls[0]).toBe("http://127.0.0.1:8000/api/v1/artisans/a%2Fb%3Fc");
  });

  it.each([
    [404, { kind: "not_found" }],
    [429, { kind: "unavailable", reason: "rate_limited" }],
    [500, { kind: "unavailable", reason: "server_error" }],
    [503, { kind: "unavailable", reason: "server_error" }],
    [422, { kind: "unavailable", reason: "http_error" }],
  ])("maps HTTP %i", async (status, expected) => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const api = client(() => json(status, { error: { code: "x", message: "y" } }));
    await expect(api.getPiece("x")).resolves.toEqual(expected);
  });

  it("rejects a 200 that does not match the contract", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const api = client(() => json(200, [piece]));
    await expect(api.getPiece("x")).resolves.toEqual({ kind: "unavailable", reason: "malformed" });
    const lists = client(() => json(200, { data: [{ nope: true }], meta: {} }));
    await expect(lists.getPieces()).resolves.toEqual({ kind: "unavailable", reason: "malformed" });
  });

  it("accepts list envelopes and additive fields", async () => {
    const api = client(() =>
      json(200, { data: [{ ...summary(piece), future_field: 1 }], meta: { total: 1 } }),
    );
    const result = await api.getPieces();
    expect(result.kind).toBe("ok");
    const artisanApi = client(() => json(200, artisan));
    expect((await artisanApi.getArtisan("a")).kind).toBe("ok");
  });

  it("reports a network error and a timeout distinctly", async () => {
    const offline = client(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(offline.getPieces()).resolves.toEqual({ kind: "unavailable", reason: "network" });
    const slow = client(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
      20,
    );
    await expect(slow.getPieces()).resolves.toEqual({ kind: "unavailable", reason: "timeout" });
  });

  it("makes no request at all on an unconfigured host", async () => {
    const fetchSpy = vi.fn();
    const api = createApiClient({ config: apiConfigFor("preview.pages.dev"), fetch: fetchSpy });
    await expect(api.getPieces()).resolves.toEqual({ kind: "unavailable", reason: "unconfigured" });
    await expect(api.resolveCertificate("abc")).resolves.toEqual({
      kind: "error",
      reason: "unconfigured",
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("resolveCertificate", () => {
  const TOKEN = "tok_SECRET-abcdefghijklmnopqrstuvwxyz0123456789";

  it("posts the token only in the body, without credentials or referrer", async () => {
    let seen: { url: string; init?: RequestInit | undefined } = { url: "" };
    const api = client((url, init) => {
      seen = { url, init };
      return json(200, certificate);
    });
    await expect(api.resolveCertificate(TOKEN)).resolves.toEqual({
      kind: "authentic",
      data: certificate,
    });
    expect(seen.url).toBe("http://127.0.0.1:8000/api/v1/certificates/resolve");
    expect(seen.url).not.toContain(TOKEN);
    expect(seen.init?.method).toBe("POST");
    expect(seen.init?.body).toBe(JSON.stringify({ token: TOKEN }));
    expect(seen.init?.credentials).toBe("omit");
    expect(seen.init?.referrerPolicy).toBe("no-referrer");
  });

  it("keeps 'unavailable' (a valid answer) apart from transport errors", async () => {
    const unavailable = client(() => json(200, { authenticity: { status: "unavailable" } }));
    await expect(unavailable.resolveCertificate(TOKEN)).resolves.toEqual({ kind: "unavailable" });
    const limited = client(() => json(429, { error: { code: "rate_limited", message: "x" } }));
    await expect(limited.resolveCertificate(TOKEN)).resolves.toEqual({
      kind: "error",
      reason: "rate_limited",
    });
    const down = client(() => json(502, {}));
    await expect(down.resolveCertificate(TOKEN)).resolves.toEqual({
      kind: "error",
      reason: "server_error",
    });
    const weird = client(() => json(200, { authenticity: { status: "revoked" } }));
    await expect(weird.resolveCertificate(TOKEN)).resolves.toEqual({
      kind: "error",
      reason: "malformed",
    });
    const offline = client(() => Promise.reject(new TypeError("offline")));
    await expect(offline.resolveCertificate(TOKEN)).resolves.toEqual({
      kind: "error",
      reason: "network",
    });
  });

  it("never writes the token to the console", async () => {
    const spies = (["log", "warn", "error", "info", "debug"] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation(() => undefined),
    );
    for (const f of [
      () => json(500, {}),
      () => json(200, "junk"),
      () => Promise.reject(new Error(TOKEN)),
    ]) {
      await client(f).resolveCertificate(TOKEN);
    }
    for (const spy of spies) {
      for (const call of spy.mock.calls) expect(JSON.stringify(call)).not.toContain(TOKEN);
    }
  });
});
