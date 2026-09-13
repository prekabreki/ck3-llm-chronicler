// ck3_chronicler-tbrm.4: Settings card for the chronicle directory — the
// prose repo whose CLAUDE.md reshapes the assistant into a chronicler, plus
// three readiness pips (exists / git initialized / CLAUDE.md present).
//
// Issue #23 replaced two things that had gone stale here. The subtitle
// hardcoded "ClaudeCodeProvider · claude --print", which is wrong on two of
// the three backends; it now reads the live backend. And the card assumed
// the directory already existed — a public user has no prose repo at all,
// so a missing one now offers Initialize, which runs the same scaffold as
// `chronicler init-prose`.

import {
  useInitProseRepo,
  useNarrativeBackend,
  useProseRepoStatus,
  useProviderStatus,
  useUpdateProseRepoSettings,
} from '../../api/queries';
import { EditablePathRow } from './EditablePathRow';
import type {
  NarrativeBackendResponse,
  ProseRepoStatusResponse,
} from '../../api/types';

const BACKEND_SUBTITLES: Record<string, string> = {
  'claude-code': 'claude-code · claude --print',
  anthropic: 'anthropic · Messages API',
  'openai-compatible': 'openai-compatible · chat completions',
};

export function ProseRepoCard(): React.JSX.Element {
  const statusQ = useProseRepoStatus();
  const providerQ = useProviderStatus();
  const backendQ = useNarrativeBackend();
  const data = statusQ.data ?? null;
  const provider = providerQ.data ?? null;
  const backend = backendQ.data ?? null;

  return (
    <article
      className="paper paper--edged provider-card provider-card--active"
      data-testid="prose-repo-card"
    >
      <span className="provider-card__active-pill">Active</span>
      <div className="provider-card__head">
        <span className="provider-card__glyph">✦</span>
        <div>
          <div className="uncial provider-card__title">The Prose Repo</div>
          <div
            className="smallcaps provider-card__sub"
            data-testid="prose-repo-backend-sub"
          >
            {subtitleFor(backend)}
          </div>
        </div>
      </div>
      {statusQ.isLoading && (
        <p className="italic-fell provider-card__locked-note">Reading…</p>
      )}
      {statusQ.isError && (
        <p className="italic-fell provider-card__locked-note">
          Could not load prose-repo status. {String(statusQ.error)}
        </p>
      )}
      {data && (
        <>
          <ReadinessRow data={data} />
          <ProseRepoInitRow data={data} />
          <ProseRepoPathRow data={data} />
          <dl className="provider-card__specs provider-card__specs--simple">
            <div className="provider-card__spec provider-card__spec--first">
              <dt className="smallcaps provider-card__spec-key">Model</dt>
              <dd className="provider-card__spec-value">
                {provider?.model ?? '—'}
                <span className="italic-fell provider-card__toggle-hint">
                  {' '}
                  the running provider; change it under The engine
                </span>
              </dd>
            </div>
            <div className="provider-card__spec">
              <dt className="smallcaps provider-card__spec-key">Bio · avg</dt>
              <dd className="provider-card__spec-value">
                {provider?.avg_biography_ms != null
                  ? `${(provider.avg_biography_ms / 1000).toFixed(1)} s`
                  : '—'}
              </dd>
            </div>
            <div className="provider-card__spec">
              <dt className="smallcaps provider-card__spec-key">Recent</dt>
              <dd className="provider-card__spec-value">
                {provider
                  ? `${provider.recent_biographies} bio`
                  : '—'}
              </dd>
            </div>
          </dl>
        </>
      )}
    </article>
  );
}

function subtitleFor(backend: NarrativeBackendResponse | null): string {
  if (backend === null) return 'reading backend…';
  const base = BACKEND_SUBTITLES[backend.backend] ?? backend.backend;
  if (backend.backend !== 'openai-compatible') return base;
  const target = backend.openai_preset ?? backend.openai_base_url;
  return target !== null && target !== '' ? `${base} · ${target}` : base;
}

function ReadinessRow({ data }: { data: ProseRepoStatusResponse }): React.JSX.Element {
  return (
    <div className="prose-repo__pips" role="group" aria-label="Prose repo readiness">
      <Pip
        label="On disk"
        ok={data.exists}
        hint={data.exists ? 'Directory found on disk.' : 'Directory not found at the resolved path.'}
      />
      <Pip
        label="Git initialised"
        ok={data.git_initialized}
        hint={
          data.git_initialized
            ? 'A .git/ subdirectory is present — biographies will commit cleanly.'
            : 'No .git/ — run `git init` in the prose repo so biographies version cleanly.'
        }
      />
      <Pip
        label="CLAUDE.md present"
        ok={data.claude_md_present}
        hint={
          data.claude_md_present
            ? 'The role-reshape file is in place. Biographies will run with the chronicler framing.'
            : 'CLAUDE.md is missing — claude --print will run as a generic assistant, not as the chronicler.'
        }
      />
    </div>
  );
}

interface PipProps {
  label: string;
  ok: boolean;
  hint: string;
}

function Pip({ label, ok, hint }: PipProps): React.JSX.Element {
  const cls = ok
    ? 'pip'
    : 'pip pip--warn';
  const dotCls = ok ? 'pip__dot pip__dot--alive' : 'pip__dot pip__dot--warn';
  return (
    <span className={cls} title={hint}>
      <span className={dotCls} /> {label}
    </span>
  );
}

/** Issue #23: the Initialize row.
 *
 * Shown while the resolved path is not a working chronicle directory —
 * missing entirely, or present without the CLAUDE.md that makes it one.
 * On success the mutation writes the post-scaffold status into the cache,
 * so this row disappears and the pips go green in the same render.
 *
 * The scaffold's own notes are rendered verbatim: they are the only place
 * a *partial* success shows up (files copied, git unavailable), and a
 * failure surfaces here as text rather than as a stuck button.
 */
function ProseRepoInitRow({
  data,
}: {
  data: ProseRepoStatusResponse;
}): React.JSX.Element | null {
  const initMutation = useInitProseRepo();
  const ready = data.exists && data.claude_md_present;

  if (ready && !initMutation.isSuccess) return null;

  return (
    <div className="prose-repo__init" data-testid="prose-repo-init">
      {!ready && (
        <>
          <p className="italic-fell provider-card__toggle-hint">
            {data.exists
              ? 'This directory has no CLAUDE.md, so generations would run as a generic assistant. Scaffold the chronicle template into it.'
              : 'No chronicle directory yet. Scaffold one from the bundled template — the same thing `chronicler init-prose` does — and its path is recorded for you.'}
          </p>
          <button
            type="button"
            className="btn btn--primary"
            disabled={initMutation.isPending}
            onClick={() => initMutation.mutate({})}
            data-testid="prose-repo-init-button"
          >
            {initMutation.isPending
              ? 'Scaffolding…'
              : `Initialize ${data.path || 'the chronicle directory'}`}
          </button>
        </>
      )}

      {initMutation.isError && (
        <p
          className="italic-fell paths-row__error"
          data-testid="prose-repo-init-error"
        >
          Initialize failed: {String(initMutation.error)}
        </p>
      )}

      {initMutation.isSuccess && (
        <ul className="prose-repo__init-notes" data-testid="prose-repo-init-notes">
          {initMutation.data.notes.map((note) => (
            <li key={note} className="italic-fell">
              {note}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ProseRepoPathRow({ data }: { data: ProseRepoStatusResponse }): React.JSX.Element {
  const updateMutation = useUpdateProseRepoSettings();

  return (
    <EditablePathRow
      label="Prose repo path"
      hint={
        <>
          The chronicle directory: the role-reshape CLAUDE.md, the voice
          guides, and per-campaign briefings. Override via the{' '}
          <code>CHRONICLER_PROSE_REPO_PATH</code> env var or set it here.
        </>
      }
      source={data.source}
      resolved={data.path}
      override={data.override}
      emptyText="— not set —"
      resetTitle="Clear override; fall back to env / default"
      isPending={updateMutation.isPending}
      errorText={updateMutation.isError ? String(updateMutation.error) : null}
      onSubmit={(value) => updateMutation.mutateAsync({ path: value })}
    />
  );
}
