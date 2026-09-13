// audit F-15 / ck3_chronicler-2isd: locks in the boundary's contract.
// Without it, a render-time exception anywhere in the React tree
// would blank the entire app — a single malformed CoA blob (combined
// with the depth-overflow risk in F-36) could brick the UI.

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { ErrorBoundary } from './ErrorBoundary';

function Boom(): never {
  throw new Error('intentional render failure');
}

describe('ErrorBoundary (audit F-15)', () => {
  let originalError: typeof console.error;
  let originalReload: typeof window.location.reload;

  beforeEach(() => {
    // React error boundaries log to console.error during the catch path;
    // silence to keep test output clean.
    originalError = console.error;
    console.error = vi.fn();
    originalReload = window.location.reload;
  });

  afterEach(() => {
    console.error = originalError;
    Object.defineProperty(window.location, 'reload', {
      configurable: true,
      value: originalReload,
    });
  });

  it('renders children when no error', () => {
    render(
      <ErrorBoundary>
        <p>healthy content</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText('healthy content')).toBeInTheDocument();
  });

  it('renders fallback with the error message when a child throws', () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/intentional render failure/)).toBeInTheDocument();
  });

  it('Reset clears the appstate localStorage entry then reloads', () => {
    window.localStorage.setItem(
      'chronicler:appstate',
      JSON.stringify({ activeCampaign: 'corrupt' }),
    );
    const reload = vi.fn();
    Object.defineProperty(window.location, 'reload', {
      configurable: true,
      value: reload,
    });

    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByRole('button', { name: /reset/i }));

    expect(window.localStorage.getItem('chronicler:appstate')).toBeNull();
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
