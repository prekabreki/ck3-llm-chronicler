// Issue #23: the narrative-backend picker.
//
// Surfaces the #45/#46 machinery: which of the three transports writes the
// chronicles, and what that transport is pointed at. One provider per
// process (see CLAUDE.md) — this is a *choice*, not a route, and it takes
// effect on the next chronicler start, which the card says out loud.
//
// The key fields are write-only by construction. The GET never returns a
// stored key, so there is nothing to prefill: an untouched field submits
// nothing at all (the PUT is partial), which is what stops a "save the
// base URL" click from blanking a key that already works.

import { useState } from 'react';

import { useNarrativeBackend, useUpdateNarrativeBackend } from '../../api/queries';
import type {
  BackendKeyState,
  NarrativeBackendResponse,
  NarrativeBackendUpdateRequest,
} from '../../api/types';

const BACKEND_LABELS: Record<string, { title: string; blurb: string }> = {
  'claude-code': {
    title: 'Claude Code',
    blurb:
      'Shells out to the claude CLI. No API key — it bills your subscription’s monthly programmatic credit pool.',
  },
  anthropic: {
    title: 'Anthropic API',
    blurb:
      'Direct Messages API on an ANTHROPIC_API_KEY. Metered per token against that key’s account.',
  },
  'openai-compatible': {
    title: 'OpenAI-compatible',
    blurb:
      'One chat-completions transport for OpenAI, DeepSeek, OpenRouter and any local server (Ollama, LM Studio).',
  },
};

const GENERIC_PRESET_CHOICE = '';

export function BackendCard(): React.JSX.Element {
  const backendQ = useNarrativeBackend();
  const data = backendQ.data ?? null;

  return (
    <article className="paper paper--edged provider-card" data-testid="backend-card">
      <div className="provider-card__head">
        <span className="provider-card__glyph">⁂</span>
        <div>
          <div className="uncial provider-card__title">The backend</div>
          <div className="smallcaps provider-card__sub">
            Who is billed for the prose
          </div>
        </div>
      </div>

      {backendQ.isLoading && (
        <p className="italic-fell provider-card__locked-note">Reading backend…</p>
      )}
      {backendQ.isError && (
        <p className="italic-fell provider-card__locked-note">
          Could not load the backend settings. {String(backendQ.error)}
        </p>
      )}
      {data && <BackendForm data={data} />}
    </article>
  );
}

function BackendForm({ data }: { data: NarrativeBackendResponse }): React.JSX.Element {
  const mutation = useUpdateNarrativeBackend();

  // Chosen-but-unsaved backend. Keyed off the server value so a refetch
  // that reports someone else's write (or our own) re-syncs the radio.
  const [choice, setChoice] = useState<string | null>(null);
  const selected = choice ?? data.backend;
  const dirty = selected !== data.backend;

  const save = (body: NarrativeBackendUpdateRequest): void => {
    mutation.mutate(body, { onSuccess: () => setChoice(null) });
  };

  return (
    <>
      <div
        className="backend-choices"
        role="radiogroup"
        aria-label="Narrative backend"
      >
        {data.valid_backends.map((id) => {
          const meta = BACKEND_LABELS[id];
          const active = selected === id;
          return (
            <label
              key={id}
              className={
                active ? 'backend-choice backend-choice--active' : 'backend-choice'
              }
              data-testid={`backend-choice-${id}`}
            >
              <input
                type="radio"
                name="narrative-backend"
                value={id}
                checked={active}
                onChange={() => setChoice(id)}
              />
              <span>
                <span className="smallcaps backend-choice__title">
                  {meta?.title ?? id}
                  {id === data.backend && (
                    <span className="backend-choice__current"> · in use</span>
                  )}
                </span>
                <span className="italic-fell backend-choice__blurb">
                  {meta?.blurb ?? id}
                </span>
              </span>
            </label>
          );
        })}
      </div>

      <p className="italic-fell provider-card__toggle-hint">
        Resolved from <strong>{sourceLabel(data.source)}</strong>. One backend
        per process: a change here takes effect the next time chronicler
        starts.
      </p>

      {!data.usable && data.error !== null && (
        <p className="italic-fell paths-row__error" data-testid="backend-error">
          {data.error}
        </p>
      )}

      {selected === 'anthropic' && (
        <KeyField
          label="Anthropic API key"
          hint={
            <>
              Write-only. Falls back to the <code>ANTHROPIC_API_KEY</code> env
              var when nothing is stored here.
            </>
          }
          state={data.anthropic_key}
          testId="anthropic-key"
          isPending={mutation.isPending}
          onSave={(value) => save({ anthropic_api_key: value })}
        />
      )}

      {selected === 'openai-compatible' && (
        <OpenAIFields data={data} isPending={mutation.isPending} onSave={save} />
      )}

      {dirty && (
        <button
          type="button"
          className="btn btn--primary backend-card__apply"
          disabled={mutation.isPending}
          onClick={() => save({ backend: selected })}
          data-testid="backend-apply"
        >
          {mutation.isPending ? 'Saving…' : `Use ${BACKEND_LABELS[selected]?.title ?? selected}`}
        </button>
      )}

      {mutation.isError && (
        <p
          className="italic-fell paths-row__error"
          data-testid="backend-save-error"
        >
          Save failed: {String(mutation.error)}
        </p>
      )}
    </>
  );
}

interface OpenAIFieldsProps {
  data: NarrativeBackendResponse;
  isPending: boolean;
  onSave: (body: NarrativeBackendUpdateRequest) => void;
}

function OpenAIFields({
  data,
  isPending,
  onSave,
}: OpenAIFieldsProps): React.JSX.Element {
  const [preset, setPreset] = useState(data.openai_preset ?? GENERIC_PRESET_CHOICE);
  // The base URL a preset supplies; the field is a placeholder-only
  // override so picking "DeepSeek" doesn't require typing its endpoint.
  const presetInfo = data.presets.find((p) => p.id === preset) ?? null;
  const [baseUrl, setBaseUrl] = useState(data.openai_base_url ?? '');
  const [model, setModel] = useState(data.openai_model ?? '');

  const pickPreset = (value: string): void => {
    setPreset(value);
    // Only ever clear a base URL the *previous* preset supplied — a URL
    // the user typed themselves is theirs to keep.
    const previous = data.presets.find((p) => p.id === preset);
    if (previous && baseUrl === previous.base_url) {
      setBaseUrl('');
    }
  };

  const keyRequired = presetInfo?.requires_key ?? false;

  return (
    <div className="backend-openai" data-testid="backend-openai-fields">
      <label className="backend-field">
        <span className="smallcaps backend-field__label">Preset</span>
        <select
          className="paths-row__input"
          value={preset}
          onChange={(e) => pickPreset(e.target.value)}
          data-testid="openai-preset"
        >
          <option value={GENERIC_PRESET_CHOICE}>
            Custom (any OpenAI-compatible endpoint)
          </option>
          {data.presets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.id}
            </option>
          ))}
        </select>
        <span className="italic-fell backend-field__hint">
          {presetInfo
            ? `Endpoint ${presetInfo.base_url}${presetInfo.requires_key ? ' · needs a key' : ' · local, no key'}`
            : 'No preset: set a base URL below. Generations are tagged openai-compatible, which has no rate card — cost reads as uncosted, never as free.'}
        </span>
      </label>

      <label className="backend-field">
        <span className="smallcaps backend-field__label">Base URL</span>
        <input
          type="text"
          className="paths-row__input"
          value={baseUrl}
          placeholder={presetInfo ? presetInfo.base_url : 'https://…/v1'}
          onChange={(e) => setBaseUrl(e.target.value)}
          data-testid="openai-base-url"
        />
        <span className="italic-fell backend-field__hint">
          {presetInfo
            ? 'Leave blank to use the preset’s endpoint.'
            : 'Required without a preset.'}
        </span>
      </label>

      <label className="backend-field">
        <span className="smallcaps backend-field__label">Model</span>
        <input
          type="text"
          className="paths-row__input"
          value={model}
          placeholder="e.g. deepseek-reasoner, qwen3:14b"
          onChange={(e) => setModel(e.target.value)}
          data-testid="openai-model"
        />
        <span className="italic-fell backend-field__hint">
          Required — the endpoint decides which models exist, so there is no
          default to fall back on.
        </span>
      </label>

      <button
        type="button"
        className="btn btn--primary"
        disabled={isPending}
        onClick={() =>
          onSave({
            backend: 'openai-compatible',
            openai_preset: preset,
            openai_base_url: baseUrl.trim(),
            openai_model: model.trim(),
          })
        }
        data-testid="openai-save"
      >
        {isPending ? 'Saving…' : 'Save endpoint'}
      </button>

      <KeyField
        label="API key"
        hint={
          keyRequired ? (
            <>
              Required by this preset. Write-only; falls back to{' '}
              <code>CHRONICLER_OPENAI_API_KEY</code> then{' '}
              <code>OPENAI_API_KEY</code>.
            </>
          ) : (
            <>Local endpoints need none. Write-only if yours does.</>
          )
        }
        state={data.openai_key}
        testId="openai-key"
        isPending={isPending}
        onSave={(value) => onSave({ openai_api_key: value })}
      />
    </div>
  );
}

interface KeyFieldProps {
  label: string;
  hint: React.ReactNode;
  state: BackendKeyState;
  testId: string;
  isPending: boolean;
  onSave: (value: string) => void;
}

/** A write-only secret field.
 *
 * There is no masked prefill to preserve: the value never leaves the
 * server, so the input starts empty and submits only when the user has
 * typed something. "Forget the stored key" is the deliberate clear —
 * it sends an empty string, which is the backend's clear signal.
 */
function KeyField({
  label,
  hint,
  state,
  testId,
  isPending,
  onSave,
}: KeyFieldProps): React.JSX.Element {
  const [draft, setDraft] = useState('');

  const submit = (value: string): void => {
    onSave(value);
    setDraft('');
  };

  return (
    <div className="backend-field" data-testid={`${testId}-field`}>
      <span className="smallcaps backend-field__label">{label}</span>
      <div className="backend-field__key-row">
        <span
          className={state.present ? 'pip' : 'pip pip--warn'}
          data-testid={`${testId}-pip`}
        >
          <span
            className={
              state.present ? 'pip__dot pip__dot--alive' : 'pip__dot pip__dot--warn'
            }
          />{' '}
          {state.present
            ? `Stored (${state.source === 'settings' ? 'this app' : 'env var'})`
            : 'No key'}
        </span>
      </div>
      <input
        type="password"
        className="paths-row__input"
        value={draft}
        placeholder={state.present ? 'Replace the stored key…' : 'Paste a key…'}
        autoComplete="off"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && draft.trim() !== '') submit(draft.trim());
        }}
        data-testid={`${testId}-input`}
      />
      <span className="italic-fell backend-field__hint">{hint}</span>
      <div className="paths-row__actions">
        <button
          type="button"
          className="btn btn--ghost"
          disabled={isPending || draft.trim() === ''}
          onClick={() => submit(draft.trim())}
          data-testid={`${testId}-save`}
        >
          Save key
        </button>
        {state.present && state.source === 'settings' && (
          <button
            type="button"
            className="btn btn--ghost"
            disabled={isPending}
            onClick={() => submit('')}
            title="Delete the stored key; fall back to the env var"
            data-testid={`${testId}-forget`}
          >
            Forget stored key
          </button>
        )}
      </div>
    </div>
  );
}

function sourceLabel(source: string): string {
  if (source === 'settings') return 'this app’s settings';
  if (source === 'env') return 'the CHRONICLER_NARRATIVE_BACKEND env var';
  return 'the default';
}
