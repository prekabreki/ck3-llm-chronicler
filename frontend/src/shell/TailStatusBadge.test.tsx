import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { TailStatusBadge } from './TailStatusBadge';
import type { TailStatus } from './tailStatus';

const green: TailStatus = { level: 'live', label: 'Live · safe to play', detail: null, tone: 'green' };
const red: TailStatus = { level: 'wrong-game', label: 'Not recording — wrong game', detail: 'x', tone: 'red' };

describe('TailStatusBadge', () => {
  it('renders the status label', () => {
    render(<TailStatusBadge status={green} />);
    expect(screen.getByText(/Live · safe to play/)).toBeInTheDocument();
  });

  it('applies a tone class', () => {
    const { container } = render(<TailStatusBadge status={red} />);
    expect(container.querySelector('.tail-badge--red')).not.toBeNull();
  });

  it('fires onClick when not busy', async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<TailStatusBadge status={green} onClick={onClick} />);
    await user.click(screen.getByRole('button', { name: /Live · safe to play/ }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('shows Halting… and is disabled when busy', () => {
    render(<TailStatusBadge status={green} busy />);
    expect(screen.getByText(/Halting…/)).toBeInTheDocument();
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('does not fire onClick when busy', async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<TailStatusBadge status={green} onClick={onClick} busy />);
    await user.click(screen.getByRole('button'));
    expect(onClick).not.toHaveBeenCalled();
  });
});
