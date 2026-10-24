"""Chart Cleaner's design system — Nothing-inspired tokens, components, helpers.

This module is the single source of truth for the visual language: colour,
type, spacing, motion and component shapes. Nothing else in the app hardcodes
a colour or a radius.

How page code keeps working
---------------------------
Pages were written against the historical ``--cc-*`` variables. Those names
are preserved, but they are now thin *aliases* onto the Nothing ``--nd-*``
tokens, so changing a token here reaches all nine screens at once.

Type roles (the whole system in four lines)
-------------------------------------------
``--nd-display``  Doto        — metric numerals only
``--nd-font``     Space Grotesk — page titles, section headings, and body
``--nd-mono``     Space Mono  — labels, ALL CAPS micro-type, data, buttons, nav
Status colour is applied to the *value*, never to a label or a row background.

Fonts are self-hosted under ``chartcleaner/static/fonts`` (see ``STATIC_DIR``)
because the app is offline-first and ``app.py`` strips any external font CDN
link from served HTML.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

# ``chartcleaner/static`` next to the package — resolves inside a PyInstaller
# bundle too, because the spec ships the folder as ``chartcleaner/static``.
STATIC_DIR = Path(__file__).resolve().parent.parent / "chartcleaner" / "static"


# ---------------------------------------------------------------------------
# brand mark
# ---------------------------------------------------------------------------

# A vitals pulse in a solid monochrome tile (inline SVG — no external assets).
BRAND_SVG = (
    '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" style="display:block">'
    '<rect x="0.5" y="0.5" width="21" height="21" rx="4" fill="var(--nd-text-display)"/>'
    '<path d="M4.6 11.4h2.7l1.4-3.4 2 6.8 1.6-3.8 1 2.1h3.5" fill="none" '
    'stroke="var(--nd-black)" stroke-width="1.6" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>')


# ---------------------------------------------------------------------------
# stylesheet
# ---------------------------------------------------------------------------

CSS = r"""
<style>
/* =====================================================================
   1. FONTS — self-hosted, offline (no CDN; app.py strips external links)
   ===================================================================== */
@font-face{font-family:"Doto";font-style:normal;font-weight:400 900;font-display:swap;
  src:url("/static/fonts/doto-latin.woff2") format("woff2");
  unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD;}
@font-face{font-family:"Doto";font-style:normal;font-weight:400 900;font-display:swap;
  src:url("/static/fonts/doto-latin-ext.woff2") format("woff2");
  unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF;}
@font-face{font-family:"Space Grotesk";font-style:normal;font-weight:300 700;font-display:swap;
  src:url("/static/fonts/space-grotesk-latin.woff2") format("woff2");
  unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD;}
@font-face{font-family:"Space Grotesk";font-style:normal;font-weight:300 700;font-display:swap;
  src:url("/static/fonts/space-grotesk-latin-ext.woff2") format("woff2");
  unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF;}
@font-face{font-family:"Space Mono";font-style:normal;font-weight:400;font-display:swap;
  src:url("/static/fonts/space-mono-400-latin.woff2") format("woff2");
  unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD;}
@font-face{font-family:"Space Mono";font-style:normal;font-weight:400;font-display:swap;
  src:url("/static/fonts/space-mono-400-latin-ext.woff2") format("woff2");
  unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF;}
@font-face{font-family:"Space Mono";font-style:normal;font-weight:700;font-display:swap;
  src:url("/static/fonts/space-mono-700-latin.woff2") format("woff2");
  unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD;}
@font-face{font-family:"Space Mono";font-style:normal;font-weight:700;font-display:swap;
  src:url("/static/fonts/space-mono-700-latin-ext.woff2") format("woff2");
  unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF;}

/* =====================================================================
   2. TOKENS
   ===================================================================== */
:root{
  /* --- type --------------------------------------------------------- */
  --nd-display:"Doto","Space Mono",monospace;
  --nd-font:"Space Grotesk","DM Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --nd-mono:"Space Mono","JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;

  --display-xl:72px; --display-lg:48px; --display-md:36px; --heading:24px;
  --subheading:18px; --body:16px; --body-sm:14px; --caption:12px; --label:12px;

  /* --- spacing (8px base) ------------------------------------------- */
  --nd-space-2xs:2px; --nd-space-xs:4px; --nd-space-sm:8px;  --nd-space-md:16px;
  --nd-space-lg:24px; --nd-space-xl:32px; --nd-space-2xl:48px; --nd-space-3xl:64px;
  --nd-space-4xl:96px;

  /* --- shape -------------------------------------------------------- */
  --nd-r-pill:999px; --nd-r-card:16px; --nd-r-compact:8px; --nd-r-tech:4px;

  /* --- motion: ease-out only, never spring or overshoot ------------- */
  --nd-ease:cubic-bezier(.22,.61,.36,1);
  --nd-dur-fast:140ms; --nd-dur:220ms; --nd-dur-slow:320ms;

  /* --- status colours ------------------------------------------------
     Hue stays put. Lightness is per theme so 12px status text clears
     WCAG 4.5:1 and APCA Lc 60 on that theme's surfaces. Light mode uses
     the darker ink; dark mode overrides these with the lighter ink. */
  --nd-accent:#D71921; --nd-accent-subtle:rgba(215,25,33,.15);
  --nd-success:#1D7637; --nd-warning:#7E5D00; --nd-error:#D71921;

  /* --- LIGHT (warm off-white paper, black ink) ---------------------- */
  --nd-black:#F5F5F5; --nd-surface:#FFFFFF; --nd-surface-raised:#F0F0F0;
  --nd-border:#E8E8E8; --nd-border-visible:#CCCCCC;
  --nd-text-disabled:#999999; --nd-text-secondary:#666666;
  --nd-text-primary:#1A1A1A; --nd-text-display:#000000;
  --nd-interactive:#007AFF;
  --nd-hover:rgba(0,0,0,.045); --nd-tint:rgba(0,0,0,.05);

  /* --- Quasar brand colours, re-pointed at the design tokens ---------
     Quasar's own `.text-primary` / `.bg-negative` utilities read these, so
     overriding the variables re-skins every Quasar-coloured element at once. */
  --q-primary:var(--nd-text-display); --q-secondary:var(--nd-text-secondary);
  --q-accent:var(--nd-text-display);
  --q-positive:var(--nd-success); --q-negative:var(--nd-accent);
  --q-info:var(--nd-interactive); --q-warning:var(--nd-warning);

  /* --- app aliases: pages read --cc-* ------------------------------- */
  --cc-font:var(--nd-font); --cc-mono:var(--nd-mono);
  --cc-bg:var(--nd-black); --cc-surface:var(--nd-surface);
  --cc-surface-2:var(--nd-surface-raised);
  --cc-border:var(--nd-border); --cc-border-strong:var(--nd-border-visible);
  --cc-text:var(--nd-text-primary); --cc-text-2:var(--nd-text-secondary);
  --cc-text-3:var(--nd-text-disabled);
  --cc-accent:var(--nd-text-display); --cc-accent-strong:var(--nd-text-display);
  --cc-on-accent:var(--nd-black);
  --cc-accent-soft:var(--nd-tint); --cc-accent-line:var(--nd-border-visible);
  --cc-pos:var(--nd-success); --cc-pos-soft:rgba(29,118,55,.16);
  --cc-neg:var(--nd-accent);  --cc-neg-soft:rgba(215,25,33,.12);
  --cc-warn:var(--nd-warning); --cc-warn-soft:rgba(126,93,0,.18);
  --cc-info:var(--nd-interactive); --cc-purple:var(--nd-text-secondary);
  --cc-header:var(--nd-black); --cc-shadow:none;
}
/* DARK — OLED black, white data. Also set on <html> so the overscroll area
   matches (Quasar puts the class on <body>). */
.body--dark, html:has(.body--dark){
  --nd-black:#000000; --nd-surface:#111111; --nd-surface-raised:#1A1A1A;
  --nd-border:#222222; --nd-border-visible:#333333;
  --nd-text-disabled:#666666; --nd-text-secondary:#999999;
  --nd-text-primary:#E8E8E8; --nd-text-display:#FFFFFF;
  --nd-interactive:#5B9BF6;
  --nd-hover:rgba(255,255,255,.05); --nd-tint:rgba(255,255,255,.06);
  --nd-accent:#FBADA4; --nd-accent-subtle:rgba(251,173,164,.22);
  --nd-success:#7ACE89; --nd-warning:#E3B753; --nd-error:#FBADA4;
  --cc-pos-soft:rgba(122,206,137,.18); --cc-neg-soft:rgba(251,173,164,.18);
  --cc-warn-soft:rgba(227,183,83,.18);
  /* Quasar re-declares its brand colours on .body--dark; point them back. */
  --q-primary:var(--nd-text-display); --q-secondary:var(--nd-text-secondary);
  --q-accent:var(--nd-text-display);
  --q-positive:var(--nd-success); --q-negative:var(--nd-accent);
  --q-info:var(--nd-interactive); --q-warning:var(--nd-warning);
}

/* =====================================================================
   3. BASE
   ===================================================================== */
html,body{background:var(--nd-black);}
body{font-family:var(--nd-font);font-size:var(--body);font-weight:400;
  color:var(--nd-text-primary);-webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;}
::selection{background:var(--nd-text-display);color:var(--nd-black);}
:focus-visible{outline:2px solid var(--nd-text-display);outline-offset:3px;
  animation:cc-focus var(--nd-dur-fast) var(--nd-ease);}
::-webkit-scrollbar{width:10px;height:10px;}
::-webkit-scrollbar-thumb{background:var(--nd-border-visible);border:3px solid var(--nd-black);
  background-clip:padding-box;border-radius:0;
  transition:background var(--nd-dur-fast) var(--nd-ease);}
::-webkit-scrollbar-thumb:hover{background:var(--nd-text-secondary);}
::-webkit-scrollbar-track{background:transparent;}

/* =====================================================================
   4. SHELL — header, nav, drawer, footer
   ===================================================================== */
.q-header{background:var(--nd-black);color:var(--nd-text-primary);min-height:0;
  border-bottom:1px solid var(--nd-border);box-shadow:none;
  backdrop-filter:none;-webkit-backdrop-filter:none;}
.q-header .q-btn--flat{color:var(--nd-text-secondary);}
/* ONE GRID. The header chrome, every page title and every page body share this
   width and this 24px inset, so the left edge never moves between screens.
   `.cc-band` carries the full-bleed hairline; `.cc-shell` carries the content. */
.cc-band{width:100%;border-bottom:1px solid var(--nd-border);}
.cc-shell{width:100%;max-width:1440px;margin:0 auto;padding:0 var(--nd-space-lg);}
.cc-topbar{min-height:56px;gap:var(--nd-space-md);}
.cc-navbar{display:flex;align-items:stretch;gap:0;
  overflow-x:auto;}
.cc-navlink,.cc-navgroup{display:flex;align-items:center;height:40px;padding:0 var(--nd-space-md);
  font-family:var(--nd-mono);font-size:12px;font-weight:400;letter-spacing:.06em;
  text-transform:uppercase;color:var(--nd-text-secondary);cursor:pointer;
  white-space:nowrap;text-decoration:none;background:none;border:0;
  transition:color var(--nd-dur-fast) var(--nd-ease);}
.cc-navlink:hover,.cc-navgroup:hover{color:var(--nd-text-display);}
.cc-navlink--active{color:var(--nd-text-display);}
.cc-navlink--active::before{content:"[ ";color:var(--nd-text-secondary);}
.cc-navlink--active::after{content:" ]";color:var(--nd-text-secondary);}
.cc-navgroup{min-height:40px;padding:0 var(--nd-space-md);}
.cc-navgroup--active{color:var(--nd-text-display);}
.cc-navgroup--active::before{content:"[ ";color:var(--nd-text-secondary);}
.cc-navgroup--active::after{content:" ]";color:var(--nd-text-secondary);}
.cc-nav-group{margin:var(--nd-space-md) var(--nd-space-md) var(--nd-space-xs);}
.cc-local-private{min-height:24px;padding:0 var(--nd-space-sm);
  color:var(--nd-text-secondary);font-size:var(--label);}
.cc-local-private-menu{background:var(--nd-surface-raised);}

.cc-summary-count{display:flex;flex-direction:column;gap:var(--nd-space-xs);
  min-width:112px;padding:var(--nd-space-sm) var(--nd-space-md);
  border:1px solid var(--nd-border);background:var(--nd-surface);}
.cc-summary-count-value{font-family:var(--nd-mono);font-size:20px;
  color:var(--nd-text-display);}
.cc-summary-count-label{font-family:var(--nd-mono);font-size:var(--label);
  letter-spacing:.06em;text-transform:uppercase;color:var(--nd-text-secondary);}
.cc-workflow-section{gap:var(--nd-space-sm);}
.cc-empty-state{color:var(--nd-text-secondary);}
.cc-task-flow{font-family:var(--nd-mono);font-size:var(--label);letter-spacing:.04em;}
.cc-task-flow__step{color:var(--nd-text-secondary);transition:color var(--nd-dur-fast) var(--nd-ease);}
.cc-task-flow__arrow{color:var(--nd-border-visible);}
.cc-task-flow[data-current-step="1"] [data-step="1"],
.cc-task-flow[data-current-step="2"] [data-step="2"],
.cc-task-flow[data-current-step="3"] [data-step="3"]{color:var(--nd-text-display);font-weight:600;}
.cc-result-header{padding-bottom:var(--nd-space-sm);border-bottom:1px solid var(--nd-border);}
.cc-mobile-workspace-switch{display:none;}
.cc-clean-workspace{display:grid;width:100%;align-items:start;}
.cc-split-handle{display:none;}
@media (min-width:1200px){
  .cc-clean-workspace{grid-template-columns:minmax(0,calc(50% - 4px)) 8px minmax(0,calc(50% - 4px));}
  .cc-split-handle{display:block;min-height:420px;cursor:col-resize;}
  .cc-split-handle:focus-visible{outline:2px solid var(--nd-text-display);}
}
@media (min-width:768px) and (max-width:1199px){
  .cc-clean-workspace{grid-template-columns:minmax(0,1fr) !important;row-gap:var(--nd-space-lg);}
}
@media (max-width:767px){
  .cc-mobile-workspace-switch{display:flex;}
  .cc-mobile-show-input .cc-result-pane,.cc-mobile-show-result .cc-input-pane{display:none !important;}
  .cc-clean-workspace{grid-template-columns:minmax(0,1fr) !important;}
}

.q-drawer{background:var(--nd-black);color:var(--nd-text-primary);}
.cc-drawer{background:var(--nd-black);}
.q-drawer--left{border-right:1px solid var(--nd-border);}
.q-drawer--right{border-left:1px solid var(--nd-border);}
.cc-nav-head{margin:var(--nd-space-lg) var(--nd-space-md) var(--nd-space-sm);}
.cc-nav-item{display:flex;justify-content:flex-start;min-height:46px;margin:0;
  padding:0 var(--nd-space-md);border-radius:0;border-left:2px solid transparent;
  font-family:var(--nd-mono);font-size:12px;font-weight:400;letter-spacing:.08em;
  text-transform:uppercase;color:var(--nd-text-secondary);
  transition:color var(--nd-dur-fast) var(--nd-ease),
             background var(--nd-dur-fast) var(--nd-ease),
             border-color var(--nd-dur) var(--nd-ease);}
.cc-nav-item:hover{color:var(--nd-text-display);background:var(--nd-hover);}
.cc-nav-active,.cc-nav-active:hover{color:var(--nd-text-display);background:transparent;
  border-left-color:var(--nd-text-display);font-weight:400;}
.cc-nav-active .q-icon{color:var(--nd-text-display);}
/* The drawer's status tags wrap instead of running off the panel. */
.nicegui-drawer .row{flex-wrap:wrap !important;}

.cc-footer{background:transparent;color:var(--nd-text-secondary);
  border-top:1px solid var(--nd-border);padding:var(--nd-space-md) 0;
  font-family:var(--nd-mono);font-size:12px;letter-spacing:.04em;}

.cc-desktop-only{display:flex !important;}
.cc-mobile-only{display:none !important;}

/* NiceGUI's own container defaults: the header becomes a two-row stack, the
   page container stops double-padding, the drawer runs edge to edge. */
.nicegui-header{flex-direction:column;align-items:stretch;flex-wrap:nowrap;gap:0;padding:0;}
.nicegui-content{padding:0;gap:0;align-items:stretch;}
.nicegui-drawer{padding:0;gap:0;align-items:stretch;}
/* Cards get a 16px interior — unless the page asked for its own. */
.nicegui-card:not([class*=" p-"]):not([class*="px-"]):not([class*="py-"]){padding:var(--nd-space-md);}
.nicegui-expansion-content{padding:0 var(--nd-space-md) var(--nd-space-md);gap:var(--nd-space-md);}
.nicegui-tab-panel{padding:0;gap:var(--nd-space-md);}

/* =====================================================================
   5. PAGE FRAME — three layers: eyebrow / display title / supporting line
   ===================================================================== */
.cc-page{width:100%;max-width:1440px;margin:0 auto;display:flex;flex-direction:column;
  gap:var(--nd-space-md);padding:var(--nd-space-md) var(--nd-space-lg) var(--nd-space-xl);}
/* Text-heavy screens (settings, doctor, rules…) opt into a readable measure.
   The cap lands on the children, so the page keeps the shared 1440 grid and
   its left edge stays flush with the nav — only the right edge pulls in. */
.cc-page--measure > *{max-width:1040px;}
.cc-pagehead{display:flex;flex-direction:column;gap:var(--nd-space-sm);
  animation:cc-arrive var(--nd-dur) var(--nd-ease);}
.cc-eyebrow{font-family:var(--nd-mono);font-size:var(--label);font-weight:400;
  letter-spacing:.12em;text-transform:uppercase;color:var(--nd-text-secondary);}
.cc-page-title{margin:0;font-family:var(--nd-font);font-size:28px;
  font-weight:600;letter-spacing:-.02em;line-height:1.02;color:var(--nd-text-display);}
.cc-section{margin:0;font-family:var(--nd-font);font-size:16px;font-weight:500;
  letter-spacing:-.005em;line-height:1.35;color:var(--nd-text-display);}
.cc-muted{font-family:var(--nd-font);font-size:13px;line-height:1.5;
  color:var(--nd-text-secondary);}
.cc-page-sub{font-family:var(--nd-font);font-size:var(--body-sm);
  color:var(--nd-text-secondary);max-width:78ch;}
.cc-rule{height:1px;background:var(--nd-border);}
.cc-headbtn{min-height:36px;min-width:36px;color:var(--nd-text-secondary);
  transition:color var(--nd-dur-fast) var(--nd-ease),
             background var(--nd-dur-fast) var(--nd-ease);}
.cc-headbtn:hover{color:var(--nd-text-display);background:var(--nd-hover);}
.cc-brand{display:inline-flex;align-items:center;font-family:var(--nd-mono);font-size:13px;
  font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--nd-text-display);
  text-decoration:none;cursor:pointer;}
.cc-version{font-family:var(--nd-mono);font-size:12px;letter-spacing:.08em;
  color:var(--nd-text-secondary);border:1px solid var(--nd-border-visible);
  border-radius:var(--nd-r-tech);padding:2px 6px;}

/* =====================================================================
   6. SURFACES — flat, border-separated, never shadowed
   ===================================================================== */
.q-card{background:var(--nd-surface);color:var(--nd-text-primary);
  border:1px solid var(--nd-border);border-radius:var(--nd-r-card);box-shadow:none;}
.q-separator{background:var(--nd-border);}
.q-menu{background:var(--nd-surface-raised);border:1px solid var(--nd-border-visible);
  border-radius:var(--nd-r-compact);box-shadow:none;}
.q-item{border-radius:0;min-height:44px;color:var(--nd-text-primary);}
.q-item__label{font-size:var(--body-sm);}
.q-banner{border-radius:var(--nd-r-compact);}
.q-splitter__panel{border-color:var(--nd-border);}

.cc-tint{border:1px solid var(--nd-border);border-radius:var(--nd-r-card);}
.cc-tint--accent{background:var(--nd-tint);border-color:var(--nd-border-visible);}
.cc-tint--warn{background:var(--cc-warn-soft);border-color:var(--nd-warning);}
/* Clean page selection modes: the chart box shows which tool is armed. */
.cc-mode-remove .q-field__control,.cc-mode-abbreviate .q-field__control{
  transition:box-shadow var(--nd-dur) var(--nd-ease);}
.cc-mode-remove .q-field__control{box-shadow:inset 0 0 0 2px var(--nd-accent);}
.cc-mode-remove textarea::selection{background:color-mix(in srgb, var(--nd-accent) 35%, transparent);}
.cc-mode-abbreviate .q-field__control{box-shadow:inset 0 0 0 2px var(--nd-interactive);}
.cc-mode-abbreviate textarea::selection{background:color-mix(in srgb, var(--nd-interactive) 30%, transparent);}
.cc-tint--danger{background:var(--cc-neg-soft);border-color:var(--nd-accent);}
.cc-panel{background:var(--nd-surface-raised);}

/* =====================================================================
   7. BUTTONS — pill or technical, mono ALL CAPS
   ===================================================================== */
.q-btn{border-radius:var(--nd-r-pill);font-family:var(--nd-mono);font-size:12.5px;
  font-weight:400;letter-spacing:.06em;text-transform:uppercase;min-height:40px;
  padding:0 var(--nd-space-lg);box-shadow:none !important;
  transition:background var(--nd-dur-fast) var(--nd-ease),
             border-color var(--nd-dur-fast) var(--nd-ease),
             color var(--nd-dur-fast) var(--nd-ease),
             transform var(--nd-dur-fast) var(--nd-ease);}
.q-btn.q-btn--actionable:active{transform:translateY(1px);}
.q-btn--dense{min-height:36px;padding:0 var(--nd-space-md);font-size:12px;}
.q-btn--round,.q-btn--fab{border-radius:var(--nd-r-pill);padding:0;}
.q-btn .q-icon{font-size:18px;}
.q-btn--flat{color:var(--nd-text-secondary);}
.q-btn--flat:hover{color:var(--nd-text-display);background:var(--nd-hover);}
.q-btn--outline:not(:disabled){border:1px solid var(--nd-border-visible);
  color:var(--nd-text-primary);}
.q-btn--outline:hover{border-color:var(--nd-text-display);}
.q-btn--outline.q-btn--flat{border:1px solid var(--nd-border-visible);}
/* Destructive: outlined in the accent, never a filled red slab. */
.q-btn.bg-negative,.q-btn.bg-red{background:transparent !important;
  color:var(--nd-accent) !important;border:1px solid var(--nd-accent);}
.q-btn.bg-negative:hover,.q-btn.bg-red:hover{background:var(--cc-neg-soft) !important;}
.q-btn:disabled,.q-btn--disabled{color:var(--nd-text-disabled) !important;opacity:1;}
.q-btn.bg-primary:disabled,.q-btn.bg-primary.q-btn--disabled{
  background:var(--nd-surface-raised) !important;color:var(--nd-text-disabled) !important;}
.q-btn-group{border:1px solid var(--nd-border-visible);border-radius:var(--nd-r-pill);
  overflow:hidden;}
.q-btn-group>.q-btn{border-radius:0;min-height:40px;}
.q-btn-group>.q-btn:not(:last-child){border-right:1px solid var(--nd-border-visible);}

/* =====================================================================
   8. INPUTS — technical 8px radius, mono data entry, underline labels
   ===================================================================== */
.q-field--outlined .q-field__control,.q-field--filled .q-field__control,
.q-textarea .q-field__control{border-radius:var(--nd-r-compact);}
.q-field--outlined .q-field__control:before{border:1px solid var(--nd-border-visible);
  transition:border-color var(--nd-dur-fast) var(--nd-ease);}
.q-field--outlined .q-field__control:hover:before{border-color:var(--nd-text-secondary);}
.q-field--outlined.q-field--focused .q-field__control:after{
  border:1px solid var(--nd-text-display);
  transition:border-color var(--nd-dur-fast) var(--nd-ease);}
.q-field__label,.q-field--float .q-field__label{
  transition-duration:var(--nd-dur);
  transition-timing-function:var(--nd-ease);}
.q-field--filled .q-field__control{background:var(--nd-surface-raised);}
.q-field__native,.q-field__input,.q-field__prefix,.q-field__suffix{
  color:var(--nd-text-primary);font-family:var(--nd-mono);font-size:13px;}
.q-field__margins,.q-field__control{color:var(--nd-text-secondary);}
.q-field__label{font-family:var(--nd-mono);font-size:var(--label);letter-spacing:.08em;
  text-transform:uppercase;color:var(--nd-text-secondary);}
.q-field--float .q-field__label{color:var(--nd-text-secondary);}
.q-field__bottom{font-family:var(--nd-mono);font-size:var(--label);
  color:var(--nd-text-secondary);}
.q-select__dialog,.q-menu .q-item{font-family:var(--nd-mono);font-size:12.5px;}
.q-uploader{border:1px dashed var(--nd-border-visible);border-radius:var(--nd-r-compact);
  background:transparent;box-shadow:none;color:var(--nd-text-secondary);
  font-family:var(--nd-mono);font-size:12px;}
.q-uploader__header{background:transparent;color:var(--nd-text-secondary);}
.q-uploader__list{background:transparent;}
/* The drop label is a control caption: mono, ALL CAPS, and never wrapped. */
.q-uploader__header-content{white-space:nowrap;}
.q-uploader:not(:has(.q-uploader__file)) .q-uploader__subtitle{display:none;}
.q-uploader__title{font-family:var(--nd-mono);font-size:var(--label);
  letter-spacing:.06em;text-transform:uppercase;color:var(--nd-text-primary);}

/* =====================================================================
   9. TABLES / DATA GRIDS — no zebra, no cell fills, mono numerals
   ===================================================================== */
.q-table{color:var(--nd-text-primary);background:transparent;}
.q-table__card{background:var(--nd-surface);border:1px solid var(--nd-border);
  border-radius:var(--nd-r-card);box-shadow:none;}
.q-table th{font-family:var(--nd-mono);font-size:var(--label);font-weight:400;
  letter-spacing:.08em;text-transform:uppercase;color:var(--nd-text-secondary);
  border-bottom:1px solid var(--nd-border-visible) !important;}
.q-table td{font-family:var(--nd-mono);font-size:12.5px;
  border-color:var(--nd-border) !important;background:transparent !important;
  transition:background var(--nd-dur-fast) var(--nd-ease);}
.q-table tbody tr:hover td{background:var(--nd-hover) !important;}
.q-table__bottom{font-family:var(--nd-mono);font-size:var(--label);
  color:var(--nd-text-secondary);border-top:1px solid var(--nd-border);}

/* =====================================================================
   10. TABS / SEGMENTED CONTROLS / EXPANSIONS
   ===================================================================== */
/* Tabs read as a Nothing segmented control: bordered track, inverted active. */
.q-tabs{background:transparent;border:1px solid var(--nd-border-visible);
  border-radius:var(--nd-r-pill);padding:3px;display:inline-flex;
  justify-content:flex-start;align-self:flex-start;width:auto !important;
  max-width:100%;overflow-x:auto;scrollbar-width:none;}
.q-tabs::-webkit-scrollbar{display:none;}
.q-tab{font-family:var(--nd-mono);font-size:12px;font-weight:400;letter-spacing:.06em;
  text-transform:none;color:var(--nd-text-secondary);opacity:1;min-height:38px;
  border-radius:var(--nd-r-pill);padding:0 var(--nd-space-md);white-space:nowrap;
  transition:background var(--nd-dur) var(--nd-ease),
             color var(--nd-dur) var(--nd-ease);}
.q-tab--active{color:var(--nd-black);background:var(--nd-text-display);}
.q-tab--active .q-icon{color:var(--nd-black);}
.q-tab .q-icon{font-size:17px;}
.q-tabs__active-indicator{display:none;}
.q-tab-panels{background:transparent;}
.q-tab-panel{padding:0;}

.q-expansion-item{border:1px solid var(--nd-border);border-radius:var(--nd-r-card);
  background:var(--nd-surface);}
.q-expansion-item .q-item__label{font-family:var(--nd-font);font-size:15px;
  font-weight:500;letter-spacing:-.005em;text-transform:none;color:var(--nd-text-primary);}
.q-expansion-item__toggle-icon{color:var(--nd-text-secondary);}
.q-expansion-item .q-item__section--avatar .q-icon{color:var(--nd-text-secondary);
  font-size:19px;}
.q-expansion-item__content{padding:0 var(--nd-space-md) var(--nd-space-md);}

/* =====================================================================
   11. TAGS — chips, badges, status pills
   ===================================================================== */
.q-badge{font-family:var(--nd-mono);font-size:var(--label);font-weight:400;
  letter-spacing:.06em;text-transform:uppercase;border-radius:var(--nd-r-tech);
  padding:3px 8px;box-shadow:none;}
.q-badge--outline{background:transparent !important;border:1px solid currentColor;}
.q-chip{background:transparent;border:1px solid var(--nd-border-visible);
  border-radius:var(--nd-r-pill);color:var(--nd-text-secondary);
  font-family:var(--nd-mono);font-size:var(--label);letter-spacing:.06em;
  text-transform:uppercase;
  transition:border-color var(--nd-dur-fast) var(--nd-ease),
             color var(--nd-dur-fast) var(--nd-ease),
             background var(--nd-dur-fast) var(--nd-ease);}
.q-chip:hover{border-color:var(--nd-text-display);color:var(--nd-text-primary);}
.q-chip .q-icon{font-size:15px;}

.cc-pill{display:inline-flex;align-items:center;gap:var(--nd-space-sm);height:24px;
  padding:0 10px;border-radius:var(--nd-r-tech);border:1px solid var(--nd-border-visible);
  background:transparent;font-family:var(--nd-mono);font-size:var(--label);
  font-weight:400;letter-spacing:0;text-transform:none;
  color:var(--nd-text-secondary);white-space:nowrap;}
.cc-pill--btn{min-height:36px;cursor:pointer;
  transition:border-color var(--nd-dur-fast) var(--nd-ease),
             color var(--nd-dur-fast) var(--nd-ease);}
.cc-pill--btn:hover{border-color:var(--nd-text-display);color:var(--nd-text-primary);}
.cc-dot{width:7px;height:7px;flex:0 0 7px;background:currentColor;display:inline-block;}

/* =====================================================================
   12. TOGGLES & CHECKBOXES — physical switch, instrument-grade
   ===================================================================== */
.q-toggle__inner--truthy{color:var(--nd-text-display) !important;}
.q-toggle__inner--falsy{color:var(--nd-text-disabled) !important;}
.q-toggle__track{opacity:1 !important;background:var(--nd-border-visible) !important;}
.q-toggle__thumb{border-color:var(--nd-border-visible) !important;}
.q-toggle__thumb:after{background:var(--nd-text-disabled) !important;}
.q-toggle__inner--truthy .q-toggle__track{background:var(--nd-text-display) !important;}
.q-toggle__inner--truthy .q-toggle__thumb{border-color:var(--nd-text-display) !important;}
.q-toggle__inner--truthy .q-toggle__thumb:after{background:var(--nd-black) !important;}
.q-toggle__label{font-family:var(--nd-mono);font-size:12px;color:var(--nd-text-secondary);}
.q-checkbox__inner--truthy,.q-radio__inner--truthy{color:var(--nd-text-display) !important;}
.q-checkbox__label,.q-radio__label{font-family:var(--nd-mono);font-size:12px;
  color:var(--nd-text-secondary);}

/* =====================================================================
   13. PROGRESS — discrete, mechanical
   ===================================================================== */
.q-linear-progress{border-radius:0;background:var(--nd-border);}
.q-linear-progress__model{border-radius:0;background:var(--nd-text-display);}
.cc-segbar-wrap{display:flex;flex-direction:column;gap:var(--nd-space-xs);width:100%;}
.cc-segbar-head{display:flex;align-items:baseline;justify-content:space-between;
  gap:var(--nd-space-md);}
.cc-segbar-label{font-family:var(--nd-mono);font-size:var(--label);letter-spacing:.08em;
  text-transform:uppercase;color:var(--nd-text-secondary);}
.cc-segbar-readout{font-family:var(--nd-mono);font-size:var(--caption);
  color:var(--nd-text-primary);font-variant-numeric:tabular-nums;}
.cc-segbar{display:flex;gap:2px;width:100%;height:10px;}
.cc-segbar > span{flex:1 1 0;min-width:2px;background:var(--nd-border);
  transition:background var(--nd-dur) var(--nd-ease);}
.cc-segbar > span.on{background:var(--nd-text-display);}
.cc-segbar--success > span.on{background:var(--nd-success);}
.cc-segbar--warning > span.on{background:var(--nd-warning);}
.cc-segbar--accent > span.on{background:var(--nd-accent);}
.cc-segbar--over > span.on{background:var(--nd-accent);}
.cc-segbar-note{font-family:var(--nd-mono);font-size:var(--label);
  color:var(--nd-text-primary);}

/* Dot-matrix motif — the empty-state illustration and decorative grid. */
.cc-dotgrid{width:108px;height:54px;
  background-image:radial-gradient(circle,var(--nd-border-visible) 1.5px,transparent 1.5px);
  background-size:12px 12px;}

/* =====================================================================
   13.5 MOTION — quiet, instrument-grade; ease-out only, never spring
   ---------------------------------------------------------------------
   State changes ease. Entrances settle a few pixels. Overlays rise instead
   of scaling out of a point. Nothing bounces, blurs, or overshoots.
   prefers-reduced-motion collapses every one of these to instant.
   Quasar removes `.q-body--layout-animate` after 155ms, which used to cut
   drawers off mid-slide; the same timing is therefore also set on the
   elements themselves so the slide finishes after that class drops.
   `html.cc-ready` (one frame after load) keeps the first paint from animating.
   ===================================================================== */
@keyframes cc-fade-up{from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:none;}}
@keyframes cc-fade{from{opacity:0;}to{opacity:1;}}
@keyframes cc-arrive{from{transform:translateY(6px);}to{transform:none;}}
@keyframes cc-focus{from{outline-offset:0;}to{outline-offset:3px;}}
@keyframes cc-spin{to{transform:rotate(360deg);}}
@keyframes cc-pulse{0%,100%{opacity:1;}50%{opacity:.35;}}
/* A freshly drawn result entering: single fade-up, retriggers each render. */
.cc-reveal{animation:cc-fade-up var(--nd-dur) var(--nd-ease) both;}
.cc-reveal--soft{animation:cc-fade var(--nd-dur) var(--nd-ease) both;}
/* Panels that toggle visibility (selection bar, spinner): slide, stay opaque
   so a click in the first frame still lands. */
.cc-arrive{animation:cc-arrive var(--nd-dur-fast) var(--nd-ease);}
/* Composed entrance: each direct child steps in behind the last, so the
   trust strip, the metric row and the tabs arrive as a sequence rather
   than a wall — no competing motion, just rhythm. */
.cc-stagger > *{animation:cc-fade-up var(--nd-dur) var(--nd-ease) both;}
.cc-stagger > *:nth-child(2){animation-delay:40ms;}
.cc-stagger > *:nth-child(3){animation-delay:80ms;}
.cc-stagger > *:nth-child(4){animation-delay:120ms;}
.cc-stagger > *:nth-child(5){animation-delay:160ms;}
.cc-stagger > *:nth-child(6){animation-delay:200ms;}
/* Square spinner — a Teenage-Engineering instrument scanning, not a blob. */
.cc-spinner{width:20px;height:20px;flex:0 0 20px;border:2px solid var(--nd-border-visible);
  border-top-color:var(--nd-text-display);border-radius:0;transform-origin:center;
  animation:cc-spin 700ms linear infinite;}
.cc-pulse{animation:cc-pulse 1.2s var(--nd-ease) infinite;}

/* Press, hover and focus share one clock. The Material ripple is a second
   animation on top of the press, so it goes. */
.q-ripple{display:none !important;}
body.desktop .q-focus-helper,
body.desktop .q-focus-helper:before,
body.desktop .q-focus-helper:after{
  transition-duration:var(--nd-dur-fast);
  transition-timing-function:var(--nd-ease);}
.q-item{transition:background var(--nd-dur-fast) var(--nd-ease),
  color var(--nd-dur-fast) var(--nd-ease);}
.q-expansion-item{transition:border-color var(--nd-dur-fast) var(--nd-ease);}
.q-expansion-item__toggle-icon{transition:transform var(--nd-dur) var(--nd-ease);}
.q-expansion-item:hover{border-color:var(--nd-border-visible);}
[dir="ltr"] .q-toggle__thumb,[dir="rtl"] .q-toggle__thumb{
  transition-duration:var(--nd-dur);
  transition-timing-function:var(--nd-ease);}
.q-checkbox__inner{transition:transform var(--nd-dur-fast) var(--nd-ease);}
.q-checkbox__inner:active{transform:scale(.94);}
.q-checkbox__inner--truthy path,.q-checkbox__inner--indet .q-checkbox__indet{
  transition-timing-function:var(--nd-ease);}
.q-card{transition:border-color var(--nd-dur-fast) var(--nd-ease);}
.q-uploader{transition:border-color var(--nd-dur-fast) var(--nd-ease),
  background var(--nd-dur-fast) var(--nd-ease);}
.q-uploader:hover{border-color:var(--nd-text-display);background:var(--nd-hover);}
.q-uploader__dnd{outline-color:var(--nd-text-display);
  background:var(--nd-hover);transition:background var(--nd-dur-fast) var(--nd-ease);}

/* Quasar overlays: one duration, and dialogs rise instead of scaling from 0. */
.q-transition--fade-enter-active,.q-transition--fade-leave-active,
.q-transition--scale-enter-active,.q-transition--scale-leave-active,
.q-transition--jump-up-enter-active,.q-transition--jump-up-leave-active,
.q-transition--jump-down-enter-active,.q-transition--jump-down-leave-active,
.q-transition--jump-left-enter-active,.q-transition--jump-left-leave-active,
.q-transition--jump-right-enter-active,.q-transition--jump-right-leave-active,
.q-transition--slide-up-enter-active,.q-transition--slide-up-leave-active,
.q-transition--slide-down-enter-active,.q-transition--slide-down-leave-active,
.q-transition--slide-left-enter-active,.q-transition--slide-left-leave-active,
.q-transition--slide-right-enter-active,.q-transition--slide-right-leave-active{
  --q-transition-duration:var(--nd-dur);
  --q-transition-easing:var(--nd-ease);}
.q-transition--fade-enter-active,.q-transition--fade-leave-active{
  transition:opacity var(--nd-dur) var(--nd-ease);}
.q-transition--scale-enter-from,.q-transition--scale-leave-to{
  opacity:0;transform:translate3d(0,8px,0);}
.q-tooltip{animation:cc-fade var(--nd-dur-fast) var(--nd-ease);}

/* Drawers and the page beside them. Specificity beats Quasar's 0.12s
   !important rule; the unscoped copy keeps running after the animate class
   is removed so a 220ms slide is not clipped at 155ms. */
html.cc-ready .q-drawer,
html.cc-ready body.q-body--layout-animate .q-drawer{
  transition:transform var(--nd-dur) var(--nd-ease),
             width var(--nd-dur) var(--nd-ease),
             top var(--nd-dur) var(--nd-ease),
             bottom var(--nd-dur) var(--nd-ease) !important;}
html.cc-ready .q-drawer__backdrop,
html.cc-ready body.q-body--layout-animate .q-drawer__backdrop{
  transition:background-color var(--nd-dur) var(--nd-ease),
             opacity var(--nd-dur) var(--nd-ease) !important;}
html.cc-ready .q-page-container,
[dir="ltr"] html.cc-ready body.q-body--layout-animate .q-page-container,
[dir="rtl"] html.cc-ready body.q-body--layout-animate .q-page-container{
  transition:padding-top var(--nd-dur) var(--nd-ease),
             padding-right var(--nd-dur) var(--nd-ease),
             padding-bottom var(--nd-dur) var(--nd-ease),
             padding-left var(--nd-dur) var(--nd-ease) !important;}
html.cc-ready .q-layout__section--marginal,
[dir="ltr"] html.cc-ready body.q-body--layout-animate .q-layout__section--marginal,
[dir="rtl"] html.cc-ready body.q-body--layout-animate .q-layout__section--marginal{
  transition:transform var(--nd-dur) var(--nd-ease),
             left var(--nd-dur) var(--nd-ease),
             right var(--nd-dur) var(--nd-ease) !important;}

/* =====================================================================
   13.6 EMPTY STATES — educational, on-brand previews
   ---------------------------------------------------------------------
   The first-run / no-result pane teaches what each result tab will show
   before the first clean: a hairline grid (structure is ornament), square
   status dots, and the signature segmented / struck / sparkline visuals.
   ===================================================================== */
.cc-preview-grid{background:var(--nd-border);border:1px solid var(--nd-border);}
.cc-preview-cell{background:var(--nd-surface);padding:var(--nd-space-lg);}
.cc-dot--ok{color:var(--nd-success);}
.cc-dot--warn{color:var(--nd-warning);}
.cc-dot--neg{color:var(--nd-accent);}
.cc-dot--info{color:var(--nd-text-display);}
/* Mini signature visuals — one per result tab. */
.cc-minibar{display:flex;gap:2px;width:100%;height:10px;}
.cc-minibar__cell{flex:1 1 0;min-width:2px;}
.cc-minibar__cell.on{background:var(--nd-text-display);}
.cc-minibar__cell.off{background:var(--nd-accent);}
.cc-spark{display:flex;align-items:flex-end;gap:3px;height:48px;width:100%;}
.cc-spark__bar{flex:1 1 0;min-width:3px;display:block;}

/* =====================================================================
   14. OVERLAYS — layering by contrast + border, never shadow
   ===================================================================== */
.q-dialog__backdrop{background:rgba(0,0,0,.8);}
.q-dialog .q-card,.q-dialog__inner>.q-card{border:1px solid var(--nd-border-visible);
  border-radius:var(--nd-r-card);box-shadow:none;background:var(--nd-surface);}
.q-tooltip{background:var(--nd-surface-raised);color:var(--nd-text-primary);
  border:1px solid var(--nd-border-visible);border-radius:var(--nd-r-tech);
  font-family:var(--nd-mono);font-size:var(--label);box-shadow:none;padding:6px 8px;}
.q-notification{background:var(--nd-surface-raised);color:var(--nd-text-primary);
  border:1px solid var(--nd-border-visible);border-left:2px solid var(--nd-text-display);
  border-radius:0;box-shadow:none;font-family:var(--nd-mono);font-size:12px;
  letter-spacing:.02em;}
.q-notification--positive{border-left-color:var(--nd-success);}
.q-notification--negative{border-left-color:var(--nd-accent);}
.q-notification--warning{border-left-color:var(--nd-warning);}
.q-notification__actions .q-btn{min-height:36px;}

/* =====================================================================
   15. METRIC WIDGETS — label on top, Doto numeral below
   ===================================================================== */
.cc-metric{display:flex;flex-direction:column;gap:var(--nd-space-sm);
  min-width:158px;padding:var(--nd-space-md) var(--nd-space-lg);
  background:var(--nd-surface);border:1px solid var(--nd-border);
  border-radius:var(--nd-r-card);}
.cc-metric-label{font-family:var(--nd-mono);font-size:var(--label);font-weight:400;
  letter-spacing:.08em;text-transform:uppercase;color:var(--nd-text-secondary);}
.cc-metric-value{font-family:var(--nd-display);font-size:40px;font-weight:600;
  letter-spacing:-.01em;line-height:1;color:var(--nd-text-display);
  font-variant-numeric:tabular-nums;}

/* =====================================================================
   16. CODE / MARKDOWN
   ===================================================================== */
.cc-mono textarea,.cc-mono input{font-family:var(--nd-mono) !important;}
pre,code,kbd{font-family:var(--nd-mono);}
.q-markdown pre{background:var(--nd-surface-raised);border:1px solid var(--nd-border);
  border-radius:var(--nd-r-compact);}
.q-markdown code{background:var(--nd-tint);color:var(--nd-text-primary);
  padding:1px 4px;border-radius:var(--nd-r-tech);}

/* =====================================================================
   17. CHART REVIEW — diff, highlights (status colour on the value only)
   ===================================================================== */
.cc-diff td{vertical-align:top;padding:1px var(--nd-space-sm);
  font-family:var(--nd-mono);font-size:12.5px;white-space:pre-wrap;
  word-break:break-word;}
.cc-del{background:var(--cc-neg-soft);}
.cc-ins{background:var(--cc-pos-soft);}
.cc-ghost-del{text-decoration:line-through;}
.cc-ghost-ins{color:var(--cc-pos);}
.cc-rep{background:var(--cc-warn-soft);}
.cc-flag{box-shadow:inset 2px 0 0 var(--nd-warning);}
.cc-diff-left{border-right:1px solid var(--nd-border);}
.cc-click{cursor:pointer;
  transition:box-shadow var(--nd-dur-fast) var(--nd-ease);}
.cc-click:hover{box-shadow:inset 0 0 0 1px var(--nd-warning);}
.cc-gone{text-decoration:line-through;text-decoration-color:var(--nd-accent);
  background:var(--cc-neg-soft);}
.cc-abbr{background:var(--nd-tint);border-bottom:1px dotted var(--nd-border-visible);}
.cc-unsupported{background:var(--cc-neg-soft);color:var(--nd-accent);
  border-radius:var(--nd-r-tech);padding:0 2px;}
.cc-spark td{padding:2px var(--nd-space-sm);}

/* =====================================================================
   18. QUASAR + TAILWIND COLOUR MAP
   ---------------------------------------------------------------------
   Why these live in `@layer theme`: NiceGUI ships Quasar's colour
   utilities inside `@layer quasar`, and for *important* declarations the
   cascade layers first-declared layer wins. `theme` is declared before
   `quasar`, so placing the overrides here is the only way to beat
   `.text-teal { color:#009688 !important }` — position in the document
   alone cannot do it.
   ===================================================================== */
@layer theme {
  .bg-primary{background:var(--nd-text-display) !important;color:var(--nd-black) !important;}
  .text-primary{color:var(--nd-text-display) !important;}
  .bg-secondary,.bg-grey,.bg-blue-grey,.bg-teal,.bg-indigo,.bg-purple,.bg-blue,
  .bg-grey-1,.bg-grey-2,.bg-grey-3,.bg-teal-1,.bg-green-1,.bg-blue-1,.bg-orange-1,
  .bg-amber-1,.bg-red-1{
    background:var(--nd-surface-raised) !important;color:var(--nd-text-primary) !important;}
  .bg-negative,.bg-red{background:var(--nd-accent) !important;color:#fff !important;}
  .bg-positive,.bg-green{background:var(--nd-success) !important;color:#fff !important;}
  .bg-warning,.bg-orange,.bg-amber{background:var(--nd-warning) !important;color:#fff !important;}
  /* Dark status fills are the light ink, so badge labels flip to black. */
  .body--dark .bg-negative,.body--dark .bg-red,
  .body--dark .bg-positive,.body--dark .bg-green,
  .body--dark .bg-warning,.body--dark .bg-orange,.body--dark .bg-amber{
    color:#000 !important;}
  .text-negative,.text-red,.text-red-500,.text-red-600,.text-red-700,.text-red-9,
  .text-red-8{color:var(--nd-accent) !important;}
  .text-positive,.text-green,.text-green-500,.text-green-600,.text-green-8,
  .text-green-9{color:var(--nd-success) !important;}
  .text-warning,.text-orange,.text-orange-600,.text-orange-700,.text-orange-8,
  .text-orange-9,.text-amber,.text-amber-600,.text-amber-700,.text-amber-8,
  .text-amber-9{color:var(--nd-warning) !important;}
  .text-info,.text-blue,.text-blue-500,.text-blue-600,.text-blue-700,.text-blue-8,
  .text-blue-9,.text-light-blue{color:var(--nd-interactive) !important;}
  .text-teal,.text-teal-8,.text-teal-9,.text-indigo,.text-blue-grey,.text-grey,
  .text-grey-7,.text-grey-8,.text-purple,.text-purple-8,.text-purple-9,
  .text-secondary{color:var(--nd-text-secondary) !important;}
  .text-white{color:var(--nd-black) !important;}
  .bg-white{background:var(--nd-surface) !important;}
  .bg-red-50,.bg-red-2{background:var(--cc-neg-soft) !important;}
  .bg-blue-50{background:var(--nd-tint) !important;}
  .bg-amber-50,.bg-orange-50,.bg-amber-2,.bg-orange-2{background:var(--cc-warn-soft) !important;}
  .bg-green-50,.bg-green-2{background:var(--cc-pos-soft) !important;}
  .border-red-300,.border-red-400{border-color:var(--nd-accent) !important;}
  .border-amber-400,.border-orange-300{border-color:var(--nd-warning) !important;}
  .border-green-400{border-color:var(--nd-success) !important;}
  /* Outlined destructive buttons stay transparent with accent-coloured text.
     This has to live in the layer: an unlayered rule loses to the fills above. */
  .q-btn.bg-negative,.q-btn.bg-red{background:transparent !important;
    color:var(--nd-accent) !important;border:1px solid var(--nd-accent);}
  .q-btn.bg-negative:hover,.q-btn.bg-red:hover{background:var(--cc-neg-soft) !important;}
  @media (max-width:599px){
    .cc-topbar,.cc-topbar .no-wrap{flex-wrap:wrap !important;row-gap:var(--nd-space-xs);}
    .cc-narrow-icon .block{display:none !important;}
    .q-field__native,.q-field__input{font-size:16px !important;}
  }
}

/* =====================================================================
   19. TYPE + SPACING UTILITY NORMALISATION — snaps every page to the scale
   ===================================================================== */
.text-xs{font-size:12px !important;line-height:1.45 !important;}
.text-sm{font-size:13px !important;line-height:1.5 !important;}
.text-base{font-size:14.5px !important;}
.text-lg{font-size:16px !important;line-height:1.35 !important;}
.text-xl{font-size:19px !important;}
.text-2xl{font-size:22px !important;}
.text-5xl{font-size:40px !important;}
.font-light{font-weight:300 !important;}
.font-medium{font-weight:500 !important;}
.font-semibold{font-weight:500 !important;}
.font-bold{font-weight:600 !important;}
.font-mono{font-family:var(--nd-mono) !important;}
.opacity-60,.opacity-70{opacity:1 !important;color:var(--nd-text-secondary) !important;}
.opacity-80{opacity:1 !important;color:var(--nd-text-primary) !important;}
.gap-3,.gap-4{gap:var(--nd-space-md) !important;}
.border{border-color:var(--nd-border) !important;}
.rounded{border-radius:var(--nd-r-tech) !important;}
.rounded-lg{border-radius:var(--nd-r-compact) !important;}
.shadow,.shadow-sm,.shadow-md,.shadow-lg,.shadow-xl,.shadow-2xl{box-shadow:none !important;}

/* =====================================================================
   20. RESPONSIVE — desktop text nav, drawer on narrow screens
   ===================================================================== */
@media (max-width:1023px){
  .cc-desktop-only{display:none !important;}
  .cc-mobile-only{display:inline-flex !important;}
  .cc-shell{padding:0 var(--nd-space-md);}
  .cc-page{padding:var(--nd-space-lg) var(--nd-space-md) var(--nd-space-2xl);
    gap:var(--nd-space-lg);}
  .nicegui-card:not([class*=" p-"]):not([class*="px-"]):not([class*="py-"]){padding:var(--nd-space-md);}
  .cc-metric{min-width:0;flex:1 1 44%;}
  .cc-metric-value{font-size:32px;}
  .q-btn--dense{min-height:44px;}
  .q-table td,.q-table th{padding:8px 10px;}
  /* Nothing may force the page wider than the phone. Fixed min-widths in the
     page code (chart columns, selects) collapse, long mono strings wrap
     mid-token, and badges — which Quasar pins to one line — are allowed to
     wrap. Small table-column minimums (<=200px) are left alone. */
  .min-w-\[260px\],.min-w-\[280px\],.min-w-\[300px\],.min-w-\[320px\],
  .min-w-\[380px\],.min-w-\[430px\]{min-width:0 !important;}
  .q-badge{white-space:normal !important;}
  /* A select whose selected text is longer than the phone would otherwise
     stretch both itself and its flex row past the card edge. Cap the row and
     the field to their container, and ellipsis-truncate the value text. */
  .cc-page .nicegui-row{max-width:100% !important;}
  .cc-page .q-field{min-width:0 !important;max-width:100% !important;}
  .cc-page .q-field__label{white-space:normal !important;}
  .cc-page .q-field__native{overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap;min-width:0;}
  .cc-page .font-mono,.cc-page .cc-mono,.cc-page .q-badge{overflow-wrap:anywhere;}
  /* Pipeline stage rows: controls drop below the label instead of running off. */
  .cc-stage-row{flex-wrap:wrap !important;row-gap:6px;}
  .cc-stage-row .q-space{display:none;}
  .cc-stage-row .cc-drag{display:none;}
}
@media (max-width:599px){
  .cc-brand{display:none;}
  .cc-page-title{font-size:24px;}
  /* The header action row is one line on desktop. On a phone it wraps, and
     Suggestions keeps its icon so palette, shortcuts and theme stay on screen. */
  .cc-topbar,.cc-topbar .no-wrap{flex-wrap:wrap !important; row-gap:var(--nd-space-xs);}
  .cc-narrow-icon .block{display:none !important;}
  .cc-narrow-icon{padding:0 !important; min-width:44px;}
  /* iOS Safari zooms the page when an input is under 16px. */
  .q-field__native,.q-field__input{font-size:16px;}
  /* Touch targets: every control earns at least 44px on a phone. */
  .q-btn{min-height:44px;}
  .cc-headbtn{min-height:44px;}
  /* A three-way toggle cannot fit a phone; stack it instead of clipping. */
  .q-btn-group{flex-direction:column;align-items:stretch;width:100%;
    border-radius:var(--nd-r-compact);}
  .q-btn-group>.q-btn{border-right:0 !important;
    border-bottom:1px solid var(--nd-border-visible);}
  .q-btn-group>.q-btn:last-child{border-bottom:0;}
  /* Dialogs are laid out at desktop widths in the page code — clamp them. */
  .q-dialog .q-card{width:auto !important;max-width:calc(100vw - 32px) !important;}
  .q-dialog__inner{padding:16px;}
}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms !important;
    animation-iteration-count:1 !important;
    transition-duration:.01ms !important;}
  html.cc-ready .q-drawer,
  html.cc-ready body.q-body--layout-animate .q-drawer,
  html.cc-ready .q-drawer__backdrop,
  html.cc-ready body.q-body--layout-animate .q-drawer__backdrop,
  html.cc-ready .q-page-container,
  [dir="ltr"] html.cc-ready body.q-body--layout-animate .q-page-container,
  [dir="rtl"] html.cc-ready body.q-body--layout-animate .q-page-container,
  html.cc-ready .q-layout__section--marginal,
  [dir="ltr"] html.cc-ready body.q-body--layout-animate .q-layout__section--marginal,
  [dir="rtl"] html.cc-ready body.q-body--layout-animate .q-layout__section--marginal{
    transition-duration:.01ms !important;}
}
</style>
<script>
requestAnimationFrame(function(){document.documentElement.classList.add("cc-ready");});
/* Quasar's uploader names its add button and file input from the icon ligature.
   Label the chart drop once Vue mounts it, and again if it re-renders. */
(function(){
  var NAME = "Upload a chart file";
  function labelDrop(drop){
    drop.querySelectorAll(".q-btn").forEach(function(btn){
      var current = btn.getAttribute("aria-label");
      if(!current || current === "add_box") btn.setAttribute("aria-label", NAME);
      var icon = btn.querySelector(".q-icon");
      if(icon) icon.setAttribute("aria-hidden", "true");
    });
    drop.querySelectorAll("input[type=file]").forEach(function(input){
      if(!input.getAttribute("aria-label")) input.setAttribute("aria-label", NAME);
    });
  }
  function scan(node){
    if(!node) return;
    if(node.nodeType === 9 || node.nodeType === 11){
      node.querySelectorAll(".cc-chart-drop").forEach(labelDrop);
      return;
    }
    if(node.nodeType !== 1) return;
    var drop = (node.matches && node.matches(".cc-chart-drop")) ? node
      : (node.closest ? node.closest(".cc-chart-drop") : null);
    if(drop) labelDrop(drop);
    if(node.querySelectorAll) node.querySelectorAll(".cc-chart-drop").forEach(labelDrop);
  }
  scan(document);
  new MutationObserver(function(records){
    records.forEach(function(record){ record.addedNodes.forEach(scan); });
  }).observe(document.documentElement, {childList:true, subtree:true});
})();
</script>
"""


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------

# Quasar colour names pages pass to stat_chip → Nothing status tokens.
_METRIC_TONES = {
    "primary": "var(--nd-text-display)",
    "green": "var(--nd-success)",
    "red": "var(--nd-accent)",
    "orange": "var(--nd-warning)",
    "grey": "var(--nd-text-secondary)",
    "indigo": "var(--nd-text-display)",
    "teal": "var(--nd-text-display)",
    "blue": "var(--nd-interactive)",
    "blue-grey": "var(--nd-text-secondary)",
    "purple": "var(--nd-text-secondary)",
}


def stat_chip(container, label: str, value: str, color: str = "primary") -> None:
    """A dashboard widget: ALL-CAPS mono label above a Doto numeral.

    Colour is applied to the *value* (it encodes status), never to the label
    or the tile background.
    """
    with container:
        with ui.element("div").classes("cc-metric"):
            ui.label(label).classes("cc-metric-label")
            ui.label(value).classes("cc-metric-value") \
                .style(f"color: {_METRIC_TONES.get(color, 'var(--nd-text-display)')}")


_STATUS_COLORS = {
    "ok": "var(--nd-success)", "on": "var(--nd-success)",
    "warn": "var(--nd-warning)", "danger": "var(--nd-accent)",
    "info": "var(--nd-text-display)", "off": "var(--nd-text-disabled)",
}


def status_tag(text: str, status: str = "info", *, tooltip: str | None = None,
               on_click=None, classes: str = ""):
    """A mechanical state readout: status square + ALL-CAPS mono label.

    Colour marks the state; the tag chrome stays monochrome. When ``on_click``
    is given the tag becomes a real keyboard control (Tab + Enter/Space), not
    just a click target — screen-reader users get the tooltip as its name.
    """
    el = ui.element("div").classes(
        f"cc-pill{' cc-pill--btn' if on_click else ''} {classes}".strip())
    if tooltip:
        el.tooltip(tooltip)
    if on_click is not None:
        el.on("click", on_click)
        el.props(f"role=button tabindex=0 aria-label=\"{tooltip or text}\"")
        el.on("keydown.enter", lambda _e: el.run_method("click"))
        el.on("keydown.space", lambda _e: el.run_method("click"))
    with el:
        ui.element("span").classes("cc-dot").props("aria-hidden=true") \
            .style(f"color: {_STATUS_COLORS.get(status, _STATUS_COLORS['info'])}")
        ui.label(text)
    return el


class SegmentedBar:
    """The signature Nothing data visual: discrete blocks, not a smooth fill.

    ``SegmentedBar(container, segments=24, tone="success", label="Recall")``
    then ``set_value(0.83, "83%")``. A fraction above 1.0 fills every block in
    the accent colour — the overflow read.
    """

    def __init__(self, container, *, segments: int = 24, tone: str = "neutral",
                 label: str = "", value_text: str = "", classes: str = "") -> None:
        self.segments = max(4, int(segments))
        self._cells: list = []
        with container:
            self.root = ui.element("div").classes("cc-segbar-wrap")
            if classes:
                self.root.classes(classes)
            with self.root:
                self._readout = None
                if label or value_text:
                    with ui.element("div").classes("cc-segbar-head"):
                        ui.label(label).classes("cc-segbar-label")
                        self._readout = ui.label(value_text).classes("cc-segbar-readout")
                self._bar = ui.element("div").classes(f"cc-segbar cc-segbar--{tone}")
                with self._bar:
                    for _ in range(self.segments):
                        self._cells.append(ui.element("span"))
        self.set_value(0.0)

    def set_value(self, fraction, value_text: str | None = None) -> None:
        try:
            frac = max(0.0, float(fraction or 0.0))
        except (TypeError, ValueError):
            frac = 0.0
        over = frac > 1.0
        filled = self.segments if over else int(round(frac * self.segments))
        for i, cell in enumerate(self._cells):
            if i < filled:
                cell.classes(add="on")
            else:
                cell.classes(remove="on")
        if over:
            self._bar.classes(add="cc-segbar--over")
        else:
            self._bar.classes(remove="cc-segbar--over")
        if value_text is not None and self._readout is not None:
            self._readout.set_text(value_text)

    def set_visibility(self, visible: bool) -> None:
        self.root.set_visibility(visible)


# ---------------------------------------------------------------------------
# charts — ECharts renders to canvas, so it cannot read CSS variables. These
# helpers hand it concrete token values for the *current* mode and re-theme
# every live chart when the light/dark toggle flips.
# ---------------------------------------------------------------------------

_THEME = {"dark": True}
_CHARTS: list[tuple] = []

_DARK_TOKENS = {
    "black": "#000000", "surface": "#111111", "surface-raised": "#1A1A1A",
    "border": "#222222", "border-visible": "#333333",
    "display": "#FFFFFF", "primary": "#E8E8E8", "secondary": "#999999",
    "disabled": "#666666", "interactive": "#5B9BF6",
    "display-60": "rgba(255,255,255,.6)", "display-30": "rgba(255,255,255,.3)",
    "ink": "255,255,255",
    "success": "#7ACE89", "warning": "#E3B753", "accent": "#FBADA4",
    "success-soft": "rgba(122,206,137,.14)",
}
_LIGHT_TOKENS = {
    "black": "#F5F5F5", "surface": "#FFFFFF", "surface-raised": "#F0F0F0",
    "border": "#E8E8E8", "border-visible": "#CCCCCC",
    "display": "#000000", "primary": "#1A1A1A", "secondary": "#666666",
    "disabled": "#999999", "interactive": "#007AFF",
    "display-60": "rgba(0,0,0,.6)", "display-30": "rgba(0,0,0,.3)",
    "ink": "0,0,0",
    "success": "#1D7637", "warning": "#7E5D00", "accent": "#D71921",
    "success-soft": "rgba(29,118,55,.14)",
}
# Fallback only. Live charts read the per-theme values above.
_STATUS_TOKENS = {
    "success": "#1D7637", "warning": "#7E5D00", "accent": "#D71921",
    "success-soft": "rgba(29,118,55,.14)",
}


def set_chart_theme(dark: bool) -> None:
    _THEME["dark"] = bool(dark)


def tone(name: str) -> str:
    """A token as a concrete colour for the mode in force right now."""
    table = _DARK_TOKENS if _THEME["dark"] else _LIGHT_TOKENS
    return table.get(name) or _STATUS_TOKENS[name]


def opacity_ramp(count: int) -> list[str]:
    """``count`` shades of the display ink — opacity before colour."""
    steps = (1.0, .82, .64, .48, .34, .24, .16, .10)
    ink = (_DARK_TOKENS if _THEME["dark"] else _LIGHT_TOKENS)["ink"]
    return [f"rgba({ink},{steps[min(i, len(steps) - 1)]})" for i in range(max(0, count))]


def _chart_axis(ax: dict) -> None:
    if not isinstance(ax, dict):
        return
    ax.setdefault("axisLine", {}).setdefault("lineStyle", {})["color"] = tone("border-visible")
    ax.setdefault("axisTick", {})["show"] = False
    ax.setdefault("axisLabel", {})["color"] = tone("secondary")
    ax.setdefault("nameTextStyle", {})["color"] = tone("secondary")
    if ax.get("type") in ("value", "log"):
        ax.setdefault("splitLine", {}).setdefault("lineStyle", {})["color"] = tone("border")
    else:
        ax.setdefault("splitLine", {})["show"] = False


def _chart_series(opt: dict) -> list[dict]:
    series = opt.get("series")
    if isinstance(series, dict):
        return [series]
    return series if isinstance(series, list) else []


def _chart_options(options: dict) -> dict:
    """Deep-copy ``options`` and lay the Nothing chart chrome over it."""
    import copy

    opt = copy.deepcopy(options)
    opt["backgroundColor"] = "transparent"
    # Bars still step (no smoothed lines). They grow in once, on the same clock
    # as the rest of the interface.
    opt["animation"] = bool(opt.get("animation", True))
    opt["animationDuration"] = int(opt.get("animationDuration", 320))
    opt["animationDurationUpdate"] = int(opt.get("animationDurationUpdate", 280))
    opt["animationEasing"] = opt.get("animationEasing", "cubicOut")
    opt["textStyle"] = {**opt.get("textStyle", {}),
                        "fontFamily": "Space Mono, monospace", "color": tone("secondary")}
    opt.setdefault("grid", {}).setdefault("containLabel", True)

    for key in ("xAxis", "yAxis"):
        axes = opt.get(key)
        if axes is None:
            continue
        for ax in (axes if isinstance(axes, list) else [axes]):
            _chart_axis(ax)

    legend = opt.get("legend")
    if isinstance(legend, dict):
        legend.setdefault("textStyle", {})["color"] = tone("secondary")
        legend.setdefault("icon", "rect")
        legend.setdefault("itemWidth", 10)
        legend.setdefault("itemHeight", 10)

    tooltip = opt.get("tooltip")
    if isinstance(tooltip, dict):
        tooltip.update({
            "backgroundColor": tone("surface-raised"),
            "borderColor": tone("border-visible"),
            "borderWidth": 1,
            "textStyle": {"color": tone("primary"), "fontSize": 12,
                          "fontFamily": "Space Mono, monospace"},
            "extraCssText": "box-shadow:none;border-radius:4px;",
        })

    for s in _chart_series(opt):
        s.pop("smooth", None)   # percussive, not fluid
        s.pop("areaStyle", None)
        if s.get("type") == "pie":
            s.setdefault("itemStyle", {}).update({"borderColor": tone("black"),
                                                  "borderWidth": 2})
    return opt


def chart(options: dict, *, classes: str = "w-full h-72"):
    """``ui.echart`` with the design-system chrome, re-themed on mode change."""
    el = ui.echart(_chart_options(options))
    el.classes(classes)
    _CHARTS.append((el, options))
    return el


def clear_charts() -> None:
    """Drop the previous page's chart references (called as each page builds)."""
    _CHARTS.clear()


def retheme_charts() -> None:
    """Re-apply the palette to every live chart after a light/dark flip."""
    for index in range(len(_CHARTS) - 1, -1, -1):
        el, options = _CHARTS[index]
        try:
            el.options = _chart_options(options)
        except Exception:
            _CHARTS.pop(index)  # the page is gone
