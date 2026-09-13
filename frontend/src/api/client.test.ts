// Regression coverage for the FE error-shape unification (audit F-09 /
// F-10 / F-12). The bus of value:
//
// - getCharacterCoa() must surface a 404 as null (procedural fallback).
//   The pre-fix branch tested err.message.includes('404'), which never
//   matched ApiError's bare-detail .message.
// - migrateClient + exportClient must throw ApiError so consumers can
//   branch on err.status the same way as the main client.

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { ApiError, getCharacterCoa } from './client';
import { exportChronicle } from './exportClient';
import { runMigration } from './migrateClient';

describe('client — ApiError shape (audit F-09/F-10/F-12)', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function mockFetchResponse(status: number, body: unknown): void {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      statusText: 'mock',
      json: async () => body,
      blob: async () => new Blob([JSON.stringify(body)]),
      headers: new Headers({ 'content-disposition': '' }),
    } as unknown as Response);
  }

  it('getCharacterCoa returns null on 404 (was: threw because err.message did not contain "404")', async () => {
    mockFetchResponse(404, { detail: 'no CoA persisted for character 17' });
    const result = await getCharacterCoa('saga', 17);
    expect(result).toBeNull();
  });

  it('getCharacterCoa propagates non-404 ApiErrors instead of swallowing', async () => {
    mockFetchResponse(500, { detail: 'engine cache disposed' });
    await expect(getCharacterCoa('saga', 17)).rejects.toBeInstanceOf(ApiError);
    await expect(getCharacterCoa('saga', 17)).rejects.toMatchObject({ status: 500 });
  });

  it('getCharacterCoa does NOT swallow a non-404 whose detail happens to contain "404"', async () => {
    // Pre-fix: err.message.includes('404') would match this and incorrectly
    // surface null.
    mockFetchResponse(500, { detail: 'upstream returned 404 to us' });
    await expect(getCharacterCoa('saga', 17)).rejects.toBeInstanceOf(ApiError);
  });

  it('migrateClient throws ApiError (was: bare Error with "<status>: <detail>" message)', async () => {
    mockFetchResponse(503, { detail: 'save-tail still running' });
    await expect(runMigration()).rejects.toBeInstanceOf(ApiError);
    await expect(runMigration()).rejects.toMatchObject({
      status: 503,
      message: 'save-tail still running',
    });
  });

  it('exportClient throws ApiError on non-2xx', async () => {
    mockFetchResponse(409, { detail: 'campaign not sealed yet' });
    await expect(exportChronicle('saga', 'pdf')).rejects.toBeInstanceOf(ApiError);
    await expect(exportChronicle('saga', 'pdf')).rejects.toMatchObject({
      status: 409,
      message: 'campaign not sealed yet',
    });
  });
});
