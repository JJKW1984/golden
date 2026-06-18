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
