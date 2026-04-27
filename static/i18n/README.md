# OmniGrid translations

Every user-visible string in the UI lives in a JSON file in this directory.
`en.json` is the source of truth; other languages mirror its key tree.

## Files

- `en.json` — reference language. Contains every key the UI looks up.
- `ar.json` — Arabic template (RTL). Empty values so it falls back to
  English until a translator fills them in.
- `index.json` — enumerates available languages for the picker. Shape:
  `[{ "code": "en", "name": "English", "dir": "ltr" }, …]`
- `README.md` — this file.

Each language file begins with a `_meta` object:

```json
"_meta": {
  "code": "ar",
  "name": "العربية",
  "dir": "rtl"
}
```

`dir` is either `"ltr"` or `"rtl"` and drives both `<html dir="…">` and
`<html lang="…">` on page load / language switch.

## Key conventions

- Keys are dotted paths grouped by area of the UI:
  `nav.stacks`, `actions.update`, `toasts.network_error`, `settings.profile.about`.
- Placeholders use `{name}` / `{count}` style — the frontend replaces them
  at render time.
- Strings containing inline HTML (e.g. `<b>`, `<kbd>`, `<code>`) are passed
  through SweetAlert2's `html:` option or bound via Alpine `x-html`; keep
  the markup intact when translating and only translate the visible text.

## Missing-key fallback

The `t()` helper looks up the current language dict first. If the key
isn't present (or its value is empty), it falls back to `en.json`; if
that also fails, it returns the key itself so the missing string is
visually obvious during development. A `console.warn` is emitted once
per missing key so translators have a clear to-do list.

## Testing locally

Open the app, then in devtools:

```js
I18N.code    // current language code
I18N.dir     // "ltr" or "rtl"
document.documentElement.dir
document.documentElement.lang
```

Switch languages from the top-bar picker or Settings → Language. The
choice persists in `localStorage` under key `lang`.

## Adding a new language

1. Copy `en.json` to `<code>.json` (two-letter ISO 639-1 lowercase; use
   a regional tag if needed: `pt-br.json`).
2. Update the `_meta` block (`code`, `name`, `dir`).
3. Add an entry to `index.json`:
   ```json
   { "code": "pt-br", "name": "Português (Brasil)", "dir": "ltr" }
   ```
4. Translate values in place. Empty / missing values fall back to English.
5. No backend change needed — `/i18n/` is served as static files.

## Adding a new UI string

1. Add the key + English value to `en.json` in the right section.
2. Reference it from the HTML / JS as `t('section.key')`.
3. Other language files don't need updates immediately — they fall back.

## Contributing

Open a PR against the OmniGrid repo. For context-sensitive translations,
leave a `// comment`-style note in the PR description; keeping JSON itself
free of non-standard comments means it stays machine-loadable in the browser
without a preprocessor.
