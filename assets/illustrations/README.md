# Catalog illustrations

Two renders of the same composition, built out of the **real Hermes Desktop interface
pieces** — the app's own compiled CSS, its real class names, the real status bar and the real
Popover surface — and magnified so it still reads at 200 px wide.

- **2:1 catalog card (the catalog `image:` field)** — `plugin-card-2x1.html`, 1600 × 800 CSS px,
  rendered as `plugin-card-2x1@2x.png` (**3200 × 1600**, device scale 2). Section
  [2:1 catalog card variant](#21-catalog-card-variant) below.
- **16:9 detail page** — `plugin-card.html`, 1600 × 900 CSS px, rendered as
  `plugin-card@2x.png` (**3200 × 1800**). Section
  [16:9 detail variant](#169-detail-variant) below, unchanged.

Both are the plugin's **status-bar chip with its popover open**. `vendor/` — the app CSS, the
Codicon font and the dark palette both pages depend on — is shared and vendored once.

Nothing here is drawn by hand: the chip, the panel, its border, its radius, its type metrics and
its colours are the app's. The only authored CSS is the **frame** (canvas, placement, the 4.2×
magnification of the subject, two aura gradients, the coloured shadow, the brand lockup, the
caption). Every frame class is prefixed `ill-` so it cannot collide with the app's utilities —
an earlier draft used `.wordmark` and silently inherited the app's own `.wordmark` rule
(`font-family: Collapse`, `text-transform: uppercase`), which is why the prefix exists.

## 2:1 catalog card variant

### Composition and the balance split

The 2:1 frame is a two-column grid, not a corner lockup and a hero:

| Part | x (CSS px) | Size |
|---|---|---|
| left column — icon tile, wordmark, subtitle | 64.0 → 467.2 | 403.2 wide |
| gutter | 467.2 → 563.2 | **95.8** |
| right block — popover + chip on its bar slice | 563.2 → 1536.0 | 972.8 wide (256 × 3.8) |
| margins | 64 left · 64 right · **110.0 top · 109.5 above the bar** | — |

Both blocks are centred on the same horizontal axis (**brand centre y 390.0, preview centre
y 390.2**) by two absolutely positioned flex bands (`top: 0; bottom: 20px; align-items: center`),
so the top margin and the margin above the status bar are equal by construction and neither
block hangs in a corner. The preview block is right-aligned and scaled about `transform-origin:
100% 50%`, which fixes its right edge at 1536 and its vertical centre at 390 without hard-coding
the panel's own content height.

**What was rejected, and why.** A 190 px icon beside 46 px type and a 973 × 560 panel cannot be
*equal in area*: the panel's width is fixed by the legibility floor (below), so the brand block
tops out at 403 css px wide. The measured consequence, stated rather than hidden:

| Metric | Left block | Right block | Ratio |
|---|---|---|---|
| Box | 403.2 × 374.0 = **150 797 px²** | 973.0 × 560.5 = **545 366 px²** | **3.62 : 1** |
| Height | 374.0 | 560.5 | **0.67 : 1** |
| Lit mass (Σ luminance above the background) | 3 264 805 | 10 113 035 | 3.10 : 1 |
| High-contrast ink (text, button, ring) | 18 056 px | 48 208 px | 2.67 : 1 |
| Colour mass (Σ chroma > 6) | icon **2 985 600** | accent button **5 974 216** | **0.50 : 1** |

The last row is the one this pass was about: at the earlier 360 px tile the icon's saturated
coral/yellow carried more colour mass than the accent button and the left block read **heavier**
than the preview even with equal heights. At 190 px the icon carries **half** the button's colour
mass and the tile is 36 100 px² (23.9 % of the brand box), so it no longer competes by saturation.

The consequence of following that instruction is that the left block is now the *lighter* half by
every area metric — **3.1 : 1 in lit mass, 0.67 in height** — so "within ~10 %" is **not** met. It
cannot be met while the icon stays at 180–200 px: to equalise heights the tile has to return to
≈ 360 px (measured 1.00 : 1 at that size, and that is exactly the version the operator rejected for
its colour mass), and to equalise areas the panel would have to fall to ≈ 500 css px wide, i.e.
`scale(2.0)`, where 11 px type renders at **2.75 px** at 200 px wide — far below the floor.

**Smallest acceptable compromise, if the 10 % target matters more than the icon size:** tile
250 px, wordmark 46/56, subtitle 20 → brand box 403.2 × **434** = 175 071 px² (3.11 : 1 area,
**0.77 : 1** height, colour mass ≈ 5.17 M ≈ **0.87** of the button). That is the knee of the
curve: the icon stops being a small badge, no colour mass exceeds the button's, and the two
columns read as two halves. The committed render follows the operator's 190 px instruction
instead; changing it is a one-line edit to `.ill-brand .ill-tile` plus the two margins.

### Layout change from the first 2:1 pass (the bottom band)

The real 20 px status bar used to be inset to the same 64 px margins as the columns, which made it
read as a **detached black rectangle** floating under the panel. It now:

- spans **x 0 → 1600**, edge to edge (`left: 0; right: 0`), flush with the bottom of the canvas;
- keeps its **top hairline visible** — `border-top: 1px solid var(--ui-stroke-secondary)`, measured
  as `(25, 38, 62)` on device rows 1560–1561 = **1.0 css px**, present at x = 1 and at x = 1598;
- sits **below** everything: the left column's subtitle ink ends at y 575.5, 204 css px above the
  bar's hairline, so nothing shares its band.

The one rule this trades away: the **bottom two corner pixels are the bar's own fill `(1, 4, 9)`**
instead of `--ui-bg-chrome`. An edge-to-edge, bottom-flush bar cannot leave the corners
background. The top two corners are still exactly `(13, 16, 21)`, and no aura or shadow paint
reaches any corner.

### Scale and effective type sizes

The chip + popover block is magnified with `transform: scale(3.8)` (origin `100% 50%`); the status
bar and the brand column are at authored size. Never by editing component styles.

| Element | Real | Rendered | At 200 px wide (÷8) |
|---|---|---|---|
| `This conversation`, `default` (`text-xs`) | 12 px | **45.6 px** | 5.7 px |
| mode buttons, `med → high`, route line (`text-[0.6875rem]`) | 11 px | **41.8 px** | 5.2 px |
| `zap` glyph (`text-[0.75rem]`) | 12 px | 45.6 px | 5.7 px |
| wordmark (frame) | — | **46 px** / 56 px line | 5.75 px |
| subtitle (frame) | — | 20 px / 28 px line | 2.5 px |
| real bar chip (product size, unscaled) | 11 px | 11 px | 1.4 px |

**Legibility at 200 px wide — yes.** The popover title, `default`, the accent button and
`med → high` all read at that size (measured on a 200 × 100 downscale of the PNG). The route line
and the product-size bar chip do not, by construction: the brief keeps the real bar at product size.

### Render

Windows, Microsoft Edge (`msedge` is not on `PATH` on this machine). **A fresh
`--user-data-dir` per run is mandatory** — without it Edge exits `0` and writes nothing.

```powershell
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$ud   = Join-Path $env:TEMP ("hae-render-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Path $ud | Out-Null
$eargs = @('--headless=new','--disable-gpu','--hide-scrollbars','--force-device-scale-factor=2',
           '--window-size=1600,800',"--user-data-dir=$ud",
           "--screenshot=$PWD\plugin-card-2x1@2x.png",
           'file:///C:/Users/elekt/Projets/jev-auto-effort/assets/illustrations/plugin-card-2x1.html')
$p = Start-Process -FilePath $edge -ArgumentList $eargs -PassThru -Wait
for ($i = 0; $i -lt 80 -and -not (Test-Path "$PWD\plugin-card-2x1@2x.png"); $i++) { Start-Sleep -Milliseconds 250 }
```

The exact command line that produced the committed PNG:

```
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --window-size=1600,800 --user-data-dir=C:\Users\elekt\AppData\Local\Temp\hae-render-d0f72088 --screenshot=C:\Users\elekt\Projets\jev-auto-effort\assets\illustrations\plugin-card-2x1@2x.png file:///C:/Users/elekt/Projets/jev-auto-effort/assets/illustrations/plugin-card-2x1.html
```

Exit `0`, **298 127 B, 3200 × 1600**. Two traps confirmed again on this pass: the fresh
`--user-data-dir`, and `Start-Process -Wait` followed by a poll (`&` returns before the file
exists). A third, new: **`--dump-dom` writes nothing at all in this Edge build** (all three headless
modes, exit `0`, 0 bytes), so geometry is measured from the PNG, never from DOM rectangles.

### Measured geometry

From a pixel probe of the committed PNG (CSS px; the PNG is 2× and the probe halves it). Regions
are found by their own fill colour and by contiguity, never by "not the background", so the aura
and the coloured shadow cannot be mistaken for a surface. Borders are **measured**, not assumed:
at 3.8× a 1 px border is ~4 css px, which is why "fill ± 1 px" is wrong here.

| Property | Token / expected | Measured |
|---|---|---|
| Size | 3200 × 1600, aspect 2.000 | 3200 × 1600, **2.000** |
| Top corners | `--ui-bg-chrome` | `(13,16,21)` both, exactly the token |
| Bottom corners | the bar's own fill (edge-to-edge bar) | `(1,4,9)` both |
| Page background | `--ui-bg-chrome` | `(13,16,21)` |
| Popover fill | `--popover-surface` = `--ui-bg-elevated` at 92 % | `(21,23,28)` |
| Popover border | `--ui-stroke-secondary` | `(40,53,77)` left, right, top, bottom |
| Border thickness | 1 px × 3.8 = 3.8 | **4.0 css px**, all four sides |
| Popover border box | — | x **563.0–1536.0** (973.0), y **110.0–571.5** (461.5) |
| Popover product width | `w-64` = 256 | 973.0 / 3.8 = **256.1** |
| Popover radius | `rounded-lg` = 2.4 product → 9.1 rendered | fill's top row starts 3.5 css px in |
| Panel shadow | `0 28px 64px -24px` accent 35 % | emitted `0 7.3684px 16.8421px -6.3158px` = those / 3.8 |
| Bar slice fill | `--ui-sidebar-surface-background` | `(1,4,9)` |
| Bar slice box | 20 css × 3.8 | x 563.0–1535.5, top 594.5, fill ends 670.0 (**76.0 tall**) |
| Slice hairline | frame `border-top` × 3.8 | `(25,38,62)`, 7 device rows |
| Gap panel → slice | `sideOffset 6` × 3.8 = 22.8 | **23.0** |
| Accent token | `--ui-accent` = `#4a84fe` | `(74,132,254)` |
| `default` value | accent | `(74,132,254)` at css (1403.5, 175.0); text box x 1343.0–1484.5, y 175.0–209.0 |
| Accent mode button | `bg-(--ui-accent)`, `rounded-md` | x 1135.5–1444.0 (309.0), y 243.0–335.5 (93.0); fill beside the label `(74,132,254)` |
| Hero chip accent run | `text-(--ui-accent)` | x 1215.0–1495.0, y 608.0–661.0; 1 585 px in the first 25 css px (the `zap` glyph) |
| Header type | 12 px × 3.8 = 45.6 | ink band 33.5 css px tall |
| Icon tile | 190 × 190 at x 64.0, y 203.0 | **190.0 × 190.0**, border box x 64.0–254.0, y 203.0–393.0 |
| Gauge ring (coral) | `../icon.png` | x 89.5–228.5, y 243.0–382.0 |
| Wordmark | 46/56, two lines = 112 | ink **300.5 × 99.0**, y 433.5–532.5, **2 lines** |
| Subtitle | 20/28, one line | ink 319.5 × 19.5, y 556.0–575.5, **one line** |
| Widest ink line | < 403.2 (column) | **319.5** → no overflow, no wrap |
| Real status bar | 20 px, edge to edge, flush | x **0–1599.5**, fill y 781.0–799.5 + 1 px hairline = **20.0**, flush `True` |
| Bar hairline | frame `border-top` 1 px | `(25,38,62)` on device rows 1560–1561 = **1.0 css px**, at x=1 and x=1598 |
| Bar chip | 11 px text | accent run x 1515.5–1588.5 |
| Margins | — | left **64.0** · right **64.0** · top **110.0** · above the bar **109.5** |

The coloured shadow lands on the chip's bar slice, which is why the slice's top rows are not the
exact surface colour — intended, and measured as such.

Note for the 16:9 table below: it derives its popover border box as "fill ± 1 css px", but at 4.2×
the border is 4.2 css px thick, so that box is ~1.6 css px tight per side. The 2:1 table above
measures the border instead.

### Crops

The 2:1 render needs **no crop**: it is the catalog card, 3200 × 1600 device = 1600 × 800 CSS,
ratio exactly 2.000, and it is what the catalog's `image:` field should point at (downscaled to
1200 × 600 if the catalog wants that exact size). Nothing is clipped: the popover's left border is
at 563, the wordmark's left edge at 64, and the bar spans the full width.

The 16:9 crops below still apply to the detail page.

### Alt text

**Catalog card (2:1):**

> Dark app surface split in two. On the left, the plugin's gauge icon and the title “Hermes
> Adaptive Effort” over the line “One chip; one panel to set the mode.” On the right, the
> status-bar chip “med → high” with its lightning glyph and the popover it opens: “This
> conversation · default”, the mode row “auto / once / always / ● Use default”, then “med → high”
> over “Route: opencode-go · deepseek-v4.1-flash”. The real 20 px status bar runs along the very
> bottom with the same chip at product size.

### Gaps (2:1 variant)

- **The 10 % balance target is not met**, by measurement: 3.62 : 1 in area, 0.67 : 1 in height,
  3.10 : 1 in lit mass, at the operator's 190 px icon. The compromise that gets closest without
  re-inflating the icon is 250 px (0.77 height, 3.11 area, colour mass 0.87 of the button); see
  the table above. This is reported rather than papered over.
- **The two bottom corner pixels are the bar's fill, not the page background** — unavoidable with
  an edge-to-edge bottom-flush bar, and requested. The top two corners and every other edge region
  are exact background.
- **Dark only.** The illustration is the app's `nous` dark palette; no light-mode variant.
- **200 px legibility is a judgement**, checked on a 200 × 100 downscale: title, `default`, the
  accent button and `med → high` read; the route line and the 11 px bar chip do not.
- **The mode buttons are unfilled** because `--ui-fill-secondary` does not exist in this app build
  (`vendor/plugin-fallbacks.css`); that is the app's real rendering.
- **`--dump-dom` produces no output** in the Edge build on this machine, so no DOM-level rectangle
  check exists; every number here comes from the PNG.
- **Cost/cache claims:** none made. The illustration asserts nothing about behaviour.

## 16:9 detail variant

### Provenance

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

### Scale

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

### Render

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

### Measured geometry

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

### Crops

All rectangles are CSS px of the 1600 × 900 page; double them for the PNG. **These apply to the
16:9 detail render only** — the catalog card is the 2:1 file above, which needs no crop.

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

### Alt text

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

### Verifying a change

Re-render, then probe the PNG instead of looking at it: four corners exactly `(13,16,21)`; popover
fill `(21,23,28)` inside a `(40,53,77)` border on all four sides with the box at
480/1548 × 256/759; the chip's bar slice `(1,4,9)` 84 css px tall at y 788–871.5; accent
`(74,132,254)` at the `default` value and at the button fill; the real bar 20 css px tall, flush,
x 40–1555.5; top margin 48.0. The probe is a throwaway script (`%TEMP%\opencode\hae_probe_t10.py`)
and is not committed; it needs `$env:PYTHONIOENCODING = "utf-8"` because the chip label contains an
arrow and the Windows console codepage is cp1252. It also writes a 200 × 200 box-downscaled
thumbnail for the legibility check.

### Gaps

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
