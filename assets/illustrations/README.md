# Catalog illustration

One illustration for the Hermes plugin catalog card and the plugin detail page: the plugin's
**status-bar chip with its popover open**, built out of the **real Hermes Desktop interface
pieces** — the app's own compiled CSS, its real class names, the real status bar and the real
Popover surface — and magnified so it still reads at 200 × 200.

- `plugin-card.html` — the source. 1600 × 900 CSS px, one page, one illustration.
- `plugin-card@2x.png` — the committed render. **3200 × 1800**, device scale 2.
- `vendor/` — the app CSS, the Codicon font and the dark palette this page depends on (below).

Nothing here is drawn by hand: the chip, the panel, its border, its radius, its type metrics and
its colours are the app's. The only authored CSS is the **frame** (canvas, placement, the 4.2×
magnification of the subject, two aura gradients, the coloured shadow, the brand lockup, the
caption). Every frame class is prefixed `ill-` so it cannot collide with the app's utilities —
an earlier draft used `.wordmark` and silently inherited the app's own `.wordmark` rule
(`font-family: Collapse`, `text-transform: uppercase`), which is why the prefix exists.

## Provenance

| Piece | Source | Vendored as |
|---|---|---|
| App CSS bundle | `hermes-agent` @ **`ecf9655b5a`** (2026-10-09), `apps/desktop/dist/assets/index-C9U-kDt2.css` (385 172 B, verbatim, sha256 `e7a9ef06aaab4655…`) | `vendor/hermes-desktop-index-C9U-kDt2.css` |
| Codicon font | same build, `dist/assets/codicon-DjkITdqj.ttf` — the real `zap` glyph in the chip | `vendor/codicon-DjkITdqj.ttf` |
| Dark palette | `nous.darkColors` as compiled into `dist/assets/i18n-ClA-B5fN.js`, applied the way `src/themes/context.tsx:applyTheme()` writes it (`:root.dark`, `class="dark"`) | `vendor/hermes-theme-nous-dark.css` |
| Status bar markup | `apps/desktop/src/app/shell/statusbar-controls.tsx:99-120` (`<footer data-slot="statusbar" class="flex h-5 … bg-(--ui-sidebar-surface-background) px-1 …">`) | copied into the page |
| Popover markup | `apps/desktop/src/components/ui/popover.tsx` (`border border-(--ui-stroke-secondary) bg-(--popover-surface) … rounded-lg p-2`, `sideOffset 6`) plus `desktop/plugin.js:1026-1230`'s `max-h-80 w-64 space-y-2 overflow-y-auto p-3` | copied into the page |
| Chip markup | `desktop/plugin.js:1120` (`inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem]`), `Codicon name="zap"` | copied into the page |

`cn()` in the app is `clsx` + `tailwind-merge`, so the plugin's `w-64` / `p-3` replace the
primitive's `w-72` / `p-2` (measured: the panel is 256 CSS px wide at product scale). The page
emits the merged class list, which is what the DOM actually carries.

### Tokens this app build genuinely lacks

The bundle is compiled from the app's own sources, so utilities only a third-party plugin uses are
absent. Recorded rather than papered over (`vendor/plugin-fallbacks.css`):

| Class in the plugin | State in `ecf9655b5a` |
|---|---|
| `space-y-1.5` | **not emitted**; restored with the bundle's own `space-y-*` formula, value 1.5 |
| `bg-(--ui-fill-secondary)` | **`--ui-fill-secondary` is not defined anywhere** in this build (neither `:root` nor `:root.dark` in `src/styles.css`), so the inactive mode buttons render unfilled — nothing was invented to fill them |
| `text-(--ui-warning)` | not emitted; unused in this composition |

That is a real finding about the plugin against this app version, not an illustration choice.

## Scale

Real classes give real sizes, which are tiny, so the **whole chip + popover block is magnified**
with `transform: scale(4.2)` (`transform-origin: 100% 100%`), never by editing component styles.
The status bar at the bottom stays at **product size** so the pair "in the bar" / "opened" is
legible.

Effective sizes (real CSS px × 4.2):

| Element | Real | Rendered |
|---|---|---|
| `This conversation`, `default` (`text-xs`) | 12 px | **50.4 px** (largest) |
| mode buttons, `med → high`, route line (`text-[0.6875rem]`) | 11 px | 46.2 px |
| `zap` glyph (`text-[0.75rem]`) | 12 px | 50.4 px |
| chip label in the real status bar (unscaled, the brief's exception) | 11 px | **11 px** (smallest) |
| brand wordmark (frame) | — | 24 px |
| caption (frame) | — | 22 px |

Consequence, stated rather than hidden: at a single uniform scale a 12 px label and a 12 px value
cannot land in different bands, so every label in the panel is 50.4 px. The panel is 1068.5 ×
503.5 CSS px, **66.8 % of the frame width and 55.9 % of its height**; the pair spans
256 → 899.5 = 645 CSS px = **72 % of the height**.

## Render

Windows, Microsoft Edge (`msedge` is not on `PATH` on this machine):

```powershell
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$ud   = Join-Path $env:TEMP ("hae-render-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Path $ud | Out-Null
$eargs = @('--headless=new','--disable-gpu','--hide-scrollbars','--force-device-scale-factor=2',
           '--window-size=1600,900',"--user-data-dir=$ud",
           "--screenshot=$PWD\plugin-card@2x.png",
           'file:///C:/Users/elekt/Projets/jev-auto-effort/assets/illustrations/plugin-card.html')
$p = Start-Process -FilePath $edge -ArgumentList $eargs -PassThru -Wait
for ($i = 0; $i -lt 80 -and -not (Test-Path "$PWD\plugin-card@2x.png"); $i++) { Start-Sleep -Milliseconds 250 }
```

The exact command line that produced the committed PNG:

```
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size=1600,900 --user-data-dir=C:\Users\elekt\AppData\Local\Temp\hae-render-b42395dc --screenshot=C:\Users\elekt\Projets\jev-auto-effort\assets\illustrations\plugin-card@2x.png file:///C:/Users/elekt/Projets/jev-auto-effort/assets/illustrations/plugin-card.html
```

Exit `0`, 264 223 B, 3200 × 1800. **Trap, verified again:** `--user-data-dir` must be a fresh
directory each run — reusing one, or omitting it, makes Edge exit `0` and write nothing — and the
launcher returns before the file exists when invoked with `&`; use `Start-Process -Wait`, then poll.

## Measured geometry

From a pixel probe of the committed PNG (CSS px; the PNG is 2× and the probe halves it). Every
region is found by its own fill colour and by contiguity, never by "not the background", so the
aura and the coloured shadow cannot be mistaken for the panel.

| Property | Token / expected | Measured |
|---|---|---|
| Size | 3200 × 1800 | 3200 × 1800 |
| Four corners | `--ui-bg-chrome` | `(13,16,21)` all four, exactly the token |
| Page background | `--ui-bg-chrome` | `(13,16,21)` |
| Popover fill | `--popover-surface` = `--ui-bg-elevated` at 92 % | `(21,23,28)` |
| Popover border | `--ui-stroke-secondary` | `(40,53,77)` left, right, top, bottom |
| Popover border box | — | x 480.0–1548.0, y 256.0–759.0 (1068.5 × 503.5) |
| Popover radius | `rounded-lg` = `--radius-scalar` .2 × .75rem = **2.4 px** (10.1 at 4.2) | fill's top row starts 4.0 css px in — a small radius, not 12 |
| Panel shadow | `0 28px 64px -24px` accent 35 % | emitted as `0 6.6667px 15.2381px -5.7143px` = exactly those values at 4.2× |
| Bar slice fill | `--ui-sidebar-surface-background` | `(1,4,9)` |
| Bar slice box | 20 css × 4.2 | x 477.0–1551.5, y 788.0–871.5 (84.0 tall) |
| Slice hairline | frame `border-top` 1px | `(25,38,62)` at row 1576 device |
| Gap panel → chip slice | `sideOffset 6` × 4.2 = 25.2 | 28.5 (measured border row to border row) |
| Accent token | `--ui-accent` = `--theme-midground` `#4a84fe` | `(74,132,254)` |
| `default` value | accent | `(74,132,254)` at css (1404.5, 324.0); text box x 1338.5–1495.5, y 324.0–361.5 |
| Accent mode button | `bg-(--ui-accent)`, `rounded-md` | box x 1109.0–1450.5, y 399.5–501.5 (342.0 × 102.5); centre hits the white label, fill beside it `(74,132,254)` |
| Hero chip accent run | `text-(--ui-accent)` | x 1197.5–1506.5, y 802.5–861.5; 1 794 px of it in the first 25 css px (the `zap` glyph) |
| Hero chip box | slice inner box − `px-1` | x 1172.5–1547.5, y 788.0–871.5 |
| Real status bar | `h-5` = 20 px | x 40.0–1555.5, y 880.0–899.5, flush with the bottom |
| Real bar chip | 11 px text | accent run x 1471.5–1544.5, y 880.0–896.5 |
| Header type | 12 px × 4.2 = 50.4 | ink band 37.5 css px tall (ascender + x-height of 50.4 px type) |
| Top margin | — | 48.0 (brand tile top edge); bottom 0.0 (the bar is flush) |

The coloured shadow lands on the chip's bar slice, which is why the slice's top rows are not the
exact surface colour — intended (it is what `0 28px 64px` does) and measured as such.

## Crops

All rectangles are CSS px of the 1600 × 900 page; double them for the PNG.

**1:1 catalog card — the chip plus the open popover.** The pair's bounding box is
`x 477–1551.5, y 256–871.5` = **1074.5 × 615.5** (aspect 1.75). No 1:1 rectangle inside 1600 × 900
can hold that box without cutting text: a 900 × 900 square anchored at the pair's bottom-right
(`x 651–1551, y 0–900`, device `1302,0 – 3102,1800`) clips the panel's left 174 css px, which cuts
the first letters of `This conversation` and of `med → high`. Two honest options:

- **Recommended (no clipping):** `x 477–1551, y 0–900` — 1074 × 900, device `954,0 – 3102,1800`,
  aspect 1.19. Holds the whole chip + popover, both chips and the bar; the caption and the wordmark
  are outside it, so the card must carry the plugin name as its own label.
- **Strict 1:1 (clips):** `x 651–1551, y 0–900`, device `1302,0 – 3102,1800`, 1800 × 1800. Cuts
  the panel's left edge mid-word; only use it if the card's ratio is fixed at 1:1.

**Detail page — the full 16:9 render, uncropped** (3200 × 1800).

## Alt text

**Catalog card (the crop above):**

> A magnified status-bar chip reading “med → high” with a lightning glyph, and above it the
> plugin's dark popover: “This conversation” with the value “default” in blue, a row of mode
> buttons ending in a filled blue “Use default”, then “med → high” and the route line
> “opencode-go · deepseek-v4.1-flash”. A thin real status bar runs along the bottom.

**Plugin detail page (full 16:9):**

> Dark app surface. Top left, the plugin icon and the title “Hermes Adaptive Effort”. Lower right,
> at 4.2× product size, the status-bar chip “med → high” with its lightning glyph and the popover
> it opens: “This conversation · default”, the mode row “auto / once / always / ● Use default”,
> and “med → high” over the route “opencode-go · deepseek-v4.1-flash”. The real 20 px status bar
> runs along the bottom with the same chip at product size, and a caption reads “One chip in the
> status bar; one panel to set the mode.”

## Verifying a change

Re-render, then probe the PNG instead of looking at it: four corners exactly `(13,16,21)`; popover
fill `(21,23,28)` inside a `(40,53,77)` border on all four sides with the box at
480/1548 × 256/759; the chip's bar slice `(1,4,9)` 84 css px tall at y 788–871.5; accent
`(74,132,254)` at the `default` value and at the button fill; the real bar 20 css px tall, flush,
x 40–1555.5; top margin 48.0. The probe is a throwaway script (`%TEMP%\opencode\hae_probe_t10.py`)
and is not committed; it needs `$env:PYTHONIOENCODING = "utf-8"` because the chip label contains an
arrow and the Windows console codepage is cp1252. It also writes a 200 × 200 box-downscaled
thumbnail for the legibility check.

## Gaps

- **200 × 200 legibility is a judgement, not a measurement.** At that size the panel's title, the
  `default` value, the accent button and `med → high` are recognisable; the 11 px status-bar chip
  and the 22 px caption are not, by construction (the brief asks for the bar at product size).
- **Dark only.** The illustration is the app's `nous` dark palette; a light-mode variant was not
  rendered or probed.
- **The 1:1 card crop clips text** (see Crops); the recommended card crop is 1074 × 900.
- **Vendor weight:** the committed CSS is the app's full 385 172 B bundle, vendored verbatim for
  fidelity rather than tree-shaken; it is not referenced by the plugin runtime.
- **The mode buttons are unfilled** because `--ui-fill-secondary` does not exist in this app build;
  that is the app's real rendering, and the measured `/default` and accent-button probes stand.
- The probe measures `gap panel → chip slice` as 28.5 css px against the declared 25.2; the
  difference is one device row of border/antialiasing at the panel's bottom edge. The CSS declares
  `margin-bottom: 6px` (the primitive's `sideOffset`).
