// Tests for ConfirmDialog — the confirm-shaped modal (lede + confirm/
// cancel actions + error) built on ModalShell. Audit M-F2 /
// ck3_chronicler-27ov.57: four surfaces shared this exact shape
// (delete campaign, reset baseline, untrack, regenerate biography).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ConfirmDialog } from './ConfirmDialog';

afterEach(cleanup);

function setup(overrides: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmDialog
      titleId="confirm-title"
      eyebrow="⚠ Permanently delete"
      title="Erase Wessex?"
      confirmLabel="Delete permanently"
      busyLabel="Deleting…"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...overrides}
    >
      <p className="modal__lede">This cannot be undone.</p>
    </ConfirmDialog>,
  );
  return { onConfirm, onCancel };
}

describe('ConfirmDialog', () => {
  it('renders eyebrow, title, body, and confirm/cancel buttons', () => {
    setup();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Erase Wessex?' })).toBeInTheDocument();
    expect(screen.getByText('⚠ Permanently delete')).toBeInTheDocument();
    expect(screen.getByText('This cannot be undone.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete permanently' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled();
  });

  it('confirm and cancel buttons fire their handlers', async () => {
    const { onConfirm, onCancel } = setup();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Delete permanently' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('busy disables both buttons and shows the busy label', () => {
    setup({ busy: true });
    expect(screen.getByRole('button', { name: 'Deleting…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  });

  it('renders the error as an alert', () => {
    setup({ error: 'Server said no' });
    expect(screen.getByRole('alert')).toHaveTextContent('Server said no');
  });

  it('Escape cancels when not busy (a11y fix)', async () => {
    const { onCancel } = setup();
    await userEvent.keyboard('{Escape}');
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('Escape does nothing while busy', async () => {
    const { onCancel } = setup({ busy: true });
    await userEvent.keyboard('{Escape}');
    expect(onCancel).not.toHaveBeenCalled();
  });
});
