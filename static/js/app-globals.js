// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// Top-level browser globals installed at module-load time. Side-effect
// import only — the body of this file mutates `window` directly and has
// nothing to export.

window.__ogCopyAiCode = function (btn) {
  if (!btn) {
    return;
  }
  const wrapper = btn.closest('.ai-resp-code-block');
  if (!wrapper) {
    return;
  }
  let body = '';
  try {
    body = JSON.parse(wrapper.dataset.code || '""');
  } catch (_) {
    body = wrapper.dataset.code || '';
  }
  const flashCopied = () => {
    btn.classList.add('ai-resp-code-copy--copied');
    setTimeout(() => btn.classList.remove('ai-resp-code-copy--copied'), 1200);
  };
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    navigator.clipboard.writeText(body).then(flashCopied).catch(() => {
      try {
        const ta = document.createElement('textarea');
        ta.value = body;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        flashCopied();
      } catch (_) { /* clipboard not available — silently no-op */
      }
    });
  } else {
    try {
      const ta = document.createElement('textarea');
      ta.value = body;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      flashCopied();
    } catch (_) {
    }
  }
};


// requestAnimationFrame violation probe — opt-in diagnostic for the
// Chromium `[Violation] 'requestAnimationFrame' handler took <N>ms`
// warning. When enabled (via `window.__ogRafProbeMs = <threshold>` or
// the matching `?raf=<ms>` URL param), wraps `window.requestAnimationFrame`
// with a perf-counter that logs slow callbacks to BOTH `console.warn`
// (devtools) AND a fire-and-forget POST `/api/admin/spa-diagnostic`
// (container stdout) so the user can localise the offender without
// devtools access (page-unresponsive scenarios). Default-off — the
// probe is a wrapper that compounds across N rAF invocations, so we
// never want it on by default.
//
// Activation:
//   1. URL: append `?raf=50` to any SPA URL (logs every rAF callback
//      that takes >50ms).
//   2. Console: `window.__ogRafProbeMs = 50; location.reload();`
//   3. Programmatic: set `window.__ogRafProbeMs` BEFORE this script
//      loads (e.g. from a debug overlay in a future build).
//
// Each slow callback emits one `[raf-violation]` line carrying the
// elapsed ms + the callback's `.name` (when assigned) + the first
// few stack frames truncated to keep the log line manageable. The
// fire-and-forget POST silently swallows any backend failure so the
// probe never amplifies a hang.
(function _installRafProbe() {
  try {
    let threshold = null;
    const urlMatch = location.search && location.search.match(/[?&]raf=(\d+)/);
    if (urlMatch) {
      threshold = parseInt(urlMatch[1], 10);
    } else if (typeof window.__ogRafProbeMs === 'number' && window.__ogRafProbeMs > 0) {
      threshold = window.__ogRafProbeMs;
    }
    if (!threshold || threshold < 1) {
      return;
    }
    if (typeof window.requestAnimationFrame !== 'function') {
      return;
    }
    if (window.__ogRafProbeInstalled) {
      return;
    }
    window.__ogRafProbeInstalled = true;
    const orig = window.requestAnimationFrame.bind(window);
    let seq = 0;
    window.requestAnimationFrame = function (cb) {
      const id = ++seq;
      const callerStack = (new Error()).stack || '';
      // Trim the stack to the FIRST 3 frames past this wrapper; that's
      // usually enough to spot the offender without flooding the log.
      const frames = callerStack.split('\n').slice(2, 5)
        .map((s) => s.replace(/\s+at\s+/, '').trim())
        .filter(Boolean).join(' | ');
      return orig(function _ogRafWrapper(ts) {
        const t0 = performance.now();
        let err = null;
        try {
          return cb(ts);
        } catch (e) {
          err = e;
          throw e;
        } finally {
          const took = performance.now() - t0;
          if (took >= threshold) {
            const name = (cb && cb.name) || 'anonymous';
            const line = '[raf-violation] id=' + id + ' took=' + took.toFixed(1)
              + 'ms cb=' + name + (err ? ' threw' : '')
              + ' caller=' + (frames || 'unknown');
            try {
              console.warn(line);
            } catch (_) {
            }
            try {
              fetch('/api/admin/spa-diagnostic', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                  kind: 'raf-violation',
                  took_ms: Math.round(took),
                  cb: name,
                  caller: frames,
                  threw: !!err,
                }),
              }).catch(() => {
              });
            } catch (_) {
            }
          }
        }
      });
    };
    try {
      console.info('[raf-probe] installed — threshold=' + threshold + 'ms. '
        + 'Watch for [raf-violation] lines in this console + container stdout.');
    } catch (_) {
    }
  } catch (_) {
    // Probe install failure is silent — never break the SPA over a
    // diagnostic tool that wasn't asked for.
  }
})();
