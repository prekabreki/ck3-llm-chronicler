// ck3_chronicler-yrv3: in-app Logs tab. Streams chronicler's own
// Python log output via /api/sse/logs, with a cold-load backfill via
// /api/logs/recent + an auto-scroll-with-pause viewer.
//
// Hand-rolled virtualisation (no react-window dep): renders only the
// window the user can see plus a small over-render buffer. Scroll
// container height is preserved via top/bottom spacers so scrollbar
// position tracks reality.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  getKnownLoggers,
  getRecentLogs,
  haltChronicler,
} from '../api/client';
import { seedLogStream, useLogStream } from '../api/useLogStream';
import type { LogEnvelope, LogLevel } from '../api/types';

const LEVELS: LogLevel[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

const ROW_HEIGHT_PX = 22;
const OVERSCAN = 20;

export function LogsPage(): React.JSX.Element {
  const stream = useLogStream();
  const [minLevel, setMinLevel] = useState<LogLevel>('INFO');
  const [loggerFilter, setLoggerFilter] = useState<string>('');
  const [search, setSearch] = useState<string>('');
  const [knownLoggers, setKnownLoggers] = useState<string[]>([]);

  // Cold-load: seed the channel with the most recent envelopes so the
  // page paints with content even before the first SSE frame arrives.
  // Refresh the per-logger list on the same tick.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [recent, loggers] = await Promise.all([
          getRecentLogs({ limit: 500 }),
          getKnownLoggers(),
        ]);
        if (cancelled) return;
        seedLogStream(recent);
        setKnownLoggers(loggers);
      } catch {
        // Logs are observability — never fatal. Empty page is
        // acceptable if the backend isn't up yet.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Refresh the known-loggers list when new envelopes arrive — newly
  // emitting loggers should appear in the filter dropdown without a
  // page reload. Throttle to once per second so the SSE fire-hose
  // doesn't trigger one fetch per envelope.
  useEffect(() => {
    if (stream.envelopes.length === 0) return;
    const t = window.setTimeout(() => {
      void getKnownLoggers().then(setKnownLoggers).catch(() => undefined);
    }, 1_000);
    return () => window.clearTimeout(t);
  }, [stream.lastSeenSeq, stream.envelopes.length]);

  const filtered = useMemo(() => {
    const minIdx = LEVELS.indexOf(minLevel);
    const needle = search.trim().toLowerCase();
    return stream.envelopes.filter((e) => {
      if (LEVELS.indexOf(e.level) < minIdx) return false;
      if (loggerFilter && !e.logger.startsWith(loggerFilter)) return false;
      if (needle && !e.message.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [stream.envelopes, minLevel, loggerFilter, search]);

  const onCopy = useCallback(() => {
    const text = filtered.map(formatLine).join('\n');
    void navigator.clipboard?.writeText(text);
  }, [filtered]);

  const onDownload = useCallback(() => {
    const blob = new Blob([filtered.map(formatLine).join('\n')], {
      type: 'text/plain',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chronicler-${new Date().toISOString().replace(/[:.]/g, '-')}.log`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [filtered]);

  // ck3_chronicler-ghfi: shut down the backend process from the SPA.
  // Confirms first since this is destructive (any in-flight biography
  // generation is lost). The backend returns 202 then exits ~400 ms
  // later; we close the browser tab right after the confirm so the
  // chromeless --app window goes away alongside chronicler.
  const onHalt = useCallback(() => {
    const ok = window.confirm(
      'Stop chronicler?\n\n' +
        'This shuts down the backend process (uvicorn + save-tail). ' +
        'Any in-flight biography generation is lost. ' +
        'You can restart from the Start Menu shortcut.',
    );
    if (!ok) return;
    void haltChronicler()
      .catch(() => undefined)
      .finally(() => {
        // window.close() only works for windows opened by script — the
        // chromeless --app browser counts as one. In a regular tab it's
        // a no-op, which is acceptable: the user can close manually.
        window.close();
      });
  }, []);

  return (
    <div className="logs-page">
      <div className="logs-page__inner">
        <header className="logs-page__header">
          <h1 className="uncial logs-page__title">Logs</h1>
          <div className="logs-page__status">
            <StatusPip dead={stream.dead} connected={stream.connected} />
            <span className="logs-page__count">
              {filtered.length} / {stream.envelopes.length} lines
            </span>
          </div>
        </header>

        <div className="logs-page__toolbar">
          <label className="logs-page__field">
            <span className="smallcaps">Level</span>
            <select
              value={minLevel}
              onChange={(e) => setMinLevel(e.target.value as LogLevel)}
            >
              {LEVELS.map((lv) => (
                <option key={lv} value={lv}>
                  {lv}+
                </option>
              ))}
            </select>
          </label>

          <label className="logs-page__field">
            <span className="smallcaps">Logger</span>
            <select
              value={loggerFilter}
              onChange={(e) => setLoggerFilter(e.target.value)}
            >
              <option value="">(all)</option>
              {knownLoggers.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>

          <label className="logs-page__field logs-page__field--grow">
            <span className="smallcaps">Search</span>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="filter messages…"
            />
          </label>

          <button
            type="button"
            className="logs-page__action"
            onClick={onCopy}
            disabled={filtered.length === 0}
          >
            Copy
          </button>
          <button
            type="button"
            className="logs-page__action"
            onClick={onDownload}
            disabled={filtered.length === 0}
          >
            Download .log
          </button>
          <button
            type="button"
            className="logs-page__action logs-page__action--danger"
            onClick={onHalt}
            title="Shut down the chronicler backend process"
          >
            Halt backend
          </button>
        </div>

        <LogsViewport envelopes={filtered} />
      </div>
    </div>
  );
}

function StatusPip({
  dead,
  connected,
}: {
  dead: boolean;
  connected: boolean;
}): React.JSX.Element {
  const label = dead ? 'Stream unavailable' : connected ? 'Live' : 'Connecting…';
  const cls = dead
    ? 'logs-page__pip logs-page__pip--dead'
    : connected
      ? 'logs-page__pip logs-page__pip--live'
      : 'logs-page__pip logs-page__pip--idle';
  return (
    <span className={cls}>
      <span className="logs-page__pip-dot" aria-hidden />
      {label}
    </span>
  );
}

interface ViewportProps {
  envelopes: LogEnvelope[];
}

function LogsViewport({ envelopes }: ViewportProps): React.JSX.Element {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [viewportHeight, setViewportHeight] = useState(400);
  const [scrollTop, setScrollTop] = useState(0);
  const [followTail, setFollowTail] = useState(true);

  // Watch the scroll container's height so the visible window updates
  // when the viewport resizes (window resize, devtools dock, etc.).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setViewportHeight(entry.contentRect.height);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Auto-scroll to tail when followTail is on. Triggered every time
  // the envelope list grows. Skip when the user has scrolled up.
  useEffect(() => {
    if (!followTail) return;
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [envelopes.length, followTail]);

  const onScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    setScrollTop(el.scrollTop);
    // Pause auto-scroll when the user is not within ~one row of bottom.
    const distFromBottom = el.scrollHeight - (el.scrollTop + el.clientHeight);
    setFollowTail(distFromBottom < ROW_HEIGHT_PX * 2);
  }, []);

  const total = envelopes.length;
  const totalHeight = total * ROW_HEIGHT_PX;
  const startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT_PX) - OVERSCAN);
  const visibleCount = Math.ceil(viewportHeight / ROW_HEIGHT_PX) + OVERSCAN * 2;
  const endIdx = Math.min(total, startIdx + visibleCount);
  const topPad = startIdx * ROW_HEIGHT_PX;
  const bottomPad = (total - endIdx) * ROW_HEIGHT_PX;

  return (
    <div className="logs-page__viewport-wrap">
      <div
        ref={scrollRef}
        className="logs-page__viewport"
        onScroll={onScroll}
        role="log"
        aria-live="polite"
      >
        <div style={{ height: totalHeight }} className="logs-page__virt-spacer">
          <div style={{ paddingTop: topPad, paddingBottom: bottomPad }}>
            {envelopes.slice(startIdx, endIdx).map((env) => (
              <LogRow key={env.seq} env={env} />
            ))}
          </div>
        </div>
      </div>
      {!followTail && (
        <button
          type="button"
          className="logs-page__jump-pill"
          onClick={() => setFollowTail(true)}
        >
          Jump to live ↓
        </button>
      )}
    </div>
  );
}

function LogRow({ env }: { env: LogEnvelope }): React.JSX.Element {
  return (
    <div
      className={`logs-row logs-row--${env.level.toLowerCase()}`}
      style={{ height: ROW_HEIGHT_PX }}
    >
      <span className="logs-row__ts">{shortTs(env.ts)}</span>
      <span className="logs-row__level">{env.level}</span>
      <span className="logs-row__logger">{env.logger}</span>
      <span className="logs-row__message">{env.message}</span>
    </div>
  );
}

function shortTs(iso: string): string {
  // Render "HH:MM:SS.mmm" — date is almost always today.
  const t = iso.match(/T(\d{2}:\d{2}:\d{2}\.\d{3})/);
  return t ? t[1]! : iso;
}

function formatLine(env: LogEnvelope): string {
  return `${env.ts} ${env.level.padEnd(8)} ${env.logger} - ${env.message}`;
}
