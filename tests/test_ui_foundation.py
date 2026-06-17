"""
Tests for UI Design System Foundation.

Validates that:
- theme.css exists and contains the required CSS custom properties
- theme.js exists and contains the theme toggle logic
- base.html links to theme.css and theme.js
- Static files are served correctly by the FastAPI app
- Theme toggle button is present in base.html

Spec: docs/superpowers/specs/2026-06-17-ui-design-system.md
"""
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
STATIC_DIR = REPO_ROOT / "finapp" / "static"
TEMPLATES_DIR = REPO_ROOT / "finapp" / "templates"
THEME_CSS = STATIC_DIR / "theme.css"
THEME_JS = STATIC_DIR / "theme.js"
BASE_HTML = TEMPLATES_DIR / "base.html"
MAIN_PY = REPO_ROOT / "finapp" / "main.py"


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------

class TestStaticFilesExist:
    def test_static_directory_exists(self):
        assert STATIC_DIR.is_dir(), "finapp/static/ directory must exist"

    def test_theme_css_exists(self):
        assert THEME_CSS.is_file(), "finapp/static/theme.css must exist"

    def test_theme_js_exists(self):
        assert THEME_JS.is_file(), "finapp/static/theme.js must exist"


# ---------------------------------------------------------------------------
# theme.css content
# ---------------------------------------------------------------------------

class TestThemeCSSContent:
    @pytest.fixture(autouse=True)
    def css(self):
        self.content = THEME_CSS.read_text()

    def test_root_light_theme_bg_primary(self):
        assert "--bg-primary:" in self.content

    def test_root_light_theme_text_primary(self):
        assert "--text-primary:" in self.content

    def test_root_light_theme_border_color(self):
        assert "--border-color:" in self.content

    def test_dark_theme_selector(self):
        assert "html.dark-theme" in self.content

    def test_dark_theme_overrides_bg_primary(self):
        # dark-theme block must come after :root and override --bg-primary
        dark_start = self.content.index("html.dark-theme")
        assert "--bg-primary:" in self.content[dark_start:]

    def test_core_teal_color(self):
        assert "--color-teal:" in self.content
        assert "#14b8a6" in self.content

    def test_core_gold_color(self):
        assert "--color-gold:" in self.content
        assert "#d97706" in self.content

    def test_core_success_color(self):
        assert "--color-success:" in self.content
        assert "#10b981" in self.content

    def test_core_warning_color(self):
        assert "--color-warning:" in self.content
        assert "#f59e0b" in self.content

    def test_core_danger_color(self):
        assert "--color-danger:" in self.content
        assert "#ef4444" in self.content

    def test_core_info_color(self):
        assert "--color-info:" in self.content
        assert "#3b82f6" in self.content

    def test_font_family_variable(self):
        assert "--font-family:" in self.content

    def test_spacing_variables_present(self):
        for name in ("--space-xs", "--space-sm", "--space-md",
                     "--space-lg", "--space-xl", "--space-2xl"):
            assert name in self.content, f"Expected spacing variable {name}"

    def test_border_radius_variables(self):
        for name in ("--radius-sm", "--radius-md", "--radius-lg"):
            assert name in self.content, f"Expected radius variable {name}"

    def test_reduced_motion_media_query(self):
        assert "prefers-reduced-motion" in self.content

    def test_btn_primary_class(self):
        assert ".btn-primary" in self.content

    def test_btn_secondary_class(self):
        assert ".btn-secondary" in self.content

    def test_card_class(self):
        assert ".card" in self.content

    def test_progress_bar_track_class(self):
        assert ".progress-bar-track" in self.content

    def test_expandable_content_class(self):
        assert ".expandable-content" in self.content

    def test_theme_toggle_button_class(self):
        assert ".btn-theme-toggle" in self.content


# ---------------------------------------------------------------------------
# theme.js content
# ---------------------------------------------------------------------------

class TestThemeJSContent:
    @pytest.fixture(autouse=True)
    def js(self):
        self.content = THEME_JS.read_text()

    def test_toggle_function_defined(self):
        assert "toggleTheme" in self.content

    def test_dark_theme_class_used(self):
        assert "dark-theme" in self.content

    def test_localstorage_set(self):
        assert "localStorage.setItem" in self.content

    def test_localstorage_get_on_load(self):
        assert "localStorage.getItem" in self.content

    def test_toggle_exposed_globally(self):
        assert "window.toggleTheme" in self.content

    def test_default_theme_is_light(self):
        # If no saved preference, must default to 'light'
        assert "'light'" in self.content or '"light"' in self.content


# ---------------------------------------------------------------------------
# base.html integration
# ---------------------------------------------------------------------------

class TestBaseHTMLIntegration:
    @pytest.fixture(autouse=True)
    def html(self):
        self.content = BASE_HTML.read_text()

    def test_links_theme_css(self):
        assert 'href="/static/theme.css"' in self.content

    def test_links_theme_js(self):
        assert 'src="/static/theme.js"' in self.content

    def test_theme_js_before_tailwind(self):
        """theme.js must load before Tailwind to prevent flash of unstyled content."""
        theme_js_pos = self.content.index('/static/theme.js')
        tailwind_pos = self.content.index('cdn.tailwindcss.com')
        assert theme_js_pos < tailwind_pos, \
            "theme.js must appear before Tailwind CDN script in base.html"

    def test_theme_toggle_button_present(self):
        assert "js-theme-toggle" in self.content

    def test_theme_toggle_calls_toggle_function(self):
        assert "toggleTheme()" in self.content

    def test_theme_toggle_has_aria_label(self):
        assert 'aria-label=' in self.content

    def test_theme_icon_span_present(self):
        assert 'js-theme-icon' in self.content


# ---------------------------------------------------------------------------
# main.py static mount
# ---------------------------------------------------------------------------

class TestMainPyStaticMount:
    @pytest.fixture(autouse=True)
    def main(self):
        self.content = MAIN_PY.read_text()

    def test_staticfiles_imported(self):
        assert "StaticFiles" in self.content

    def test_static_mount_present(self):
        assert 'app.mount' in self.content
        assert '"/static"' in self.content or "'/static'" in self.content

    def test_static_dir_in_mount(self):
        assert "finapp/static" in self.content


# ---------------------------------------------------------------------------
# HTTP: static files served by FastAPI
# ---------------------------------------------------------------------------

class TestStaticFilesServed:
    """Integration test: FastAPI actually serves the static files."""

    @pytest.fixture(autouse=True)
    def client(self):
        from fastapi.testclient import TestClient
        from finapp.main import app
        self.client = TestClient(app)

    def test_theme_css_served(self):
        response = self.client.get("/static/theme.css")
        assert response.status_code == 200

    def test_theme_css_content_type(self):
        response = self.client.get("/static/theme.css")
        assert "text/css" in response.headers.get("content-type", "")

    def test_theme_js_served(self):
        response = self.client.get("/static/theme.js")
        assert response.status_code == 200

    def test_theme_js_content_type(self):
        response = self.client.get("/static/theme.js")
        content_type = response.headers.get("content-type", "")
        # application/javascript or text/javascript
        assert "javascript" in content_type
