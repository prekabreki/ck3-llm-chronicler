// Tests for ExportChronicleButton (ck3_chronicler-441).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ExportChronicleButton } from './ExportChronicleButton';
import * as exportClient from '../api/exportClient';

function renderWithClient(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ExportChronicleButton', () => {
  it('renders a button with the expected label', () => {
    renderWithClient(<ExportChronicleButton campaignName="Wessex" />);
    expect(
      screen.getByRole('button', { name: /Export chronicle/ }),
    ).toBeInTheDocument();
  });

  it('on click: calls the client and triggers a blob download', async () => {
    const blob = new Blob(['fake'], { type: 'application/zip' });
    const spy = vi.spyOn(exportClient, 'exportChronicle').mockResolvedValue({
      blob,
      filename: 'wessex-chronicle.zip',
    });
    const createObjectURL = vi.fn().mockReturnValue('blob:fake');
    const revokeObjectURL = vi.fn();
    Object.assign(globalThis.URL, { createObjectURL, revokeObjectURL });

    renderWithClient(<ExportChronicleButton campaignName="Wessex" />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Export chronicle/ }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith('Wessex', 'markdown'));
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake');
  });

  it('format="pdf" passes pdf to the client and labels the button accordingly', async () => {
    const blob = new Blob(['fake'], { type: 'application/pdf' });
    const spy = vi.spyOn(exportClient, 'exportChronicle').mockResolvedValue({
      blob,
      filename: 'wessex-chronicle.pdf',
    });
    Object.assign(globalThis.URL, {
      createObjectURL: vi.fn().mockReturnValue('blob:fake'),
      revokeObjectURL: vi.fn(),
    });

    renderWithClient(<ExportChronicleButton campaignName="Wessex" format="pdf" />);
    expect(screen.getByRole('button', { name: /Export PDF/ })).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Export PDF/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith('Wessex', 'pdf'));
  });

  it('surfaces an error message when the export fails', async () => {
    vi.spyOn(exportClient, 'exportChronicle').mockRejectedValue(
      new Error('409: campaign has no closing chronicle'),
    );
    renderWithClient(<ExportChronicleButton campaignName="Wessex" />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Export chronicle/ }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/no closing chronicle/),
    );
  });

  it('"quiet" variant uses btn--quiet class for kebab placement', () => {
    renderWithClient(
      <ExportChronicleButton campaignName="Wessex" variant="quiet" />,
    );
    const btn = screen.getByRole('button', { name: /Export chronicle/ });
    expect(btn.className).toContain('btn--quiet');
  });
});
