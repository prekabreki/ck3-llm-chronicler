// M-F7 (27ov.61): the ~115-line path-editor state machine (view ↔ edit
// draft, Enter/Escape keys, Save / Cancel / Reset-to-default, pending
// + error surfaces) was duplicated between PathsCard's PathRow and
// ProseRepoCard's ProseRepoPathRow. One owner; parents supply the
// labels, the resolved/override values, and an onSubmit that performs
// their mutation (null value = clear the override).
//
// Issue #23 widened it past paths: the LLMEngineCard's model rows want the
// identical machine over a model tag, so `placeholder` is a prop and
// `source` is optional (a model row has no provenance to chip).

import { useState } from 'react';

const SOURCE_LABEL: Record<string, string> = {
  override: 'Override',
  env: 'Env var',
  default: 'Default',
  probe: 'Probed',
};

interface EditablePathRowProps {
  label: string;
  hint: React.ReactNode;
  /** Provenance chip. Omit for a value with no resolution tiers to report. */
  source?: string | null;
  resolved: string | null;
  override: string | null;
  /** Shown when ``resolved`` is empty, e.g. '— not found —'. */
  emptyText: string;
  resetTitle: string;
  /** Editor placeholder. Doubles as the test handle for the input. */
  placeholder?: string;
  /** data-testid for the resolved-value element. */
  valueTestId?: string;
  /** Whether to offer "Reset to default". Defaults to "an override is
   * set" — pass explicitly for a value whose response carries no
   * provenance (the model rows), where clearing is always meaningful. */
  canReset?: boolean;
  /** Extra badges next to the source chip (e.g. the exists pip). */
  meta?: React.ReactNode;
  isPending: boolean;
  errorText: string | null;
  /** Persist the value (null clears the override). Resolves on success;
   * a rejection keeps the editor open with ``errorText`` rendered. */
  onSubmit: (value: string | null) => Promise<unknown>;
}

export function EditablePathRow({
  label,
  hint,
  source,
  resolved,
  override,
  emptyText,
  resetTitle,
  placeholder = 'Absolute path…',
  valueTestId,
  canReset,
  meta,
  isPending,
  errorText,
  onSubmit,
}: EditablePathRowProps): React.JSX.Element {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(override ?? resolved ?? '');

  const startEdit = (): void => {
    setDraft(override ?? resolved ?? '');
    setEditing(true);
  };

  const cancel = (): void => {
    setEditing(false);
    setDraft(override ?? resolved ?? '');
  };

  const submit = (value: string | null): void => {
    void onSubmit(value).then(
      () => setEditing(false),
      () => undefined, // error surfaces via errorText
    );
  };

  const save = (): void => {
    const trimmed = draft.trim();
    submit(trimmed === '' ? null : trimmed);
  };

  const sourceLabel = source ? (SOURCE_LABEL[source] ?? source) : null;
  const sourceClass =
    source === 'override'
      ? 'paths-row__source paths-row__source--override'
      : source === 'env'
        ? 'paths-row__source paths-row__source--env'
        : 'paths-row__source paths-row__source--default';

  return (
    <div className="paths-row">
      <div className="paths-row__head">
        <div className="paths-row__label-col">
          <div className="smallcaps paths-row__label">{label}</div>
          <p className="italic-fell paths-row__hint">{hint}</p>
        </div>
        <div className="paths-row__meta">
          {sourceLabel !== null && (
            <span className={sourceClass}>{sourceLabel}</span>
          )}
          {meta}
        </div>
      </div>

      {!editing ? (
        <div className="paths-row__value">
          <code className="paths-row__resolved" data-testid={valueTestId}>
            {resolved || emptyText}
          </code>
          <div className="paths-row__actions">
            <button
              type="button"
              className="btn btn--ghost paths-row__edit"
              onClick={startEdit}
            >
              Edit
            </button>
          </div>
        </div>
      ) : (
        <div className="paths-row__editor">
          <input
            type="text"
            className="paths-row__input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={placeholder}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') save();
              if (e.key === 'Escape') cancel();
            }}
          />
          <div className="paths-row__actions">
            <button
              type="button"
              className="btn btn--primary"
              disabled={isPending}
              onClick={save}
            >
              Save
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={isPending}
              onClick={cancel}
            >
              Cancel
            </button>
            {(canReset ?? override !== null) && (
              <button
                type="button"
                className="btn btn--ghost"
                disabled={isPending}
                onClick={() => submit(null)}
                title={resetTitle}
              >
                Reset to default
              </button>
            )}
          </div>
          {errorText !== null && (
            <div className="paths-row__error italic-fell">{errorText}</div>
          )}
        </div>
      )}
    </div>
  );
}
