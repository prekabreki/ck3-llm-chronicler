// FirstRunWizard — ck3_chronicler-kze6 (f9w.3) / supersedes vysp.15.
//
// Auto-opens on a fresh install (empty Library, no settings.json
// overrides, no heraldry assets, wizard not yet dismissed). Walks the
// user through:
//   1. Save dir
//   2. CK3 install dir
//   3. Heraldry extract (uses the f9w.2 SSE wire)
//   4. Chronicle directory + backend (issue #23) — the step a public user
//      cannot skip: without a scaffolded chronicle dir there is nothing for
//      the backend to write against, and cloning the maintainer's private
//      prose repo (the pre-#20 setup) was never possible for them.
//   5. "Adopt your first save" closing card
//
// A dismiss action ("I'll do this myself") writes wizard_dismissed_at
// so the wizard does not re-prompt on subsequent loads.

import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { updatePathsSettings } from '../api/client';
import {
  queryKeys,
  useDismissFirstRunWizard,
  useFirstRunStatus,
  useHeraldryStatus,
  useInitProseRepo,
  useNarrativeBackend,
  usePathsSettings,
  useProseRepoStatus,
} from '../api/queries';
import { useHeraldryExtract } from '../api/useHeraldryExtract';
import { useModalA11y } from './useModalA11y';

const STEP_LABELS = [
  'Save dir',
  'CK3 install',
  'Extract heraldry',
  'Chronicle',
  'Adopt save',
];

const LAST_STEP = STEP_LABELS.length - 1;

export function FirstRunWizard(): React.JSX.Element | null {
  const statusQ = useFirstRunStatus();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);

  // Auto-open on first successful detection of needs_wizard=true. Only
  // ever triggers once — after the user closes (by dismissing or
  // completing) we don't re-open from a query refetch. Adjust during
  // render rather than in an effect so the modal opens in the same
  // commit the status resolves.
  const [autoOpened, setAutoOpened] = useState(false);
  if (!autoOpened && statusQ.data?.needs_wizard) {
    setAutoOpened(true);
    setOpen(true);
  }

  if (!open) return null;

  return (
    <WizardShell
      step={step}
      onStepChange={setStep}
      onClose={() => setOpen(false)}
    />
  );
}

interface WizardShellProps {
  step: number;
  onStepChange: (s: number) => void;
  onClose: () => void;
}

function WizardShell({
  step,
  onStepChange,
  onClose,
}: WizardShellProps): React.JSX.Element {
  const queryClient = useQueryClient();
  const dismissMu = useDismissFirstRunWizard();
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const dismiss = (): void => {
    dismissMu.mutate(undefined, {
      onSuccess: () => {
        void queryClient.invalidateQueries({
          queryKey: queryKeys.firstRunStatus(),
        });
        onClose();
      },
      // If the dismiss POST fails (rare), still close the modal so the
      // user isn't trapped — the wizard can show again next session.
      onError: () => onClose(),
    });
  };

  // audit F-14 / ck3_chronicler-jdkr: focus-trap, Escape-dismiss,
  // restore-focus on close. Without this the first-run user could
  // tab out of the wizard into AppShell tabs.
  useModalA11y({ open: true, onClose: dismiss, dialogRef });

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="First-run setup"
      ref={dialogRef}
      tabIndex={-1}
    >
      <div className="modal modal--720">
        <header className="modal__head">
          <h2 className="uncial">Welcome to chronicler</h2>
          <button
            type="button"
            className="modal__close"
            onClick={dismiss}
            aria-label="Skip the first-run wizard"
          >
            ×
          </button>
        </header>

        <ol className="wizard-steps" aria-label="Setup steps">
          {STEP_LABELS.map((label, i) => (
            <li
              key={label}
              className={
                'wizard-steps__item' +
                (i === step ? ' wizard-steps__item--active' : '') +
                (i < step ? ' wizard-steps__item--done' : '')
              }
            >
              <span className="wizard-steps__num">{i + 1}</span>
              <span className="wizard-steps__label">{label}</span>
            </li>
          ))}
        </ol>

        <div className="modal__body">
          {step === 0 && <SaveDirStep />}
          {step === 1 && <InstallDirStep />}
          {step === 2 && <HeraldryStep />}
          {step === 3 && <ChronicleStep />}
          {step === 4 && <AdoptSaveStep />}
        </div>

        <footer className="modal__foot">
          <button
            type="button"
            className="btn btn--quiet"
            onClick={dismiss}
          >
            I'll do this myself
          </button>
          <span style={{ flex: 1 }} />
          {step > 0 && (
            <button
              type="button"
              className="btn btn--quiet"
              onClick={() => onStepChange(step - 1)}
            >
              Back
            </button>
          )}
          {step < LAST_STEP ? (
            <button
              type="button"
              className="btn"
              onClick={() => onStepChange(step + 1)}
            >
              Next
            </button>
          ) : (
            <button type="button" className="btn" onClick={dismiss}>
              Done
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

function SaveDirStep(): React.JSX.Element {
  const pathsQ = usePathsSettings();
  const [override, setOverride] = useState('');

  const queryClient = useQueryClient();
  const saveMu = useMutation({
    mutationFn: (value: string | null) =>
      updatePathsSettings({ save_dir: value }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.pathsSettings() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.firstRunStatus() });
      setOverride('');
    },
  });

  const current = pathsQ.data?.save_dir;
  return (
    <div className="wizard-step">
      <p className="italic-fell">
        Where CK3 writes its save files. By default that's
        <code> ~/Documents/Paradox Interactive/Crusader Kings III/save games</code>.
      </p>
      <p>
        <strong>Resolved:</strong>{' '}
        <code>{current?.resolved || '(none)'}</code>{' '}
        <span className="smallcaps">({current?.source ?? '—'})</span>
        {current?.exists === false && (
          <span style={{ color: 'var(--color-warn, #7a3a3a)' }}> · doesn't exist</span>
        )}
      </p>
      <label className="wizard-step__label">
        Override
        <input
          type="text"
          placeholder="Leave blank to use default"
          value={override}
          onChange={(e) => setOverride(e.target.value)}
          className="wizard-step__input"
        />
      </label>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button
          type="button"
          className="btn btn--quiet"
          onClick={() => saveMu.mutate(override || null)}
          disabled={saveMu.isPending}
        >
          {saveMu.isPending ? 'Saving…' : 'Save override'}
        </button>
      </div>
    </div>
  );
}

function InstallDirStep(): React.JSX.Element {
  const pathsQ = usePathsSettings();
  const [override, setOverride] = useState('');

  const queryClient = useQueryClient();
  const saveMu = useMutation({
    mutationFn: (value: string | null) =>
      updatePathsSettings({ ck3_install_dir: value }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.pathsSettings() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.firstRunStatus() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.heraldryStatus() });
      setOverride('');
    },
  });

  const current = pathsQ.data?.ck3_install_dir;
  return (
    <div className="wizard-step">
      <p className="italic-fell">
        Where CK3 is installed (the Steam library directory). Used to
        extract heraldry textures so your character shields render with
        their actual in-game arms.
      </p>
      <p>
        <strong>Resolved:</strong>{' '}
        <code>{current?.resolved || '(not found)'}</code>{' '}
        <span className="smallcaps">({current?.source ?? '—'})</span>
        {current?.exists === false && (
          <span style={{ color: 'var(--color-warn, #7a3a3a)' }}> · doesn't exist</span>
        )}
      </p>
      <label className="wizard-step__label">
        Override
        <input
          type="text"
          placeholder='e.g. C:\Program Files (x86)\Steam\steamapps\common\Crusader Kings III'
          value={override}
          onChange={(e) => setOverride(e.target.value)}
          className="wizard-step__input"
        />
      </label>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button
          type="button"
          className="btn btn--quiet"
          onClick={() => saveMu.mutate(override || null)}
          disabled={saveMu.isPending}
        >
          {saveMu.isPending ? 'Saving…' : 'Save override'}
        </button>
      </div>
    </div>
  );
}

function HeraldryStep(): React.JSX.Element {
  const heraldryQ = useHeraldryStatus();
  const queryClient = useQueryClient();
  // M-F8 (27ov.62): the SSE lifecycle lives in api/useHeraldryExtract
  // (this copy had already drifted once — F-40 dropped the 'started'
  // frame). The wizard additionally invalidates firstRunStatus so the
  // step list re-evaluates once assets exist.
  const { progress, run } = useHeraldryExtract({
    onDone: () => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.firstRunStatus(),
      });
    },
  });

  const fraction = progress.active
    ? progress.total > 0
      ? (progress.current / progress.total) * 100
      : 0
    : progress.message !== null
      ? 100
      : 0;
  const caption = progress.active
    ? progress.group
      ? `${progress.group} · ${progress.current}/${progress.total}`
      : (progress.message ?? 'Starting…')
    : (progress.error ?? progress.message ?? 'Idle');

  const installMissing = heraldryQ.data?.ck3_install_dir_exists === false;

  return (
    <div className="wizard-step">
      <p className="italic-fell">
        Convert CK3's coat-of-arms textures so your character shields
        render with their real in-game arms instead of placeholder
        banners.
      </p>
      {heraldryQ.data?.extracted ? (
        <p>
          <strong>Already extracted</strong> · {heraldryQ.data.patterns_count} patterns
          · {heraldryQ.data.emblems_count} emblems · {heraldryQ.data.palette_colors}{' '}
          palette colours
        </p>
      ) : (
        <p>Not extracted yet.</p>
      )}
      <div className="heraldry-progress">
        <div className="heraldry-progress__bar" aria-hidden>
          <i style={{ width: `${fraction}%` }} />
        </div>
        <div className="heraldry-progress__caption">
          <span>{caption}</span>
        </div>
      </div>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button
          type="button"
          className="btn"
          onClick={() => run(false)}
          disabled={progress.active || installMissing}
        >
          {progress.active
            ? 'Extracting…'
            : heraldryQ.data?.extracted
            ? 'Re-extract'
            : 'Run extract'}
        </button>
        {installMissing && (
          <span className="italic-fell" style={{ color: 'var(--color-warn, #7a3a3a)' }}>
            Set the CK3 install dir on the previous step first.
          </span>
        )}
      </div>
    </div>
  );
}

// Issue #23: the chronicle directory + which backend writes into it.
//
// Deliberately not a copy of the Settings cards: the wizard's job is to get
// a working default in place in one click, so this offers Initialize plus a
// read of the current backend, and sends anyone who wants a different
// backend or a different path to Settings, which owns those forms.
function ChronicleStep(): React.JSX.Element {
  const proseQ = useProseRepoStatus();
  const backendQ = useNarrativeBackend();
  const initMu = useInitProseRepo();
  const prose = proseQ.data ?? null;
  const ready = prose !== null && prose.exists && prose.claude_md_present;

  return (
    <div className="wizard-step">
      <p className="italic-fell">
        Chronicles are written into a directory of your own — a{' '}
        <code>CLAUDE.md</code> that reshapes the assistant into a chronicler,
        the voice guides, and one folder per campaign. Chronicler ships the
        template; this scaffolds a copy you own.
      </p>
      <p>
        <strong>Chronicle directory:</strong>{' '}
        <code>{prose?.path || '(unresolved)'}</code>{' '}
        <span className="smallcaps">({prose?.source ?? '—'})</span>
      </p>
      {ready ? (
        <p data-testid="wizard-prose-ready">
          <strong>Ready</strong> · template in place
          {prose.git_initialized ? ' · git initialised' : ' · not a git repo yet'}
        </p>
      ) : (
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <button
            type="button"
            className="btn"
            onClick={() => initMu.mutate({})}
            disabled={initMu.isPending}
            data-testid="wizard-prose-init"
          >
            {initMu.isPending ? 'Scaffolding…' : 'Initialize chronicle directory'}
          </button>
          <span className="italic-fell">
            Same as <code>chronicler init-prose</code>.
          </span>
        </div>
      )}
      {initMu.isError && (
        <p
          style={{ color: 'var(--color-warn, #7a3a3a)' }}
          data-testid="wizard-prose-init-error"
        >
          {String(initMu.error)}
        </p>
      )}
      <p className="italic-fell">
        <strong>Backend:</strong> <code>{backendQ.data?.backend ?? '—'}</code>.{' '}
        {backendQ.data?.backend === 'claude-code'
          ? 'The default shells out to the claude CLI and needs no API key. No Claude subscription? Pick the Anthropic or OpenAI-compatible backend under Settings → Provider & LLM.'
          : 'Change it under Settings → Provider & LLM.'}
      </p>
    </div>
  );
}

function AdoptSaveStep(): React.JSX.Element {
  return (
    <div className="wizard-step">
      <p className="italic-fell">
        That's the setup done. Open CK3, play through to your next
        autosave, and chronicler will spot it on the watcher and ask
        whether you'd like to adopt it as your first campaign.
      </p>
      <p>
        Power user? You can also paste a save path manually via the{' '}
        <strong>+ Adopt save</strong> button on the Library page.
      </p>
      <p className="italic-fell">
        Click <strong>Done</strong> to close this wizard. It won't
        re-open unless you delete <code>~/Documents/chronicler/settings.json</code>.
      </p>
    </div>
  );
}
