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


# ---------------------------------------------------------------------------
# Global guard: zero hardcoded color classes in ANY template
# ---------------------------------------------------------------------------

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
