// Audit F-28: heraldry pipeline card — extract status + run + SSE
// progress bar (audit F-44 typed frames). Extracted from SettingsPage.
// M-F8 (27ov.62): the SSE lifecycle lives in api/useHeraldryExtract —
// this card only renders the returned state.

import { useHeraldryStatus } from '../../api/queries';
import { useHeraldryExtract } from '../../api/useHeraldryExtract';

export function HeraldryPipelineCard(): React.JSX.Element {
  const statusQ = useHeraldryStatus();
  const { progress, run, isStarting } = useHeraldryExtract();

  const status = statusQ.data ?? null;
  const installMissing =
    status !== null && status.ck3_install_dir_exists === false;
  const formatLastExtract = (iso: string | null): string => {
    if (!iso) return 'never';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  };

  const stats = status
    ? [
        { n: status.patterns_count.toLocaleString(), k: 'Patterns' },
        { n: status.emblems_count.toLocaleString(), k: 'Emblems' },
        { n: status.palette_colors.toLocaleString(), k: 'Palette colours' },
        { n: status.is_stale ? 'Stale' : status.extracted ? 'Fresh' : 'Empty', k: 'State' },
      ]
    : [
        { n: '—', k: 'Patterns' },
        { n: '—', k: 'Emblems' },
        { n: '—', k: 'Palette colours' },
        { n: '—', k: 'State' },
      ];

  const fraction =
    progress.total > 0
      ? Math.min(100, Math.round((progress.current / progress.total) * 100))
      : progress.active
      ? 0
      : 100;

  const captionText = progress.active
    ? `${progress.group ?? 'extracting'} · ${progress.current}/${progress.total}`
    : progress.error
    ? progress.error
    : progress.message
    ? progress.message
    : status?.extracted
    ? `Last extraction · ${formatLastExtract(status.last_extraction_at)}`
    : 'Not extracted yet';

  const pipDot = progress.error
    ? 'pip__dot--warn'
    : progress.active
    ? 'pip__dot--pending'
    : status?.is_stale
    ? 'pip__dot--warn'
    : status?.extracted
    ? 'pip__dot--alive'
    : 'pip__dot--warn';

  const pipLabel = progress.error
    ? 'Failed'
    : progress.active
    ? 'Running…'
    : status?.is_stale
    ? 'Stale (CK3 patched)'
    : status?.extracted
    ? 'Healthy'
    : 'Not extracted';

  return (
    <section id="heraldry" className="settings-card">
      <div className="settings-card__head">
        <h2 className="settings-card__title">Heraldry pipeline</h2>
        <p className="italic-fell settings-card__description">
          Real CK3 coat-of-arms textures live under your chronicler data
          dir. Re-run after a CK3 patch — when the source dir is newer
          than the last extraction, this card flips to <em>stale</em>.
        </p>
      </div>
      <div className="settings-card__body">
        <div className="heraldry-strip">
          {stats.map((s) => (
            <div key={s.k} className="heraldry-strip__cell">
              <div className="heraldry-strip__n">{s.n}</div>
              <div className="heraldry-strip__k">{s.k}</div>
            </div>
          ))}
        </div>
        <div className="heraldry-progress">
          <div className="heraldry-progress__bar" aria-hidden>
            <i style={{ width: `${fraction}%` }} />
          </div>
          <div className="heraldry-progress__caption">
            <span>{captionText}</span>
            <span className="pip">
              <span className={`pip__dot ${pipDot}`} /> {pipLabel}
            </span>
          </div>
        </div>
        <div
          style={{
            display: 'flex',
            gap: '0.5rem',
            marginTop: '0.75rem',
            alignItems: 'center',
          }}
        >
          <button
            type="button"
            className="btn"
            onClick={() => run(false)}
            disabled={progress.active || installMissing || isStarting}
          >
            {progress.active ? 'Extracting…' : status?.extracted ? 'Re-extract' : 'Run extract'}
          </button>
          {status?.is_stale && !progress.active && (
            <button
              type="button"
              className="btn btn--quiet"
              onClick={() => run(true)}
              disabled={installMissing}
              title="Force re-extract every asset, ignoring the idempotent cache"
            >
              Force re-extract
            </button>
          )}
          {installMissing && (
            <span className="italic-fell" style={{ color: 'var(--color-warn, #7a3a3a)' }}>
              CK3 install dir not configured — set it via Paths above.
            </span>
          )}
        </div>
      </div>
    </section>
  );
}
