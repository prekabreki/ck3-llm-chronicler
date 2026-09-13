// audit F-29 / ck3_chronicler-vgzs: extracted from TrackedPage.tsx so
// the page component stays focused on the row grid + side rail. The
// modal scaffold (backdrop, paper, role=dialog, focus-trap) lives in
// ModalShell (audit M-F2 / ck3_chronicler-27ov.57); this component just
// supplies the form body.

import { useState } from 'react';

import { useAddTracked } from '../api/queries';
import { ModalShell } from './ModalShell';

interface AddTrackedModalProps {
  campaignName: string;
  onClose: () => void;
}

export function AddTrackedModal({
  campaignName,
  onClose,
}: AddTrackedModalProps): React.JSX.Element {
  const [characterId, setCharacterId] = useState('');
  const [role, setRole] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const mutation = useAddTracked(campaignName);

  const onSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    setError(null);
    const parsed = Number.parseInt(characterId.trim(), 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setError('Enter a positive integer CK3 character ID.');
      return;
    }
    mutation.mutate(
      {
        character_id: parsed,
        role: role.trim() || null,
        note: note.trim() || null,
      },
      {
        onSuccess: () => onClose(),
        onError: (err) =>
          setError(err instanceof Error ? err.message : String(err)),
      },
    );
  };

  return (
    <ModalShell
      titleId="add-tracked-title"
      eyebrow="+ Add by ID"
      title="Track a character"
      onClose={onClose}
    >
      <form className="modal__body" onSubmit={onSubmit}>
        <p className="italic-fell modal__lede">
          Track a soul by their CK3 character ID. Find the ID via the
          in-game console (<code>charinfo 1</code> then hover the
          portrait), or from the URL on a Codex row.
        </p>
        <label className="modal__field">
          <span className="smallcaps modal__field-label">CK3 character ID</span>
          <input
            type="text"
            inputMode="numeric"
            className="modal__input"
            value={characterId}
            onChange={(e) => setCharacterId(e.target.value)}
            placeholder="e.g. 36892"
            required
            autoFocus
            disabled={mutation.isPending}
          />
        </label>
        <label className="modal__field">
          <span className="smallcaps modal__field-label">Role (optional)</span>
          <input
            type="text"
            className="modal__input"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            placeholder="rival, vassal, heir, …"
            disabled={mutation.isPending}
          />
        </label>
        <label className="modal__field">
          <span className="smallcaps modal__field-label">Note (optional)</span>
          <input
            type="text"
            className="modal__input"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="why you're tracking them"
            disabled={mutation.isPending}
          />
        </label>
        <div className="modal__actions">
          <button
            type="submit"
            className="btn"
            disabled={mutation.isPending || !characterId.trim()}
          >
            {mutation.isPending ? 'Adding…' : 'Add'}
          </button>
          <button
            type="button"
            className="btn btn--quiet"
            onClick={onClose}
            disabled={mutation.isPending}
          >
            Cancel
          </button>
        </div>
        {error && (
          <p className="italic-fell modal__error" role="alert">
            {error}
          </p>
        )}
      </form>
    </ModalShell>
  );
}
