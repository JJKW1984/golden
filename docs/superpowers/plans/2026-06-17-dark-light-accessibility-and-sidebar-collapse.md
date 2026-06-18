# Dark/Light Accessibility, Theme Control & Sidebar Collapse — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every page readable in both light and dark themes (WCAG AA), move the theme control into a Settings "Appearance" section (Light/Dark/System), and turn the sidebar collapse control into a full-width sliding switch that is the last item in the sidebar.

**Architecture:** Presentation-only. One source of truth for color — the CSS custom properties in `finapp/static/theme.css`. Templates stop using hardcoded Tailwind color classes (`text-gray-900`, `bg-white`, …) and use variable-backed semantic classes instead. `theme.js` gains a `system` mode. No backend, money, or ledger code is touched.

**Tech Stack:** FastAPI + Jinja2 templates, Tailwind (Play CDN), vanilla JS (`theme.js`), pytest (file-content + `TestClient` assertions; no JS test runner in this repo).

**Spec:** `docs/superpowers/specs/2026-06-17-dark-light-accessibility-and-sidebar-collapse-design.md`

## Global Constraints

- All color decisions resolve from CSS variables in `finapp/static/theme.css`. No new raw hex/Tailwind color classes in templates after conversion.
- Both themes must meet **WCAG 2.1 AA**: body text ≥ 4.5:1, large text/UI ≥ 3:1.
- Dark theme is selected by the class `html.dark-theme` (existing convention). Theme preference persists in `localStorage` under the key `theme`, value one of `light` | `dark` | `system`.
- Sidebar collapsed state persists in `localStorage` under `sidebar-collapsed` (existing key, unchanged).
- `theme.js` must load before the Tailwind CDN script in `base.html` (existing constraint; no flash of unstyled content).
- Calm-by-design: never use red for over-budget (amber `--color-warning` `#f59e0b`); this work does not introduce any new red states.
- Run tests with: `uv run pytest tests/test_theme_accessibility.py tests/test_ui_foundation.py -v`
- No backend/ledger files change; `recompute_balances(ctx)` reconciliation is unaffected and not re-run by this work.

---

### Task 1: WCAG contrast test + token audit

Build a reusable contrast checker that parses `theme.css` and asserts the text/background pairings that caused the disappearing-text bug meet AA in **both** themes. Fix any failing token.

**Files:**
- Create: `tests/test_theme_accessibility.py`
- Modify: `finapp/static/theme.css` (only if a pairing fails)

**Interfaces:**
- Produces: test module `tests/test_theme_accessibility.py` with helpers `parse_theme_vars(css, selector) -> dict`, `resolve_dark(root, dark) -> dict`, `contrast_ratio(hex_a, hex_b) -> float`. Later tasks add assertions to this file.

- [x] **Step 1: Write the failing test**

Create `tests/test_theme_accessibility.py`:

```python
"""
WCAG AA contrast checks for the theme system.

Parses the CSS custom properties in theme.css for the light (:root) and
dark (html.dark-theme) themes and verifies the text/background pairings
that previously caused disappearing text meet WCAG 2.1 AA.

Spec: docs/superpowers/specs/2026-06-17-dark-light-accessibility-and-sidebar-collapse-design.md
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
THEME_CSS = REPO_ROOT / "finapp" / "static" / "theme.css"


def parse_theme_vars(css: str, selector: str) -> dict:
    """Return {var_name: value} for the first CSS block matching `selector`."""
    start = css.index(selector)
    brace = css.index("{", start)
    depth = 0
    i = brace
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = css[brace + 1:i]
    out = {}
    for name, val in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body):
        out[name.strip()] = val.strip()
    return out


def resolve_dark(root: dict, dark: dict) -> dict:
    """Dark theme = root with dark overrides applied."""
    merged = dict(root)
    merged.update(dark)
    return merged


def _hex_to_rgb(value: str):
    h = value.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb):
    def chan(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    la = _luminance(_hex_to_rgb(hex_a))
    lb = _luminance(_hex_to_rgb(hex_b))
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# Text pairings that must meet AA body-text contrast (>= 4.5:1) in BOTH themes.
TEXT_PAIRINGS = [
    ("--text-primary", "--bg-primary"),
    ("--text-primary", "--bg-secondary"),
    ("--text-primary", "--bg-tertiary"),
    ("--text-secondary", "--bg-primary"),
    ("--text-secondary", "--bg-secondary"),
]


@pytest.fixture(scope="module")
def themes():
    css = THEME_CSS.read_text()
    root = parse_theme_vars(css, ":root")
    dark = parse_theme_vars(css, "html.dark-theme")
    return {"light": root, "dark": resolve_dark(root, dark)}


@pytest.mark.parametrize("fg,bg", TEXT_PAIRINGS)
@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_text_pairings_meet_aa(themes, theme_name, fg, bg):
    vars_ = themes[theme_name]
    ratio = contrast_ratio(vars_[fg], vars_[bg])
    assert ratio >= 4.5, (
        f"{theme_name}: {fg} ({vars_[fg]}) on {bg} ({vars_[bg]}) "
        f"= {ratio:.2f}:1, needs >= 4.5:1"
    )
```

- [x] **Step 2: Run test to verify it fails (or surfaces a weak token)**

Run: `uv run pytest tests/test_theme_accessibility.py -v`
Expected: either failures naming specific pairings below 4.5:1 (e.g. `--text-secondary` on `--bg-secondary`), or all pass. The likely offender is light-theme `--text-secondary: #6b7280` on `--bg-secondary: #f9fafb`.

- [x] **Step 3: Fix any failing token in `finapp/static/theme.css`**

If a light-theme pairing fails, darken the offending text token. Recommended adjustment (only apply if its pairing fails):

```css
/* :root (light theme) */
--text-secondary: #4b5563; /* gray-600 — was #6b7280; raises contrast on tinted backgrounds */
```

Re-check that no *other* pairing regresses. Do not change accent colors (teal/gold/success/warning/danger/info) — those are theme-stable per the spec.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_theme_accessibility.py -v`
Expected: PASS (all parametrized pairings, both themes).

- [x] **Step 5: Commit**

```bash
git add tests/test_theme_accessibility.py finapp/static/theme.css
git commit -m "test: WCAG AA contrast checks for theme tokens"
```

---

### Task 2: Add missing semantic classes to theme.css

Add the variable-backed classes templates will use so no template needs a raw color.

**Files:**
- Modify: `finapp/static/theme.css` (append to the relevant sections)
- Modify: `tests/test_theme_accessibility.py` (add presence assertions)

**Interfaces:**
- Produces CSS classes consumed by Tasks 3, 5, 6, 7: `.surface`, `.page-title`, `.section-title`, `.input-disabled`, `.link-info`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_theme_accessibility.py`:

```python
class TestSemanticClasses:
    @pytest.fixture(autouse=True)
    def css(self):
        self.content = THEME_CSS.read_text()

    def test_surface_class(self):
        assert ".surface" in self.content

    def test_page_title_class(self):
        assert ".page-title" in self.content

    def test_section_title_class(self):
        assert ".section-title" in self.content

    def test_input_disabled_class(self):
        assert ".input-disabled" in self.content

    def test_link_info_class(self):
        assert ".link-info" in self.content

    def test_surface_uses_variables(self):
        # The .surface rule body must use variables, not raw hex.
        start = self.content.index(".surface")
        body = self.content[start:start + 300]
        assert "var(--bg-primary)" in body
        assert "var(--border-color)" in body
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_theme_accessibility.py::TestSemanticClasses -v`
Expected: FAIL — classes not found.

- [x] **Step 3: Add the classes to `finapp/static/theme.css`**

Append after the Card Components section (near the existing `.card` rule):

```css
/* --------------------------------------------------------------------------
   Semantic surface & text helpers (theme-aware replacements for raw Tailwind)
   -------------------------------------------------------------------------- */

/* Page section / panel surface — replaces `bg-white border border-gray-200` */
.surface {
  background-color: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

/* Page-level heading — replaces `text-gray-900` on <h1> */
.page-title {
  color: var(--text-primary);
}

/* Section heading — replaces `text-gray-900` on <h2>/<h3> */
.section-title {
  color: var(--text-primary);
}

/* Themed disabled / placeholder input — replaces `bg-gray-50 text-gray-400` */
.input-disabled {
  background-color: var(--bg-tertiary);
  color: var(--text-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
}

/* Info link / accent text — replaces `text-blue-600` */
.link-info {
  color: var(--color-info);
}
.link-info:hover {
  opacity: 0.85;
}
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_theme_accessibility.py::TestSemanticClasses -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add finapp/static/theme.css tests/test_theme_accessibility.py
git commit -m "feat: add themed semantic classes (.surface, .page-title, etc.)"
```

---

### Task 3: Rewrite base.html chrome to use variables

Replace hardcoded colors in the `base.html` `<style>` block (sidebar, bottom-nav, footer, modal) and the Tailwind-unavailable fallback block with `var(--…)`, so the chrome themes correctly.

**Files:**
- Modify: `finapp/templates/base.html` (the `<style>` block, ≈ lines 33–409)
- Modify: `tests/test_theme_accessibility.py` (add chrome guard test)

**Interfaces:**
- Consumes: variables from `theme.css` (`--bg-primary`, `--bg-secondary`, `--bg-tertiary`, `--text-primary`, `--text-secondary`, `--border-color`, `--color-info`, `--color-info-light`).

- [x] **Step 1: Write the failing test**

Append to `tests/test_theme_accessibility.py`:

```python
class TestBaseChromeThemed:
    @pytest.fixture(autouse=True)
    def html(self):
        self.content = (REPO_ROOT / "finapp" / "templates" / "base.html").read_text()

    def test_no_hardcoded_white_in_style_block(self):
        style = self.content[self.content.index("<style>"):self.content.index("</style>")]
        # The chrome must not hardcode white/gray surfaces or text.
        for raw in ("#ffffff", "#374151", "#4b5563", "#e5e7eb", "#f3f4f6",
                    "#f9fafb", "#2563eb", "#eff6ff", "#111827", "#4b5563"):
            assert raw not in style.lower(), f"hardcoded {raw} left in <style> chrome"

    def test_style_block_uses_variables(self):
        style = self.content[self.content.index("<style>"):self.content.index("</style>")]
        assert "var(--bg-primary)" in style
        assert "var(--text-primary)" in style
        assert "var(--border-color)" in style
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_theme_accessibility.py::TestBaseChromeThemed -v`
Expected: FAIL — hardcoded hex values present.

- [x] **Step 3: Rewrite the `<style>` chrome in `finapp/templates/base.html`**

In the `<style>` block, replace every hardcoded color with its variable. Apply these substitutions throughout both the primary rules and the fallback block (≈ lines 247–408):

- `#ffffff` / `background-color: #ffffff` → `var(--bg-primary)`
- `#374151` (nav text) → `var(--text-primary)`
- `#4b5563` (muted nav text) → `var(--text-secondary)`
- `#e5e7eb` / `#d1d5db` (borders) → `var(--border-color)`
- `#f3f4f6` (hover fill) → `var(--bg-tertiary)`
- `#f9fafb` (toggle bg) → `var(--bg-secondary)`
- `#111827` (hover text) → `var(--text-primary)`
- `#2563eb` (active) → `var(--color-info)`
- `#eff6ff` (active bg) → `var(--color-info-light)`
- `box-shadow ... rgba(0,0,0,...)` → leave as-is (shadows are theme-tuned via variables elsewhere; do not block on these).

Also convert the `@apply`-based duplicates that name Tailwind colors so the chrome is correct even when the Play CDN is absent. For example:

```css
/* Sidebar — was: @apply ... bg-white border-r border-gray-200 shadow-sm; */
#sidebar {
    @apply fixed left-0 top-0 h-screen w-64;
    @apply hidden md:flex md:flex-col;
    background-color: var(--bg-primary);
    border-right: 1px solid var(--border-color);
    box-shadow: var(--shadow-sm);
    z-index: 50;
    overflow-y: auto;
    transition: width 0.3s ease;
}

/* Nav links — was: text-gray-700 ... hover:bg-gray-100 hover:text-gray-900 */
#sidebar-nav a,
#sidebar-nav button {
    @apply block w-full px-4 py-3 text-left rounded-lg font-medium transition-colors duration-200;
    color: var(--text-primary);
}
#sidebar-nav a:hover,
#sidebar-nav button:hover {
    background-color: var(--bg-tertiary);
    color: var(--text-primary);
}

/* Active nav — was: bg-blue-50 text-blue-600 border-blue-600 */
#sidebar-nav a.active,
#sidebar-nav button.active {
    background-color: var(--color-info-light);
    color: var(--color-info);
    border-left: 4px solid var(--color-info);
    padding-left: 0.75rem;
}
```

Apply the same pattern to `#bottom-nav`, `#bottom-nav-items`, `#sidebar-footer`, `#sidebar-header`, `.btn-toggle-sidebar`, `.modal-panel`, and the modal header markup colors. The modal header element in the body (`bg-white`) is handled in this task too — change `sticky top-0 bg-white` to use an inline `style="background-color: var(--bg-primary)"` or a `.surface` wrapper, and the `text-gray-900`/`text-gray-500` in the modal to `.section-title`/`.text-muted`.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_theme_accessibility.py::TestBaseChromeThemed tests/test_ui_foundation.py -v`
Expected: PASS for the new chrome tests. (Note: `test_ui_foundation.py` theme-toggle-in-base assertions still pass here — the toggle button is removed in Task 5, which updates those tests.)

- [x] **Step 5: Commit**

```bash
git add finapp/templates/base.html tests/test_theme_accessibility.py
git commit -m "feat: theme base.html chrome via CSS variables"
```

---

### Task 4: Add `system` mode to theme.js

Extend the theme engine to support `light` | `dark` | `system`, resolving `system` via `prefers-color-scheme` with a live listener, and expose `setTheme(mode)`.

**Files:**
- Modify: `finapp/static/theme.js`
- Modify: `tests/test_ui_foundation.py` (`TestThemeJSContent`) to assert the new API

**Interfaces:**
- Produces: globals `window.setTheme(mode)` and `window.getThemeMode()`; keeps `window.toggleTheme()` as a shim. Consumed by Task 5 (Settings control).

- [x] **Step 1: Write the failing test**

Add to `TestThemeJSContent` in `tests/test_ui_foundation.py`:

```python
    def test_set_theme_function_defined(self):
        assert "function setTheme" in self.content or "setTheme =" in self.content

    def test_set_theme_exposed_globally(self):
        assert "window.setTheme" in self.content

    def test_system_mode_supported(self):
        assert "system" in self.content

    def test_prefers_color_scheme_used(self):
        assert "prefers-color-scheme" in self.content

    def test_matchmedia_used(self):
        assert "matchMedia" in self.content
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ui_foundation.py::TestThemeJSContent -v`
Expected: FAIL — new assertions not satisfied.

- [x] **Step 3: Rewrite `finapp/static/theme.js`**

Replace the file body with a version that supports `system`:

```javascript
/**
 * Theme engine — light / dark / system with localStorage persistence.
 * Spec: docs/superpowers/specs/2026-06-17-dark-light-accessibility-and-sidebar-collapse-design.md
 *
 * Stored under localStorage key "theme" as one of: "light" | "dark" | "system".
 * "system" resolves via prefers-color-scheme and updates live on OS change.
 */
(function () {
  'use strict';

  var MEDIA = window.matchMedia('(prefers-color-scheme: dark)');

  function storedMode() {
    var m = localStorage.getItem('theme');
    return (m === 'light' || m === 'dark' || m === 'system') ? m : 'light';
  }

  function resolve(mode) {
    if (mode === 'system') {
      return MEDIA.matches ? 'dark' : 'light';
    }
    return mode;
  }

  function applyResolved(resolved) {
    var html = document.documentElement;
    if (resolved === 'dark') {
      html.classList.add('dark-theme');
      html.classList.remove('light-theme');
    } else {
      html.classList.remove('dark-theme');
      html.classList.add('light-theme');
    }
  }

  // React to OS changes only while in system mode.
  function onSystemChange() {
    if (storedMode() === 'system') {
      applyResolved(resolve('system'));
    }
  }
  if (MEDIA.addEventListener) {
    MEDIA.addEventListener('change', onSystemChange);
  } else if (MEDIA.addListener) {
    MEDIA.addListener(onSystemChange); // older browsers
  }

  /** Set and persist the theme mode. mode: 'light' | 'dark' | 'system'. */
  function setTheme(mode) {
    if (mode !== 'light' && mode !== 'dark' && mode !== 'system') {
      mode = 'light';
    }
    localStorage.setItem('theme', mode);
    applyResolved(resolve(mode));
  }

  /** Return the stored mode ('light' | 'dark' | 'system'). */
  function getThemeMode() {
    return storedMode();
  }

  /** Back-compat: flip between light and dark explicitly. */
  function toggleTheme() {
    var resolved = document.documentElement.classList.contains('dark-theme') ? 'dark' : 'light';
    setTheme(resolved === 'dark' ? 'light' : 'dark');
  }

  // Apply saved preference immediately (before first paint).
  applyResolved(resolve(storedMode()));

  window.setTheme = setTheme;
  window.getThemeMode = getThemeMode;
  window.toggleTheme = toggleTheme;
}());
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui_foundation.py::TestThemeJSContent -v`
Expected: PASS (including existing assertions: `toggleTheme`, `dark-theme`, `localStorage.setItem`/`getItem`, `window.toggleTheme`, `'light'` default).

- [x] **Step 5: Commit**

```bash
git add finapp/static/theme.js tests/test_ui_foundation.py
git commit -m "feat: add system theme mode + setTheme() to theme.js"
```

---

### Task 5: Move theme control to Settings "Appearance" section

Remove the sidebar theme-toggle button; add an Appearance section to `settings.html` with a Light/Dark/System segmented control; update the affected `test_ui_foundation.py` assertions.

**Files:**
- Modify: `finapp/templates/base.html` (remove theme-toggle button, ≈ lines 481–485)
- Modify: `finapp/templates/settings.html` (add Appearance section)
- Modify: `tests/test_ui_foundation.py` (`TestBaseHTMLIntegration` — move toggle assertions to settings)
- Modify: `tests/test_settings.py` (add Appearance render assertion)

**Interfaces:**
- Consumes: `window.setTheme(mode)` and `window.getThemeMode()` from Task 4.

- [x] **Step 1: Write/adjust the failing tests**

In `tests/test_ui_foundation.py`, **replace** the `TestBaseHTMLIntegration` theme-toggle assertions (`test_theme_toggle_button_present`, `test_theme_toggle_calls_toggle_function`, `test_theme_icon_span_present`) with:

```python
    def test_sidebar_theme_toggle_removed(self):
        # The theme control now lives in Settings, not the sidebar.
        assert "js-theme-toggle" not in self.content
        assert "toggleTheme()" not in self.content
```

Keep `test_theme_toggle_has_aria_label` only if `aria-label=` still appears elsewhere in base.html (it does — sidebar collapse button); otherwise remove it.

Append to `tests/test_settings.py` a content test (mirror the file-read pattern used in `test_ui_foundation.py`):

```python
from pathlib import Path

SETTINGS_HTML = Path(__file__).parent.parent / "finapp" / "templates" / "settings.html"


class TestAppearanceSection:
    def setup_method(self):
        self.content = SETTINGS_HTML.read_text()

    def test_appearance_heading_present(self):
        assert "Appearance" in self.content

    def test_theme_options_present(self):
        for label in ("Light", "Dark", "System"):
            assert label in self.content

    def test_calls_set_theme(self):
        assert "setTheme(" in self.content
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui_foundation.py::TestBaseHTMLIntegration tests/test_settings.py::TestAppearanceSection -v`
Expected: FAIL — toggle still in base.html; Appearance section absent.

- [x] **Step 3: Remove the sidebar theme-toggle button**

In `finapp/templates/base.html`, delete the theme-toggle button block:

```html
<!-- DELETE these lines -->
<button type="button" class="btn-theme-toggle js-theme-toggle mt-1" onclick="toggleTheme()"
    aria-label="Switch to dark theme" title="Switch to dark theme">
    <span class="js-theme-icon">🌙</span>
</button>
```

- [x] **Step 4: Add the Appearance section to `settings.html`**

Insert as the first section (after the `<header>`), using themed classes:

```html
<!-- Appearance -->
<section class="surface p-6">
    <h2 class="section-title text-lg font-semibold mb-4">Appearance</h2>
    <p class="text-muted text-sm mb-4">Choose how the app looks. “System” follows your device setting.</p>
    <div id="theme-options" class="inline-flex rounded-lg border border-gray-200 overflow-hidden"
         role="group" aria-label="Theme">
        <button type="button" class="theme-option px-4 py-2 text-sm font-medium" data-mode="light"
            onclick="selectTheme('light')">Light</button>
        <button type="button" class="theme-option px-4 py-2 text-sm font-medium" data-mode="dark"
            onclick="selectTheme('dark')">Dark</button>
        <button type="button" class="theme-option px-4 py-2 text-sm font-medium" data-mode="system"
            onclick="selectTheme('system')">System</button>
    </div>
</section>

<script>
    function selectTheme(mode) {
        window.setTheme(mode);
        highlightActiveTheme();
    }
    function highlightActiveTheme() {
        var active = window.getThemeMode();
        document.querySelectorAll('.theme-option').forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-mode') === active);
        });
    }
    document.addEventListener('DOMContentLoaded', highlightActiveTheme);
</script>
```

Add a `.theme-option.active` style to `theme.css` (active = info background) so the selected mode is visible:

```css
.theme-option {
  background-color: var(--bg-primary);
  color: var(--text-primary);
  border: none;
  cursor: pointer;
}
.theme-option + .theme-option {
  border-left: 1px solid var(--border-color);
}
.theme-option.active {
  background-color: var(--color-info-light);
  color: var(--color-info);
}
```

(Convert the `border-gray-200` on the wrapper to a `.surface`-consistent border in Task 7's settings pass; leaving it here is fine until then since it is part of the conversion list.)

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui_foundation.py::TestBaseHTMLIntegration tests/test_settings.py::TestAppearanceSection tests/test_settings.py -v`
Expected: PASS (and existing `test_settings.py` router tests still pass).

- [x] **Step 6: Commit**

```bash
git add finapp/templates/base.html finapp/templates/settings.html finapp/static/theme.css tests/test_ui_foundation.py tests/test_settings.py
git commit -m "feat: move theme control to Settings Appearance (light/dark/system)"
```

---

### Task 6: Full-width sliding sidebar collapse switch

Move "Add Transaction" into the nav list and make the footer a single full-width sliding collapse switch as the last sidebar element.

**Files:**
- Modify: `finapp/templates/base.html` (sidebar markup + footer + `<style>` for the switch)
- Modify: `tests/test_ui_foundation.py` (add sidebar-structure assertions)

**Interfaces:**
- Consumes: existing `toggleSidebar()` / `initializeSidebar()` JS (kept; extended to slide the knob).

- [x] **Step 1: Write the failing test**

Append a class to `tests/test_ui_foundation.py`:

```python
class TestSidebarCollapseSwitch:
    @pytest.fixture(autouse=True)
    def html(self):
        self.content = (Path(__file__).parent.parent / "finapp" / "templates" / "base.html").read_text()

    def test_collapse_switch_present(self):
        assert "btn-collapse-switch" in self.content

    def test_collapse_switch_is_after_add_transaction(self):
        # Add Transaction must appear before the collapse switch (switch is last).
        add_pos = self.content.index("openAddTransactionModal()")
        switch_pos = self.content.index("btn-collapse-switch")
        assert add_pos < switch_pos, "collapse switch must be the last sidebar element"

    def test_add_transaction_in_nav(self):
        # Add Transaction now lives inside the nav list, not a separate footer.
        nav_start = self.content.index('id="sidebar-nav"')
        nav_end = self.content.index("</nav>", nav_start)
        assert "openAddTransactionModal()" in self.content[nav_start:nav_end]

    def test_switch_has_sliding_knob(self):
        assert "collapse-knob" in self.content
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ui_foundation.py::TestSidebarCollapseSwitch -v`
Expected: FAIL — new markup absent.

- [x] **Step 3: Restructure the sidebar in `finapp/templates/base.html`**

Add "Add Transaction" as the last entry inside `#sidebar-nav` (after the Settings link), keeping it consistent with nav items:

```html
            <a href="/settings" class="nav-item" data-route="settings">
                <!-- (existing settings svg + label) -->
            </a>
            <!-- Add Transaction (last nav entry) -->
            <button type="button" class="nav-item" onclick="openAddTransactionModal()" title="Add Transaction">
                <svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
                </svg>
                <span class="nav-label">Add Transaction</span>
            </button>
        </nav>
```

Replace the entire `#sidebar-footer` block with the full-width sliding switch:

```html
        <div id="sidebar-footer">
            <button type="button" id="btn-collapse-switch" class="btn-collapse-switch"
                onclick="toggleSidebar()" aria-label="Collapse sidebar" title="Collapse sidebar">
                <span class="collapse-knob" id="collapse-knob">
                    <span id="toggle-icon">‹</span>
                </span>
                <span class="collapse-label nav-label">Collapse</span>
            </button>
        </div>
```

Add the switch styles to the `<style>` block (variable-backed):

```css
/* Full-width sliding collapse switch (last sidebar element) */
.btn-collapse-switch {
    position: relative;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    width: 100%;
    height: 40px;
    padding: 0 0.5rem;
    background-color: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: 999px;
    color: var(--text-primary);
    cursor: pointer;
    transition: background-color var(--transition-normal);
}
.btn-collapse-switch:hover {
    background-color: var(--bg-secondary);
}
.collapse-knob {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 999px;
    background-color: var(--bg-primary);
    box-shadow: var(--shadow-sm);
    transition: transform var(--transition-slow);
}
/* Collapsed: slide the knob to the right end of the track (toggle-switch feel). */
#sidebar.collapsed .btn-collapse-switch {
    justify-content: center;
}
#sidebar.collapsed .collapse-knob {
    transform: translateX(0);
}
.btn-collapse-switch .collapse-knob {
    transform: translateX(0);
}
/* When expanded the knob rests at the start; when collapsed it slides across. */
#sidebar:not(.collapsed) .collapse-knob {
    transform: translateX(0);
}
#sidebar.collapsed .collapse-knob {
    transform: translateX(calc(100% + 0.5rem));
}
```

> Note: the slide is expressed as the knob translating along the full-width track. Because the global `prefers-reduced-motion` rule in `theme.css` zeroes `transition-duration`, reduced-motion users get an instant change automatically.

Keep `toggleSidebar()`, `initializeSidebar()`, and `updateToggleIcon()` as-is — they already toggle `#sidebar.collapsed`, persist `sidebar-collapsed`, and flip the `#toggle-icon` chevron, which now lives inside the knob.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui_foundation.py::TestSidebarCollapseSwitch tests/test_ui_foundation.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add finapp/templates/base.html tests/test_ui_foundation.py
git commit -m "feat: full-width sliding sidebar collapse switch as last item"
```

---

### Task 7: Convert remaining templates to themed classes + global guard

Replace all remaining hardcoded color classes across the page templates and assert zero remain anywhere.

**Files:**
- Modify: `finapp/templates/dashboard.html`, `budget.html`, `transactions.html`, `debt.html`, `debt_projection.html`, `savings.html`, `reviews.html`, `onboarding.html`, `assets.html`, `settings.html` (finish any spots from Task 5)
- Modify: `tests/test_theme_accessibility.py` (global guard test)

**Interfaces:**
- Consumes: semantic classes from Task 2 (`.surface`, `.page-title`, `.section-title`, `.input-disabled`, `.link-info`), plus existing `.text-muted`, `.form-input`, `.form-label`, `.card`.

- [x] **Step 1: Write the failing guard test**

Append to `tests/test_theme_accessibility.py`:

```python
TEMPLATES_DIR = REPO_ROOT / "finapp" / "templates"

# Color-bearing Tailwind tokens that must not appear in any template after conversion.
FORBIDDEN = re.compile(
    r"\b(?:text-gray-\d{2,3}|bg-gray-\d{2,3}|border-gray-\d{2,3}"
    r"|divide-gray-\d{2,3}|text-blue-\d{2,3}|bg-blue-\d{2,3}|bg-white)\b"
)


def _all_templates():
    return sorted(TEMPLATES_DIR.glob("*.html"))


@pytest.mark.parametrize("template", _all_templates(), ids=lambda p: p.name)
def test_no_hardcoded_color_classes(template):
    text = template.read_text()
    hits = FORBIDDEN.findall(text)
    assert not hits, f"{template.name}: {len(hits)} hardcoded color classes remain: {sorted(set(hits))}"
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_theme_accessibility.py -k no_hardcoded_color -v`
Expected: FAIL for most templates, each listing the remaining tokens.

- [x] **Step 3: Convert each template using the mapping**

For every template, apply these replacements (color only — leave layout/spacing utilities untouched):

| Hardcoded | Replacement |
| --- | --- |
| `bg-white rounded-xl border border-gray-200` (and `…rounded-2xl…`) | `surface` (keep the radius utility if it differs from `.surface`'s `--radius-md`) |
| `text-gray-900` on `<h1>` | `page-title` |
| `text-gray-900` on `<h2>`/`<h3>` | `section-title` |
| `text-gray-700` (labels) | `form-label` (for `<label>`) or `text-primary-color` |
| `text-gray-600` / `text-gray-500` / `text-gray-400` | `text-muted` |
| `border-gray-300` / `border-gray-200` on inputs/selects | `form-input` (inputs) or drop in favor of `.surface` border |
| `bg-gray-50 text-gray-400` (disabled input) | `input-disabled` |
| `text-blue-600` | `link-info` |
| `bg-blue-50` | inline `style="background-color: var(--color-info-light)"` or a `.card-accent-info` |
| `hover:bg-gray-50` | remove; rely on `.btn-secondary`/themed hover |
| `divide-gray-100` | inline `style="border-color: var(--border-color)"` on items, or drop |

Work one file at a time. After each file, re-run the guard for that file:

```bash
uv run pytest tests/test_theme_accessibility.py -k "no_hardcoded_color and dashboard" -v
```

Repeat for `budget`, `transactions`, `debt`, `debt_projection`, `savings`, `reviews`, `onboarding`, `assets`, `settings`. Finish the `settings.html` Appearance wrapper border from Task 5 here (swap `border-gray-200` to a `.surface`-consistent border).

Preserve intentional, theme-stable accents: amber badge (`--color-warning` / `#F59E0B`), hero-card gradients, and `text-white` on colored backgrounds — none are in the FORBIDDEN set.

- [x] **Step 4: Run the full guard + accessibility + UI suites to verify they pass**

Run: `uv run pytest tests/test_theme_accessibility.py tests/test_ui_foundation.py -v`
Expected: PASS for every template (guard reports zero hits) and all theme/contrast/structure tests.

- [x] **Step 5: Run the whole test suite to confirm no regressions**

Run: `uv run pytest tests/ -q`
Expected: PASS (no backend/money tests affected).

- [x] **Step 6: Commit**

```bash
git add finapp/templates/ tests/test_theme_accessibility.py
git commit -m "feat: convert all templates to themed classes (no hardcoded colors)"
```

---

### Task 8: Manual verification sweep

Automated tests cover structure and token contrast; this task covers the rendered experience the spec's Done gate requires.

**Files:** none (verification only)

- [x] **Step 1: Start the app**

Run: `uv run python finapp/run.py` (serves `127.0.0.1:5000`). Verified: all pages return 200 (`/debt/{id}/projection` 404s only because no debt is seeded, not a template fault). theme.js loads before the Tailwind CDN (no FOUC), the sidebar theme toggle is gone, `btn-collapse-switch`/`collapse-knob`/`toggle-icon` render as the last sidebar element, and the Settings → Appearance Light/Dark/System control is wired to `setTheme`/`getThemeMode`. Full suite: 452 passed.

> **Headless-environment note:** Steps 2–4 below require a human looking at rendered pixels and changing OS appearance. No browser is available in this environment (Chrome cannot be installed without admin; Playwright MCP is pinned to the `chrome` channel), so the *visual* portions could not be automated. Structure and token contrast are fully covered by `tests/test_theme_accessibility.py` + `tests/test_ui_foundation.py` (80 tests). The pixel-level checks below are left for human confirmation.

- [ ] **Step 2: Theme sweep across every page**

For each of `/dashboard`, `/budget`, `/transactions`, `/debt`, `/savings`, `/reviews`, `/settings`, `/onboarding`, `/assets`, and the debt projection view: set Light, then Dark, then System (via Settings → Appearance). Confirm no text or control disappears, surfaces/chrome theme correctly, and the choice persists across a page reload.

- [ ] **Step 3: System mode live-change**

While in System mode, change the OS appearance (Windows: Settings → Personalization → Colors). Confirm the app flips live and shows no flash on reload.

- [ ] **Step 4: Sidebar switch**

Confirm the collapse switch is the last sidebar element, spans full width, and its knob slides on click; collapse/expand persists across reloads in both states. With OS "reduce motion" enabled, confirm the slide is instant.

- [ ] **Step 5: Record results**

Note the outcome in the PR description (pages checked, both themes, switch behavior). If any pairing looked weak in practice, add it to `TEXT_PAIRINGS` in Task 1's test and fix the token.

---

## Self-Review

**Spec coverage:**
- §1 Theme tokens & contrast → Task 1 (+ token fix).
- §1 New semantic classes → Task 2.
- §2 Template conversion (all 11) → base.html in Task 3/6, settings.html in Task 5/7, remaining pages in Task 7 (guard covers all).
- §3 base.html chrome rewrite → Task 3.
- §4 Theme control to Settings (Light/Dark/System, system mode, persistence) → Task 4 (engine) + Task 5 (UI).
- §5 Sidebar collapse switch (last item, full width, sliding, Add Transaction into nav) → Task 6.
- §6 Verification (contrast table, manual sweep, system live, switch, no money regressions) → Tasks 1, 7 (suite), 8 (manual).

**Placeholder scan:** No TBD/TODO; every code step contains concrete code and exact commands.

**Type/name consistency:** `setTheme`/`getThemeMode`/`toggleTheme` (Task 4) are the exact names used by Task 5's `selectTheme`/`highlightActiveTheme`. `btn-collapse-switch`, `collapse-knob`, `toggle-icon` (Task 6 markup) match the Task 6 tests and the retained `updateToggleIcon()`. `.surface`/`.page-title`/`.section-title`/`.input-disabled`/`.link-info` defined in Task 2 are the exact classes used in Tasks 3, 5, 7. The `FORBIDDEN` regex in Task 7 matches the tokens the mapping tables eliminate.
