// THE one place that answers "may this animate?". Both halves of the app read it:
// CSS honours `prefers-reduced-motion` through a global block in globals.css, and
// anything driven from JS (anime.js) has to ask here, because a media query in a
// stylesheet cannot stop a script from tweening.
//
// Split into a plain predicate and a hook so non-React code and tests can ask
// without rendering anything, and so the hook only exists to keep a component in
// sync when the setting changes mid-session (people do toggle it, and a tree that
// is mid-draw when they do should stop).

import { useEffect, useState } from 'react';

export const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

/** True when the OS asks for reduced motion. Safe before the DOM exists (SSR, unit
 *  tests with a bare environment): no matchMedia means we cannot know, and the
 *  honest default for "cannot know" is to animate, matching CSS. */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

/** The same answer, kept current if the setting changes while the app is open. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }
    const mq = window.matchMedia(REDUCED_MOTION_QUERY);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    // addEventListener is the modern spelling; addListener is kept as the fallback
    // because some older WebKit builds only have that one, and a thrown TypeError
    // here would take the whole page down rather than just lose the live update.
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    }
    if (typeof mq.addListener === 'function') {
      mq.addListener(onChange);
      return () => mq.removeListener(onChange);
    }
    return;
  }, []);

  return reduced;
}
