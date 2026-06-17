# Base Template Layout Documentation

## Overview

The `finapp/templates/base.html` template implements a responsive two-tier navigation system per Decision T2 of the Phase 4 specification:

- **Desktop (768px+):** Persistent left sidebar with full navigation
- **Mobile (<768px):** Bottom navigation bar with floating action button

## Layout Structure

### Desktop Layout (≥768px)

```
┌──────────────────────────────────────────────────────────┐
│                    BROWSER WINDOW                         │
├───────┬──────────────────────────────────────────────────┤
│       │                                                  │
│ SIDE  │              MAIN CONTENT AREA                   │
│ BAR   │              (with padding-left)                 │
│ (256) │                                                  │
│       │                                                  │
│ 7nav  │                                                  │
│ +btn  │                                                  │
│       │                                                  │
└───────┴──────────────────────────────────────────────────┘
```

**Sidebar (fixed, left=0):**
- Width: 256px (16rem)
- Position: fixed on left side
- Height: 100% viewport height
- Content:
  - Header: "FinApp" + tagline
  - Nav items: Dashboard, Budget, Transactions, Debt, Savings, Reviews, Settings
  - Footer: "Add Transaction" button

**Main Content:**
- Margin-left: 256px (to accommodate sidebar)
- Padding: 24-32px (responsive)
- Padding-bottom: 32px (safe distance from view bottom)

### Mobile Layout (<768px)

```
┌──────────────────────────────────────┐
│      MAIN CONTENT AREA               │
│      (full width)                    │
│                                      │
│                                      │
│                                      │
│                  [+] FLOATING BTN    │
│                                      │
├──────────────────────────────────────┤
│ BOTTOM NAVIGATION (5 items)          │
│ 🔷 Budget  📊  ⏱️  💳  💰            │
└──────────────────────────────────────┘
```

**Bottom Navigation (fixed, bottom=0):**
- Height: 80px (20 * 4)
- Full width
- 5 nav items (abbreviated):
  - Dashboard → 🔷
  - Budget → 📊
  - Transactions → ⏱️
  - Debt → 💳
  - Savings → 💰
- Reviews & Settings hidden (not enough room)

**Floating Action Button:**
- Position: fixed
- Bottom: 96px from viewport bottom (24px + 80px nav height)
- Right: 16px
- Size: 56x56px (14 * 4)
- Icon: Plus symbol
- Z-index: 40 (above nav at z=50)

**Add Transaction Modal:**
- Overlay backdrop: Semi-transparent black
- Panel:
  - Mobile: Slides up from bottom, max 90vh height
  - Desktop: Centered, max-width 448px
- Z-index: 60 (above all other elements)

## Responsive Breakpoints

### Tailwind `md:` Prefix = 768px

The template uses the `md:` Tailwind breakpoint (768px) for all responsive behavior:

- `hidden md:flex` - Hide on mobile, show on desktop
- `md:hidden` - Show on mobile, hide on desktop
- `md:pb-8` - Different padding-bottom values
- `md:bottom-8` - Different positioning for floating button
- etc.

## Navigation Items (All 7)

1. **Dashboard** - Overview and daily check-in
2. **Budget** - Category allocation and spending
3. **Transactions** - Transaction list and detail
4. **Debt** - Debt accounts and payoff
5. **Savings** - Savings goals and progress
6. **Reviews** - Weekly and monthly reviews
7. **Settings** - App configuration and backup

**Mobile abbreviated labels:** Dashboard, Budget, Trans, Debt, Save

## Add Transaction Affordance

### Desktop
- **Location:** Sidebar footer (sticky)
- **Style:** Full-width blue button with icon + text
- **Behavior:** Click opens modal
- **Always visible:** Yes, in sidebar

### Mobile
- **Location:** Floating action button (FAB)
- **Style:** 56x56px circle, blue background, white icon
- **Position:** Bottom-right corner with safe margins
- **Behavior:** Click opens modal
- **Always visible:** Yes, above bottom nav

## Add Transaction Modal

**Structure:**
```
┌────────────────────────────────┐
│ Add Transaction          [X]   │
├────────────────────────────────┤
│                                │
│  Form Content Here             │
│  (wired in Phase 5)            │
│                                │
│  [Save]        [Cancel]        │
├────────────────────────────────┘
```

**Features:**
- Full-screen backdrop (click to close)
- Header with close button (X)
- Escape key to close
- Cancel button to close
- Body scrollable if content overflows
- Form placeholder (for Phase 5 wiring)

**Mobile Styling:**
- Slides up from bottom
- Rounded top corners (16px radius)
- Max height: 90vh of viewport

**Desktop Styling:**
- Centered on screen
- Max width: 448px (md breakpoint)
- Rounded all corners (8px radius)

## Template Blocks (for child templates)

All child pages should extend `base.html` and override these blocks:

```jinja2
{% extends "base.html" %}

{% block title %}Page Title{% endblock %}

{% block extra_css %}
  <!-- Page-specific CSS here -->
{% endblock %}

{% block content %}
  <!-- Page content here -->
{% endblock %}

{% block extra_js %}
  <!-- Page-specific JavaScript here -->
{% endblock %}
```

## CSS Custom Properties (Colors)

Defined for consistency:

```css
--color-success: #10b981    (green)
--color-warning: #f59e0b    (amber - never red per spec)
--color-danger: #ef4444     (red)
--color-info: #3b82f6       (blue)
--color-accent: #0ea5e9     (cyan)
```

## Tech Stack

- **Tailwind CSS:** Utility-first framework via CDN (Play)
- **HTMX:** Server-side HTML swaps for interactivity
- **Alpine.js:** Optional for pure client-side UI toggles
- **Chart.js:** For debt/savings projection charts
- **Jinja2:** FastAPI template engine

## Security

All external scripts include:
- **Subresource Integrity (SRI) hashes** to verify CDN content
- **crossorigin="anonymous"** attribute for CORS

## File Location

`/d/golden/finapp/templates/base.html`

## Size

- **Lines:** 384
- **Size:** ~16 KB
- **Gzipped:** ~4-5 KB (typical web serving)

## Ready For

- **Phase 4:** Dashboard page injection
- **Phase 5:** Transaction form wiring + budget screen
- **Phase 6+:** All other page screens
