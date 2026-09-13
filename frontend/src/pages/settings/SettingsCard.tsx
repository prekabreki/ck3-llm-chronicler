// Audit F-28: shared SettingsCard frame + the section nav. Used by
// every concrete card module under ./settings/.

import type React from 'react';

interface SettingsCardProps {
  id: string;
  title: string;
  description: string;
  stub?: boolean;
  stubHint?: string;
  children?: React.ReactNode;
}

export function SettingsCard({
  id,
  title,
  description,
  stub,
  stubHint,
  children,
}: SettingsCardProps): React.JSX.Element {
  return (
    <section id={id} className="settings-card">
      <div className="settings-card__head">
        <h2 className="settings-card__title">{title}</h2>
        <p className="italic-fell settings-card__description">{description}</p>
      </div>
      {stub ? (
        <div className="settings-card__stub italic-fell">
          {stubHint ?? 'Coming soon.'}
        </div>
      ) : (
        <div className="settings-card__body">{children}</div>
      )}
    </section>
  );
}

const SETTINGS_NAV: { id: string; label: string }[] = [
  { id: 'paths', label: 'Paths & saves' },
  { id: 'heraldry', label: 'Heraldry pipeline' },
  { id: 'provider', label: 'Provider & LLM' },
  { id: 'cost', label: 'Cost guardrail' },
  { id: 'migrations', label: 'Migrations' },
  { id: 'keyboard', label: 'Keyboard' },
  { id: 'advanced', label: 'Advanced' },
];

export function SettingsNav(): React.JSX.Element {
  return (
    <aside className="settings-nav" aria-label="Settings sections">
      <div className="smallcaps settings-nav__eyebrow">Sections</div>
      <ul className="settings-nav__list">
        {SETTINGS_NAV.map((s) => (
          <li key={s.id}>
            <a className="settings-nav__link" href={`#${s.id}`}>
              {s.label}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
}
