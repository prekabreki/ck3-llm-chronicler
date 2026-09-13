// ck3_chronicler-gx7b + 5d9o: LLM engine card. Two subsections:
//   1. Pause toggle — global LLM pause. Save-tail keeps ingesting; only the
//      autonomous biography schedule calls are gated.
//      Flipping OFF drains every open campaign through the catchup scan.
//   2. Per-kind model rows — the tag each prompt kind resolves to, editable
//      in place (issue #23) via PUT /api/settings/models. The env vars
//      remain as a fallback tier below the stored setting; the panel always
//      shows the RESOLVED value, so a global override that shadows a row
//      the user just saved is visible rather than mysterious.

import {
  useLLMPause,
  useResolvedModels,
  useToggleLLMPause,
  useUpdateResolvedModels,
} from '../../api/queries';
import type { LLMPauseResponse, ResolvedModelsResponse } from '../../api/types';
import { EditablePathRow } from './EditablePathRow';

const KIND_ROWS: { key: 'biography' | 'closing'; label: string; hint: string }[] = [
  {
    key: 'biography',
    label: 'Biography',
    hint:
      'Fires on a tracked character\'s death event (and on manual Regenerate). Narrative craft — Opus by default.',
  },
  {
    key: 'closing',
    label: 'Closing chronicle',
    hint:
      'Synthesises every tracked biography into the campaign capstone when you click "Complete Campaign". Highest-stakes write — Opus by default.',
  },
];

export function LLMEngineCard(): React.JSX.Element {
  const modelsQ = useResolvedModels();
  const models = modelsQ.data ?? null;
  const pauseQ = useLLMPause();
  const pause = pauseQ.data ?? null;
  const toggleMutation = useToggleLLMPause();

  const onToggle = (): void => {
    if (pause === null || toggleMutation.isPending) {
      return;
    }
    toggleMutation.mutate({ paused: !pause.paused });
  };

  return (
    <article
      className="paper paper--edged provider-card"
      data-testid="llm-engine-card"
    >
      <div className="provider-card__head">
        <span className="provider-card__glyph">⚙</span>
        <div>
          <div className="uncial provider-card__title">The engine</div>
          <div className="smallcaps provider-card__sub">
            Pause toggle · per-kind model routing
          </div>
        </div>
      </div>

      {pauseQ.isLoading && (
        <p className="italic-fell provider-card__locked-note">Reading pause state…</p>
      )}
      {pauseQ.isError && (
        <p className="italic-fell provider-card__locked-note">
          Could not load pause state. {String(pauseQ.error)}
        </p>
      )}

      {pause && (
        <LLMPauseRow
          pause={pause}
          onToggle={onToggle}
          pending={toggleMutation.isPending}
          mutationError={toggleMutation.error}
        />
      )}

      {modelsQ.isLoading && (
        <p className="italic-fell provider-card__locked-note">Reading models…</p>
      )}
      {modelsQ.isError && (
        <p className="italic-fell provider-card__locked-note">
          Could not load model status. {String(modelsQ.error)}
        </p>
      )}

      {models && (
        <>
          {models.global_override !== null && (
            <p
              className="italic-fell provider-card__locked-note"
              data-testid="llm-global-override"
            >
              A global override is set — every kind below resolves to{' '}
              <code>{models.global_override}</code> regardless of its own
              value. Clear the <strong>All kinds</strong> row (or unset{' '}
              <code>CHRONICLER_NARRATIVE_MODEL</code>) to re-enable per-kind
              tags.
            </p>
          )}
          <ModelRows models={models} />
        </>
      )}
    </article>
  );
}

// Issue #23: the model rows, editable in place. One mutation for all three
// rows — the PUT is partial, so a row saves only itself; saving an empty
// value clears the stored tag and falls back to env-then-default, which is
// why every row offers Reset (the GET carries no per-row provenance to
// decide that from).
function ModelRows({
  models,
}: {
  models: ResolvedModelsResponse;
}): React.JSX.Element {
  const mutation = useUpdateResolvedModels();
  const errorText = mutation.isError ? String(mutation.error) : null;

  return (
    <div className="llm-model-rows" data-testid="llm-model-rows">
      <EditablePathRow
        label="All kinds"
        hint={
          <>
            Pins every kind to one tag. Leave blank for per-kind defaults;
            falls back to <code>CHRONICLER_NARRATIVE_MODEL</code>.
          </>
        }
        resolved={models.global_override}
        override={models.global_override}
        emptyText="— not set —"
        resetTitle="Clear the global override; fall back to per-kind tags"
        placeholder="e.g. claude-opus-5"
        isPending={mutation.isPending}
        errorText={errorText}
        onSubmit={(value) => mutation.mutateAsync({ global_override: value ?? '' })}
      />
      {KIND_ROWS.map((row) => (
        <EditablePathRow
          key={row.key}
          label={row.label}
          hint={row.hint}
          resolved={models[row.key]}
          override={null}
          canReset
          emptyText="— unresolved —"
          resetTitle="Clear the stored tag; fall back to env / default"
          placeholder="e.g. claude-opus-5"
          isPending={mutation.isPending}
          errorText={errorText}
          onSubmit={(value) => mutation.mutateAsync({ [row.key]: value ?? '' })}
          valueTestId={`llm-model-${row.key}`}
        />
      ))}
    </div>
  );
}

interface LLMPauseRowProps {
  pause: LLMPauseResponse;
  onToggle: () => void;
  pending: boolean;
  mutationError: Error | null;
}

function LLMPauseRow({
  pause,
  onToggle,
  pending,
  mutationError,
}: LLMPauseRowProps): React.JSX.Element {
  const statusLabel = pause.paused ? 'Paused' : 'Live';
  // Render the toggle as a button that flips state. Two visual states —
  // "Live" (gold-tinted ghost) and "Paused" (warn-tinted filled). Both
  // are clickable; pending disables.
  const btnClass = pause.paused
    ? 'btn btn--primary'
    : 'btn btn--ghost';
  const btnLabel = pause.paused ? 'Resume LLM generation' : 'Pause LLM generation';

  return (
    <div className="provider-card__pause-row" data-testid="llm-pause-row">
      <div className="provider-card__pause-status">
        <span
          className={
            pause.paused
              ? 'pip pip--warn'
              : 'pip'
          }
          data-testid="llm-pause-status-pip"
        >
          <span
            className={
              pause.paused
                ? 'pip__dot pip__dot--warn'
                : 'pip__dot pip__dot--alive'
            }
          />{' '}
          {statusLabel}
        </span>
        {pause.paused && pause.paused_at && (
          <span
            className="italic-fell provider-card__toggle-hint"
            data-testid="llm-paused-since"
          >
            since {pause.paused_at}
          </span>
        )}
      </div>

      {pause.paused ? (
        <p className="italic-fell provider-card__toggle-hint">
          Save-tail is still ingesting and persisting events. Resuming will
          drain every open campaign through the catchup scan — biographies
          will fire for dead-without-bio characters.
        </p>
      ) : (
        <p className="italic-fell provider-card__toggle-hint">
          Biographies auto-fire on the usual triggers. Pause to play a
          campaign without burning Opus tokens — ingest keeps running,
          only the autonomous LLM work is gated.
        </p>
      )}

      {pause.last_drain &&
        pause.last_drain.biographies_scheduled > 0 && (
          <p
            className="italic-fell provider-card__toggle-hint"
            data-testid="llm-last-drain"
          >
            Last resume drained{' '}
            <strong>{pause.last_drain.biographies_scheduled}</strong>{' '}
            biographies.
          </p>
        )}

      <button
        type="button"
        className={btnClass}
        disabled={pending}
        onClick={onToggle}
        data-testid="llm-pause-toggle"
      >
        {pending ? 'Working…' : btnLabel}
      </button>

      {mutationError && (
        <p
          className="italic-fell paths-row__error"
          data-testid="llm-pause-error"
        >
          Toggle failed: {String(mutationError)}
        </p>
      )}
    </div>
  );
}
