# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A two-file internal tool for GoKwik. Open `merchant-product-radar.html` in a browser — no build step, no package manager.

> **Note:** `config.js` must be loadable by the browser. If opening from `file://` in Chrome, serve the directory via HTTP instead: `python3 -m http.server 8080`, then open `http://localhost:8080/merchant-product-radar.html`.

## Configuration

Edit `config.js` (never commit this file with a real key):

```js
window.GK_CONFIG = {
  METABASE_URL: 'https://internal-stats.gokwik.in',  // no trailing slash
  API_KEY: '',   // Metabase Admin → Settings → API Keys
  DB_ID: null,   // optional: skip auto-detection
};
```

The HTML reads `window.GK_CONFIG` on `DOMContentLoaded`. If either field is blank it shows an error banner — there is no UI input for credentials.

## Architecture

`merchant-product-radar.html` contains CSS, HTML, and JS in one file. `config.js` is the only external dependency (besides CDNs).

### CSS (top of file)

Uses CSS custom properties on `:root` and `[data-theme="dark"]` for full dark/light theming. The `data-theme` attribute is set on `<html>` and toggled by `toggleTheme()`. Theme preference is persisted in `localStorage` as `gk_theme`.

### JS module structure (inside the `<script>` block)

| Section | Purpose |
|---|---|
| CONFIG | Reads `window.GK_CONFIG`; sets `API_KEY`, `DB_ID`, runtime state |
| INIT | `DOMContentLoaded` — applies stored theme, validates config, calls `detectDatabase()` |
| THEME | `setTheme()`, `toggleTheme()` — swaps `data-theme` and moon/sun icon |
| DATABASE AUTO-DETECT | `detectDatabase()` — hits `/api/database`, picks Trino/Starburst engine by name |
| SEARCH | `runSearch()` — builds UNION ALL SQL across both stickiness tables |
| SCHEMA DISCOVERY | `discoverSchema()` — `SELECT * LIMIT 1` to map column names → `detectedCols` |
| METABASE API | `mbFetch()`, `runNativeQuery()` — wraps `/api/dataset` POST |
| RENDER | `renderTable()` — rebuilds `<tbody>` from `filteredRows` |
| STATS & SIDE CARD | `updateStats()`, `updateSideCard()` — stat tiles + sidebar merchant summary |
| FILTERS & SORT | `applyFilter()`, `applyProductFilter()`, `sortTable()` — client-side on `allRows` |

### Data flow

1. User types → `runSearch()`
2. `discoverSchema()` runs once (cached in `detectedCols`)
3. UNION ALL SQL built using `detectedCols` aliases, executed via `runNativeQuery()`
4. Raw rows → `allRows`; `filteredRows` = working subset after status/product filters
5. `renderTable(filteredRows)` rebuilds the table on every filter/sort change

### External dependencies (CDN)

- Font Awesome 4.7 (icon names use `fa-*` pattern)
- jQuery 3.7.1
- Google Fonts — Inter
- Metabase instance at the URL in `config.js`

## Key design decisions

**SQL injection mitigation**: `'` → `''` escaping applied to the search term. MID searches use exact CAST comparison; name searches use `LIKE`. Preserve this when editing query construction.

**Column name flexibility**: `discoverSchema()` + `findCol()` handle tables with non-default column names. `detectedCols` is cached per page load. Force re-discovery in the browser console: `detectedCols = {}`.

**`parseActive()`**: normalises `is_active` across boolean, number, and string variants (`true/false`, `1/0`, `yes/no`, `active/inactive`, etc.) → JS `true | false | null`.

**Dark mode**: toggled via `data-theme="dark"` on `<html>`. All colours are CSS variables — never hardcode colours outside the `:root` / `[data-theme="dark"]` blocks.
