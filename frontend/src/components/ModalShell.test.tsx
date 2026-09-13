// Tests for ModalShell — the shared modal scaffold (backdrop + paper +
// head + a11y wiring) extracted by audit M-F2 / ck3_chronicler-27ov.57.
// The a11y assertions (Escape, focus-in) are the whole point: the two
// LibraryPage confirms previously hand-rolled the markup WITHOUT the
// focus-trap/Escape/restore the other modals had.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ModalShell } from './ModalShell';

afterEach(cleanup);

describe('ModalShell', () => {
  it('renders eyebrow, title, and body inside a labelled dialog', () => {
    render(
      <ModalShell titleId="t" eyebrow="An eyebrow" title="A title" onClose={vi.fn()}>
        <div className="modal__body">body content</div>
      </ModalShell>,
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAttribute('aria-labelledby', 't');
    expect(dialog).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('heading', { name: 'A title' })).toHaveAttribute('id', 't');
    expect(screen.getByText('An eyebrow')).toBeInTheDocument();
    expect(screen.getByText('body content')).toBeInTheDocument();
  });

  it('moves focus into the dialog on open (a11y)', async () => {
    render(
      <ModalShell titleId="t" eyebrow="e" title="T" onClose={vi.fn()}>
        <button type="button">First focusable</button>
      </ModalShell>,
    );
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'First focusable' })).toHaveFocus(),
    );
  });

  it('Escape calls onClose by default (a11y)', async () => {
    const onClose = vi.fn();
    render(
      <ModalShell titleId="t" eyebrow="e" title="T" onClose={onClose}>
        <button type="button">x</button>
      </ModalShell>,
    );
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape does nothing when closeOnEscape is false', async () => {
    const onClose = vi.fn();
    render(
      <ModalShell titleId="t" eyebrow="e" title="T" onClose={onClose} closeOnEscape={false}>
        <button type="button">x</button>
      </ModalShell>,
    );
    await userEvent.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('backdrop click closes by default', async () => {
    const onClose = vi.fn();
    const { container } = render(
      <ModalShell titleId="t" eyebrow="e" title="T" onClose={onClose}>
        <button type="button">x</button>
      </ModalShell>,
    );
    await userEvent.click(container.querySelector('.modal-backdrop')!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('backdrop click does not close when closeOnBackdrop is false', async () => {
    const onClose = vi.fn();
    const { container } = render(
      <ModalShell titleId="t" eyebrow="e" title="T" onClose={onClose} closeOnBackdrop={false}>
        <button type="button">x</button>
      </ModalShell>,
    );
    await userEvent.click(container.querySelector('.modal-backdrop')!);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('clicking inside the dialog does not close (stops propagation)', async () => {
    const onClose = vi.fn();
    render(
      <ModalShell titleId="t" eyebrow="e" title="T" onClose={onClose}>
        <button type="button">Inside</button>
      </ModalShell>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Inside' }));
    expect(onClose).not.toHaveBeenCalled();
  });
});
