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
