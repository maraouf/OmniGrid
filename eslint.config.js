// ESLint v9 flat config for OmniGrid.
//
// The SPA is one large vanilla-JS file (static/js/app.js) served as-is to
// the browser — no build step, no module bundler. ESLint runs purely for
// lint feedback; it never transforms / minifies / outputs anything.
//
// Run via:
//   npm install        # picks up devDependencies below + the eslint binary
//   npm run lint       # lint static/js/ + static/login.js + static/auth-fetch.js
//   npm run lint -- --fix  # apply auto-fixable rules in place
//
// The ruleset deliberately respects the codebase's intentional conventions:
//   - `x == null` (matches BOTH null and undefined) is idiomatic and used in
//     ~260 sites — `eqeqeq` is set to `"smart"` so == is allowed against
//     null literals but flagged for any other type coercion.
//   - `catch (_) {}` swallow-pattern is used in ~300 sites where the call
//     is decorative / fire-and-forget (e.g. localStorage, optional fetches,
//     decorative animations). `no-empty` is configured with
//     `allowEmptyCatch: true` to permit it.
//   - `_`-prefixed locals are intentional "I know this isn't used yet"
//     markers. `no-unused-vars` ignores any name starting with `_`.
//   - Console-logging is operational telemetry (the `[live]` SSE event
//     trace, `[statsDebug]` operator-invoked helper, `[whyNoGraphs]` inline
//     diagnostics). `no-console` is OFF.
//
// To tighten the ruleset later: extend `recommended` in `rules:` below and
// turn individual rules on as the codebase converges on stricter style.

import js from "@eslint/js";
import globals from "globals";

export default [
  // The `node_modules/` tree ships front-end deps (alpinejs, sweetalert2,
  // @xterm/*, etc.) — never lint vendor code, it's not ours.
  {
    ignores: [
      "node_modules/**",
      ".venv/**",
      "venv/**",
      "data/**",
      "tmp/**",
      "**/*.min.js",
    ],
  },
  // Baseline rules from eslint:recommended.
  js.configs.recommended,
  // Browser-scope JS files (the SPA itself + login + the global fetch wrapper).
  {
    files: ["static/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",  // The SPA is served as a plain <script>, not a module.
      globals: {
        ...globals.browser,
        // Alpine.js global (browser bundle exposes `Alpine` on window).
        Alpine: "readonly",
        // SweetAlert2 global (browser bundle exposes `Swal`).
        Swal: "readonly",
        // qrcode-generator browser global.
        qrcode: "readonly",
        // xterm.js globals (Terminal class + addons).
        Terminal: "readonly",
        FitAddon: "readonly",
        WebLinksAddon: "readonly",
        // SPA-wide globals stamped on window at boot.
        omnigrid: "writable",
      },
    },
    rules: {
      // Allow `== null` / `!= null` — idiomatic JS for "null or undefined".
      // The `"smart"` mode keeps `===` enforcement for every OTHER comparison.
      eqeqeq: ["error", "smart"],
      // The codebase uses `catch (_) {}` extensively as a "swallow without
      // logging" pattern for decorative / fire-and-forget calls. Allow it.
      "no-empty": ["error", { allowEmptyCatch: true }],
      // Allow `_`-prefixed vars / args to be unused — they're intentional
      // markers. Don't error on args after used args either (matches Python
      // convention).
      "no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      // Operational telemetry — the SPA prints diagnostic lines that
      // operators rely on (`[live]`, `[statsDebug]`, `[whyNoGraphs]`).
      // Don't flag console.* usage.
      "no-console": "off",
      // The SPA wraps `window.fetch` at boot to add the 401 → /login
      // redirect; that's a deliberate global mutation.
      "no-global-assign": ["error", { exceptions: ["fetch"] }],
      // Bare `function () {}` declarations (vs arrow functions) are
      // intentional in Alpine event handlers + a few legacy spots.
      // Don't enforce arrow-or-function style.
      "prefer-arrow-callback": "off",
      // `prefer-const` is genuinely useful — surface let-that-could-be-const.
      "prefer-const": "warn",
      // `no-prototype-builtins` (e.g. `obj.hasOwnProperty(x)`) is overzealous
      // for hot-path lookups where defensiveness doesn't matter.
      "no-prototype-builtins": "off",
      // The SPA legitimately uses `new Function(...)` in ONE place (chart
      // expression evaluator). Loud-flag for any other site though.
      "no-new-func": "warn",
      // Permit unused expressions only when they're short-circuit
      // (`x && y()` is idiomatic optional-call).
      "no-unused-expressions": [
        "warn",
        { allowShortCircuit: true, allowTernary: true, allowTaggedTemplates: true },
      ],
    },
  },
];
