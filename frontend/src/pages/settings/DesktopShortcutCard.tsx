// Issue #10: desktop-shortcut Settings card. A single button labelled
// exactly "Create Desktop shortcut for Chronicler" that POSTs to
// /api/settings/desktop-shortcut; on success it shows the created path
// and platform, on failure the server's message. Pending state while the
// request runs (Pillow conversion + subprocesses make it slow enough to
// need one).

import { useMutation } from '@tanstack/react-query';

import { createDesktopShortcut } from '../../api/client';

export function DesktopShortcutCard(): React.JSX.Element {
  const mutation = useMutation({
    mutationFn: () => createDesktopShortcut(),
  });

  return (
    <section
      id="desktop-shortcut"
      className="settings-card"
      data-testid="desktop-shortcut-card"
    >
      <div className="settings-card__head">
        <h2 className="settings-card__title">Desktop shortcut</h2>
        <p className="italic-fell settings-card__description">
          Put a Crusader Kings III icon on your desktop that runs{' '}
          <code>chronicler launch</code> — starting the chronicler, CK3 and
          the web app together.
        </p>
      </div>
      <div className="settings-card__body">
        <button
          type="button"
          className="btn btn--primary"
          disabled={mutation.isPending}
          onClick={() => mutation.mutate()}
          data-testid="desktop-shortcut-button"
        >
          {mutation.isPending ? 'Creating…' : 'Create Desktop shortcut for Chronicler'}
        </button>

        {mutation.isError && (
          <p
            className="italic-fell paths-row__error"
            data-testid="desktop-shortcut-error"
          >
            {mutation.error.message}
          </p>
        )}

        {mutation.isSuccess && (
          <p className="italic-fell" data-testid="desktop-shortcut-result">
            Shortcut created at <code>{mutation.data.path}</code> (
            {mutation.data.platform}).
          </p>
        )}
      </div>
    </section>
  );
}
