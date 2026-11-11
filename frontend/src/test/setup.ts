// Vitest setup — runs before every test file.
// Adds @testing-library/jest-dom matchers (toBeInTheDocument, toHaveClass, ...).

import '@testing-library/jest-dom/vitest';

// Issue #34: happy-dom resolves relative URLs against its default origin
// (http://localhost:3000), so an unstubbed fetch in a page test made a real
// socket connection and printed ECONNREFUSED while the test still passed.
// Fail loudly with the URL instead: a test that needs the network must stub
// the fetcher (or the fetch itself), never rely on an empty socket.
const unmockedFetch = (input: RequestInfo | URL): Promise<Response> => {
  const url = input instanceof Request ? input.url : String(input);
  return Promise.reject(
    new Error(
      `Unexpected unmocked fetch to ${url}. Stub the API call in this test.`,
    ),
  );
};

globalThis.fetch = unmockedFetch as typeof fetch;
