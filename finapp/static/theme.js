/**
 * Theme toggle — light / dark mode with localStorage persistence.
 * Spec: docs/superpowers/specs/2026-06-17-ui-design-system.md, Part 7
 *
 * Usage:
 *   Call toggleTheme() from a button's onclick handler.
 *   Theme is saved to localStorage under the key "theme" ("light" or "dark").
 */

(function () {
  'use strict';

  /**
   * Apply the given theme ('light' or 'dark') to <html> and update any
   * toggle button icons already in the DOM.
   * @param {string} theme - 'light' or 'dark'
   */
  function applyTheme(theme) {
    var html = document.documentElement;
    if (theme === 'dark') {
      html.classList.add('dark-theme');
      html.classList.remove('light-theme');
    } else {
      html.classList.remove('dark-theme');
      html.classList.add('light-theme');
    }
    updateToggleIcons(theme);
  }

  /**
   * Update the aria-label and icon of every .js-theme-toggle button
   * to reflect the current theme.
   * @param {string} theme - 'light' or 'dark'
   */
  function updateToggleIcons(theme) {
    var buttons = document.querySelectorAll('.js-theme-toggle');
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      var icon = btn.querySelector('.js-theme-icon');
      if (theme === 'dark') {
        btn.setAttribute('aria-label', 'Switch to light theme');
        btn.setAttribute('title', 'Switch to light theme');
        if (icon) { icon.textContent = '☀️'; }
      } else {
        btn.setAttribute('aria-label', 'Switch to dark theme');
        btn.setAttribute('title', 'Switch to dark theme');
        if (icon) { icon.textContent = '🌙'; }
      }
    }
  }

  /**
   * Toggle between light and dark themes, persisting the choice.
   * Called from the toggle button's onclick.
   */
  function toggleTheme() {
    var current = document.documentElement.classList.contains('dark-theme')
      ? 'dark'
      : 'light';
    var next = current === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', next);
    applyTheme(next);
  }

  // Restore saved preference on every page load (runs immediately on script parse).
  var savedTheme = localStorage.getItem('theme') || 'light';
  applyTheme(savedTheme);

  // Expose toggleTheme globally so inline onclick handlers can call it.
  window.toggleTheme = toggleTheme;
}());
