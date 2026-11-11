# Catalog illustration

One illustration for the Hermes plugin catalog card and the plugin detail page. It is a dark app
surface rendered by a real browser — CSS only, no vector drawing, no icon font, no image model, no
SVG, no `<img>`, no animation, no grain, no fake window chrome.

- `plugin-card.html` — the source. 1600 × 900 CSS px, one page, one illustration.
- `plugin-card@2x.png` — the committed render. **3200 × 1800**, device scale 2.

Subject: a message composer whose right-aligned controls show that a separate scorer picks the
reasoning effort (`auto` mode, `high` effort) without changing the model.

## Render

Windows, Microsoft Edge (`msedge` is not on `PATH` on this machine):

```powershell
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$ud   = Join-Path $env:TEMP ("hae-render-" + [guid]::NewGuid().ToString("N").Substring(0,8))
& $edge --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 `
        --window-size=1600,900 --user-data-dir="$ud" `
        --screenshot="$PWD\plugin-card@2x.png" `
        "file:///C:/Users/elekt/Projets/jev-auto-effort/assets/illustrations/plugin-card.html"
```

The exact command line that produced the committed PNG:

```
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size=1600,900 --user-data-dir=C:\Users\elekt\AppData\Local\Temp\hae-render-cf50de93 --screenshot=C:\Users\elekt\Projets\jev-auto-effort\assets\illustrations\plugin-card@2x.png file:///C:/Users/elekt/Projets/jev-auto-effort/assets/illustrations/plugin-card.html
```

**Trap, verified twice:** `--user-data-dir` must be a fresh directory each run. Reusing one, or
omitting it, makes Edge exit `0` and write nothing. The launcher also returns before the file exists
when invoked with `&`; use `Start-Process -Wait`, then poll for the file. Expected output is exactly
3200 × 1800 (CSS 1600 × 900 at 2×).

## Palette tokens

| Token | Value | Used for |
|---|---|---|
| `--bg` | `#0F1113` | page background; all four corners are exactly this |
| `--panel` | `#141619` | composer panel fill, brand tile fill |
| `--line` | `#23272C` | 2px panel border, 1px control borders |
| `--lit` | `#2E3339` | lit top edge (panel `inset 0 1px 0`, tile `border-top-color`), gauge track |
| `--ink` | `#E8EAED` | title, control values, needle |
| `--ink-2` | `#C9CDD2` | subtitle |
| `--ink-3` | `#7C8288` | caption, chevron |
| `--accent` | `#5C4BDD` | accent dot, filled level meter, primary control |

Data colours are computed from the accent token as alpha steps (`.16`, `.64`, `.80`, and `.10`/`.18`/
`.26` in the aura), never chosen separately. No accent token is defined in the repository
(`desktop/plugin.js` uses the host's `--ui-accent` CSS variable, which has no literal value here), so
`#5C4BDD` is used, as the brief specifies.

The brand tile reuses the plugin's existing mark (`assets/icon.png`, a gauge with a needle): the same
dial idea rebuilt from CSS — a conic-gradient sweep cut to a ring by a radial `mask`, a 2px needle
and a hub, in a 64 × 64 tile with radius 18 and a 1px border.

## Measured geometry

From pixel probes of the committed PNG (device px, halved for CSS):

| Property | Expected | Measured |
|---|---|---|
| Size | 3200 × 1800 | 3200 × 1800 |
| Corners | `#0F1113` | `(15,17,19)` all four |
| Panel fill | `#141619` | `(20,22,25)` at 4 empty points |
| Panel border | 2px `#23272C` | rows 1200–1203 and 1460–1463 (top/bottom), columns 192–195 and 3004–3007 |
| Lit top edge | `#2E3339` | rows 1204–1205 |
| Panel box | 1408 × 132, centre y 666 | x 96.0–1504.0, y 600.0–732.0, centre 666.00 |
| Primary control | 132 × 52 | x 1348.0–1480.0, y 640.0–692.0 |
| Primary control centre | `#5C4BDD` | `(92,75,221)` |
| Accent dot centre | `#5C4BDD` | `(92,75,221)` |
| Top margin (ink) | ~95 | 95.0 |
| Bottom margin (ink) | ~95 | 94.5 (delta 0.5) |
| Caption baseline | ~800 | 801.0 (mass of ink ends at row 1601; descender tail to 1610) |

The first non-background row is 190 (CSS 95.0) — the top edge of the brand tile. The last row with ink
is 1610 (CSS 805.0) — the descender of the caption.

Two defects were found by these probes and fixed in the CSS, not in the probe: the first aura radius
reached the bottom-left corner (`#111319` instead of `#0F1113`), and the caption's default `<p>`
margin shifted an absolutely positioned box down by 19px (bottom margin 75.5).

## Alt text

**Catalog card (1:1 crop):**

> Dark app surface showing a message composer. On its right: a mode control reading “auto”, an effort
> control reading “high” with a three-of-four level meter, and a filled violet button.

**Plugin detail page (full 16:9):**

> Dark plugin illustration. Top left, a gauge icon and the title “Hermes Adaptive Effort” over the line
> “one scorer chooses the effort, not the model”. The middle is empty — the conversation. At the
> bottom, a wide composer with right-aligned controls: mode “auto”, effort “high”, and a filled violet
> button, over a violet glow. A caption reads “four modes · auto, once, always, off”.

## Crops

All rectangles are CSS px of the 1600 × 900 page; double them for the PNG.

**1:1 catalog card — use the composer controls.** `x 950–1600, y 250–900` (650 × 650 → device
`1900,500 – 3200,1800`, 1300 × 1300). Contains the whole control cluster (measured x 1074.5–1480,
y 640–692) with the aura below. It excludes the wordmark (ends x 758.5) and the caption (ends x 948),
so no text is clipped. Verified at 320 × 320 and 200 × 200: “auto” and “high” stay readable and the
violet control still reads as the primary action. The card must show the plugin name as its own label,
because this crop has no wordmark.

**Detail page — use the full 16:9 render, uncropped.**

Avoid narrower crops: the wordmark (x 95–758.5) and the controls (x 1074.5–1480) are 1000 CSS px apart
on a diagonal, so no 4:3 or 1:1 rectangle holds both, and any crop between them clips the caption
(x 653–948) mid-word.

## Verifying a change

Re-render, then probe the PNG instead of looking at it: four corners equal `#0F1113` exactly, panel
fill `#141619`, border token `#23272C` at the rows and columns above, panel edges 96/1504 and 600/732,
accent `(92,75,221)` at the dot and control centres, and the two margins equal within 2 CSS px. The
probe used for this render is a throwaway script (`%TEMP%\opencode\hae_probe.py`) and is not committed.
