// Audit F-27: pure-decoration components extracted from ChroniclePage.
// Stateless and shared across panels — kept in one file to avoid the
// per-component overhead of separate .tsx stubs. The legacy
// `CornerOrnament` that used to live here was replaced by the polish-
// pass v3 `CornerOrnamentFrame` (Ornaments.tsx in components/).

import type React from 'react';

export function Fleuron(): React.JSX.Element {
  return (
    <div className="fleuron chronicle-page__fleuron">
      <span className="fleuron__glyph">✦  ✦  ✦</span>
    </div>
  );
}

export function SmallcapsLabel({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  return <div className="smallcaps chronicle-page__section-label">❧ {children}</div>;
}
