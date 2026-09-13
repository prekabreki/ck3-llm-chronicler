// M-F8 (27ov.62): the heraldry-extract SSE lifecycle (start mutation →
// open sse_url → started/progress/done/error reduction → eager close on
// terminal frames → heraldryStatus invalidation) was hand-rolled twice,
// in settings/HeraldryCard and FirstRunWizard, and had already drifted
// once (F-40: the wizard silently dropped the 'started' frame). One
// owner now; consumers only render the returned state.

import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { queryKeys, useStartHeraldryExtract } from './queries';
import type { HeraldryProgressFrame } from './types';

export interface HeraldryExtractProgress {
  active: boolean;
  group: string | null;
  current: number;
  total: number;
  /** 'Starting…' while the run spins up; 'Done · …' after success. */
  message: string | null;
  error: string | null;
}

const IDLE: HeraldryExtractProgress = {
  active: false,
  group: null,
  current: 0,
  total: 0,
  message: null,
  error: null,
};

export function useHeraldryExtract(opts?: { onDone?: () => void }): {
  progress: HeraldryExtractProgress;
  run: (force?: boolean) => void;
  isStarting: boolean;
} {
  const queryClient = useQueryClient();
  const startMu = useStartHeraldryExtract();
  const [progress, setProgress] = useState<HeraldryExtractProgress>(IDLE);

  // EventSource is held in a ref so run() can open it once and the
  // cleanup effect can close it on unmount. Closed eagerly on terminal
  // frames; the SSE connection is one-shot per extract.
  const sseRef = useRef<EventSource | null>(null);
  useEffect(() => {
    return () => {
      if (sseRef.current) sseRef.current.close();
    };
  }, []);

  const onDone = opts?.onDone;
  const run = (force = false): void => {
    setProgress({ ...IDLE, active: true });
    startMu.mutate(force, {
      onSuccess: (started) => {
        // Close any prior stream before opening the new one.
        if (sseRef.current) sseRef.current.close();
        const es = new EventSource(started.sse_url);
        sseRef.current = es;
        es.onmessage = (ev) => {
          try {
            // audit F-44: typed against the BE Pydantic discriminated
            // union so a stage rename surfaces as a TS error.
            const data = JSON.parse(ev.data) as HeraldryProgressFrame;
            if (data.stage === 'started') {
              setProgress((p) => ({ ...p, active: true, message: 'Starting…' }));
            } else if (data.stage === 'progress') {
              setProgress((p) => ({
                ...p,
                active: true,
                group: data.group,
                current: data.current,
                total: data.total,
                error: null,
              }));
            } else if (data.stage === 'done') {
              setProgress({
                ...IDLE,
                message: `Done · ${data.palette_colors} colours · ${data.patterns} patterns · ${data.emblems} emblems`,
              });
              es.close();
              sseRef.current = null;
              void queryClient.invalidateQueries({
                queryKey: queryKeys.heraldryStatus(),
              });
              onDone?.();
            } else if (data.stage === 'error') {
              setProgress({ ...IDLE, error: data.message ?? 'extract failed' });
              es.close();
              sseRef.current = null;
            }
          } catch {
            // ignore malformed frames
          }
        };
        es.onerror = () => {
          setProgress((p) =>
            p.active
              ? { ...p, active: false, error: p.error ?? 'SSE connection closed' }
              : p,
          );
        };
      },
      onError: (err) => {
        setProgress({ ...IDLE, error: err.message });
      },
    });
  };

  return { progress, run, isStarting: startMu.isPending };
}
