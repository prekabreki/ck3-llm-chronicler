// Audit F-28: debug.log status + manual rotate (ck3_chronicler-z6jm
// slice 1). Extracted from SettingsPage.
//
// Backend polls debug.log size every 60s (refetchInterval on the
// query). When size > threshold, the panel raises an alert and the
// rotate button highlights. The button is always clickable so the
// user can choose to rotate at any time; the backend's
// archive_and_truncate is best-effort on Windows when CK3 is open
// and reports 'CK3 running?' to stderr without raising.

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { rotateDebugLog } from '../../api/client';
import { queryKeys, useDebugLogStatus } from '../../api/queries';

export function DebugLogPanel(): React.JSX.Element {
  const queryClient = useQueryClient();
  const statusQ = useDebugLogStatus();
  const rotateMutation = useMutation({
    mutationFn: () => rotateDebugLog(),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.debugLogStatus(),
      });
    },
  });

  const status = statusQ.data ?? null;

  if (!status) {
    return (
      <div className="debug-log-panel italic-fell">Reading log status…</div>
    );
  }

  return (
    <div className="debug-log-panel">
      <div className="debug-log-panel__row">
        <div>
          <div className="smallcaps debug-log-panel__label">CK3 debug.log</div>
          <div className="debug-log-panel__path">{status.path}</div>
        </div>
        <div className="debug-log-panel__sizes">
          <div className="debug-log-panel__size">
            {status.exists ? formatBytes(status.size_bytes) : '(not yet written)'}
          </div>
          <div className="smallcaps debug-log-panel__threshold">
            threshold {formatBytes(status.threshold_bytes)}
          </div>
        </div>
      </div>
      {status.exceeded && (
        <p className="debug-log-panel__alert italic-fell">
          The debug log has exceeded the rotation threshold. Close CK3
          and rotate to keep the chronicler's tail loop responsive.
        </p>
      )}
      <div className="debug-log-panel__actions">
        <button
          type="button"
          className={
            'debug-log-panel__rotate' +
            (status.exceeded ? ' debug-log-panel__rotate--alert' : '')
          }
          onClick={() => rotateMutation.mutate()}
          disabled={rotateMutation.isPending || !status.exists}
        >
          {rotateMutation.isPending ? 'Rotating…' : 'Rotate now'}
        </button>
        <span className="italic-fell debug-log-panel__hint">
          Archives the log to <code>logs/archives/</code> and resets every
          campaign's tail offset. Best run with CK3 closed.
        </span>
      </div>
      {rotateMutation.data && (
        <p
          className={
            'debug-log-panel__result italic-fell' +
            (rotateMutation.data.rotated ? '' : ' debug-log-panel__result--noop')
          }
        >
          {rotateMutation.data.message}
          {rotateMutation.data.offsets_reset > 0 &&
            ` (${rotateMutation.data.offsets_reset} campaign offset${rotateMutation.data.offsets_reset === 1 ? '' : 's'} reset)`}
        </p>
      )}
    </div>
  );
}

function formatBytes(n: number): string {
  if (n === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  const rounded = i === 0 ? v.toString() : v.toFixed(1);
  return `${rounded} ${units[i]}`;
}
