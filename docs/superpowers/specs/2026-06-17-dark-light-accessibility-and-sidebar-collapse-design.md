# Dark/Light Accessibility, Theme Control & Sidebar Collapse Switch — Design

**Date:** 2026-06-17
**Status:** Approved (design); pending implementation plan
**Related:** `docs/superpowers/specs/2026-06-17-ui-design-system.md` (theme system, `theme.css`, `theme.js`)

## Problem

When switching between light and dark themes, some text disappears.

**Root cause:** The dark theme only swaps CSS custom properties (`html.dark-theme`
re-defines `--bg-*` / `--text-*` / `--border-color` in `finapp/static/theme.css`).
But the templates almost never consume those variables. Instead they use
**hardcoded Tailwind color classes** — `text-gray-900`, `bg-white`,
`text-gray-500`, `border-gray-200` — **168 occurrences across all 11 templates**,
and `finapp/templates/base.html` hardcodes `#ffffff` / `#374151` / `#e5e7eb` in
its `<style>` block for the sidebar, bottom-nav, footer, and modal.

So in dark mode the `<body>` background goes dark (it *does* use a variable) while
the text stays dark-gray and cards stay white. Dark text over the dark background
is unreadable — it "disappears."

This work also includes two requested UI changes that touch the same files:

1. Move the light/dark control out of the sidebar and into a Settings "Appearance"
   section, expanded to **Light / Dark / System**.
2. Make the sidebar collapse control the **last item in the sidebar**, full sidebar
   width, styled as a **sliding toggle** whose content slides on click.

## Goals

- Both themes meet **WCAG 2.1 AA** contrast (≥4.5:1 for body text, ≥3:1 for large
  text and UI components / borders).
- No text or control becomes invisible in either theme, on any page.
- Theme selection lives in Settings, supports Light / Dark / System, and persists.
- The sidebar collapse control is the final, full-width element in the sidebar and
  animates as a sliding switch.

## Non-goals / Out of scope

- Visual redesign beyond color/theme correctness (no new layout, spacing, or
  typography changes except those required by the items above).
- New settings beyond the Appearance section.
- Mobile bottom-nav layout redesign. It receives the same color-variable treatment
  so it themes correctly, but its structure does not change.
- Any backend, money, or ledger logic. This is presentation-only; the
  `recompute_balances(ctx)` reconciliation gate is unaffected.

## Approach

**Chosen: semantic themed classes (Approach A).** Lean on the design-system
classes that already exist in `theme.css` (`.card`, `.nav-item`, `.text-muted`,
`.form-input`, `.form-label`, `.btn-*`, `.text-primary-color`,
`.text-secondary-color`), add the few missing ones, and replace hardcoded Tailwind
color classes in the templates with them. One variable set drives both themes.

**Rejected — Tailwind `dark:` variants:** still edits all 168 spots, doubles the
class soup, and forks the source of truth (Tailwind palette vs. CSS variables).

**Rejected — remap Tailwind's gray palette to CSS variables:** fewest edits but
fragile. The same token (`gray-900`) means "dark text" in one place and "dark
surface" in another; a numeric scale cannot carry that semantics, producing subtle
contrast bugs.

Structural Tailwind utilities (flex, grid, spacing, sizing) are left untouched.
Only color-bearing classes are converted.

## Design

### 1. Theme tokens & contrast

Audit and lock both variable sets in `theme.css` to WCAG AA. Document each
semantic text/background pairing and its contrast ratio in both themes (see
Verification). Adjust any token that fails (e.g. nudge `--text-secondary` /
`--text-tertiary` darker in light mode or lighter in dark mode if a pairing falls
below threshold).

**New semantic classes added to `theme.css`:**

- `.surface` — themed section/card surface: `background: var(--bg-primary)`,
  `border: 1px solid var(--border-color)`, `border-radius: var(--radius-md)`.
  Replaces the repeated `bg-white rounded-xl border border-gray-200`.
- `.page-title` — replaces `text-gray-900` page headings; `color: var(--text-primary)`.
- `.section-title` — replaces `text-gray-900` / `text-lg font-semibold` section
  headings; `color: var(--text-primary)`.
- `.input-disabled` (or equivalent) — themed replacement for the
  `bg-gray-50 text-gray-400` disabled/placeholder input row.

Existing classes reused as-is: `.text-muted`, `.text-secondary-color`,
`.text-primary-color`, `.form-input`, `.form-select`, `.form-label`, `.form-help`,
`.btn-primary` / `.btn-secondary`, `.nav-item`, `.card`, `.badge-*`.

### 2. Template conversion (all 11 templates)

Mechanical, color-only swap using this mapping:

| Hardcoded Tailwind | Replacement |
| --- | --- |
| `bg-white … rounded-xl border border-gray-200` (sections) | `.surface` |
| `text-gray-900` (page heading) | `.page-title` |
| `text-gray-900` / `text-lg font-semibold` (section heading) | `.section-title` |
| `text-gray-700` (labels) | `.form-label` or `.text-primary-color` |
| `text-gray-600` / `text-gray-500` / `text-gray-400` | `.text-muted` / `.text-secondary-color` |
| `border-gray-300` / `border-gray-200` (inputs) | `.form-input` |
| `bg-gray-50 text-gray-400` (disabled) | `.input-disabled` |
| `text-blue-600` / `bg-blue-50` | `--color-info` helpers (`.text-info`, info backgrounds) |
| `hover:bg-gray-50` | themed hover backed by `var(--bg-tertiary)` |

Templates in scope: `dashboard.html`, `budget.html`, `transactions.html`,
`debt.html`, `debt_projection.html`, `savings.html`, `reviews.html`,
`settings.html`, `onboarding.html`, `assets.html`, `base.html`.

Inline `style="background-color: #F59E0B;"` and similar one-offs are reviewed; the
amber badge color is an intentional, theme-stable accent (`--color-warning`) and is
kept, but referenced via the variable where practical.

### 3. `base.html` chrome rewrite

Rewrite the hardcoded colors in `base.html`'s `<style>` block — both the primary
rules and the Tailwind-unavailable fallback block (≈ lines 247–408) — to consume
variables:

- `#ffffff` backgrounds → `var(--bg-primary)`
- `#374151` / `#4b5563` text → `var(--text-primary)` / `var(--text-secondary)`
- `#e5e7eb` / `#d1d5db` borders → `var(--border-color)`
- `#f3f4f6` / `#f9fafb` hovers/fills → `var(--bg-tertiary)` / `var(--bg-secondary)`
- `#2563eb` / `#eff6ff` active states → `var(--color-info)` / `var(--color-info-light)`

This makes the sidebar, bottom-nav, footer, and Add Transaction modal theme
correctly. The `@apply`-based duplicates that reference Tailwind color utilities
(e.g. `bg-white`, `text-gray-700`) are converted to variable-backed declarations so
the chrome is correct whether or not the Play CDN loads.

### 4. Theme control moves to Settings

**Remove** the sidebar theme-toggle button (`base.html` lines ≈ 481–485).

**Add an "Appearance" section** to `settings.html` containing a 3-way control —
**Light / Dark / System** (segmented buttons or a select; segmented preferred for
visibility of the active mode). The active mode is highlighted.

**`theme.js` changes:**

- Persist one of `light` | `dark` | `system` under the existing `theme` key.
- `applyTheme(mode)`:
  - `light` / `dark` → set the class directly (current behavior).
  - `system` → resolve via `window.matchMedia('(prefers-color-scheme: dark)')`,
    apply the resolved theme, and register a `change` listener so live OS changes
    re-apply while in `system` mode (listener removed/ignored when the user picks an
    explicit mode).
- Pre-paint restore logic in `<head>` updated to resolve `system` before first
  paint (no flash).
- Provide `setTheme(mode)` for the Settings control. Keep a thin `toggleTheme()`
  shim only if still referenced; otherwise remove it along with the sidebar button.
- The Settings control reads the stored mode on load and reflects the active
  selection.

### 5. Sidebar collapse switch

- **Move "Add Transaction"** out of `#sidebar-footer` and into `#sidebar-nav` as
  the last nav entry (below "Settings", above the footer). It keeps its existing
  collapse behavior (icon-only when the rail is collapsed).
- **`#sidebar-footer` becomes a single full-width collapse switch** — the last
  element in the sidebar. Styled as a sliding toggle:
  - A full-width track (`width: 100%`) containing a knob + chevron and a "Collapse"
    label.
  - On click, the knob/chevron **slides across the track** via
    `transform: translateX(...)` with a ~0.3s transition (toggle-switch feel), in
    addition to collapsing the sidebar.
- Clicking still toggles the sidebar between full width (256px) and the 80px icon
  rail (existing width transition) and persists `sidebar-collapsed` in
  `localStorage` (unchanged mechanism).
- **Collapsed state:** the switch shrinks with the 80px rail and shows only the
  slid chevron (`›`); the "Collapse" label is hidden by the existing
  `#sidebar.collapsed … .nav-label` rule (or an equivalent rule for the switch
  label). Expanded shows `‹ Collapse` with the knob at the start.
- **Reduced motion:** under `prefers-reduced-motion: reduce`, the slide is instant
  (already covered by the global reduced-motion rule in `theme.css`; verify it
  applies to the new transform).

### Components & boundaries

- `theme.css` — single source of truth for color tokens and semantic classes. No
  template carries raw colors after conversion.
- `theme.js` — owns theme resolution/persistence, including `system`. Settings UI
  calls `setTheme(mode)`; nothing else manipulates the theme class directly.
- `base.html` — owns app chrome (sidebar, nav, footer switch, modal), all
  variable-backed.
- `settings.html` — owns the Appearance control surface only; delegates behavior to
  `theme.js`.

## Verification (Done gate)

1. **Contrast table:** for every semantic pairing (page title, section title, body,
   muted text, labels, links, borders, nav item, active nav item, badges) record
   the computed contrast ratio in **both** themes; all meet WCAG AA. Documented
   alongside the implementation.
2. **Manual theme sweep:** set Light, then Dark, then System on **every** page —
   `dashboard`, `budget`, `transactions`, `debt`, `debt_projection`, `savings`,
   `reviews`, `settings`, `onboarding`, `assets`. Confirm no text/control
   disappears, surfaces and chrome theme correctly, and the choice persists across
   a reload.
3. **System mode:** toggling the OS appearance while in `system` mode flips the app
   live; no flash on reload.
4. **Sidebar switch:** the collapse switch is the last sidebar element, is full
   width, and its content slides on click; collapse/expand persists across reloads
   in both expanded and collapsed states; reduced-motion makes the slide instant.
5. **No regressions to money logic:** no backend/ledger files changed;
   `recompute_balances(ctx)` reconciliation remains green.

## Open questions

None. All decisions resolved during brainstorming:
- Fix scope: full theme-aware pass.
- Theme control: Appearance section, Light / Dark / System.
- Sidebar footer: collapse switch is the only footer item (Add Transaction moves
  into the nav list).
- "Slide": the button content slides (toggle-switch feel) in addition to the
  sidebar collapsing.
