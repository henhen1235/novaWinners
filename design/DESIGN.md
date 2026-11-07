# Rehearsal design system

One identity for the viewer, the replay UI (WS-C), the plan renderer and the
iOS app. Tokens live in `tokens.json` (Python, Three.js) and `tokens.css`
(same names as CSS custom properties, `--<group>-<name>`). Change a value in
both files; `tests/test_render_plan.py` fails if they drift.

## Idea: a drawing sheet at night

Rehearsal turns a building into a drawing that you can run people through.
The screen is treated as an architectural sheet printed in cyanotype ink: a
deep blue ground, pale drafting lines, a white card massing model, and a title
block that states what was drawn, from what source, and whether it passed the
validator. The brand mark is a drawing reference bubble (the circle with a
split line used to call out sections and details on a plan).

Color carries meaning and nothing else. Green is the ISO 7010 safe-condition
color and only ever means "way out". Red is fire and blocked routes. Amber is a
warning that needs a human. Everything else is blue-gray so those three read
from the back of the room.

## Color

| Token | Hex | Use |
|---|---|---|
| `color-ground` | #0F2135 | Page and 3D background |
| `color-surface` / `color-raised` | #142A40 / #1B354F | Panels, controls |
| `color-line` / `color-line-strong` | #284460 / #5A7C9E | Grid, hairlines / control borders (3.4:1) |
| `color-text` / `color-text-muted` | #EAF0F6 / #A3B6CA | Text (12.8:1 / 7.0:1 on surface) |
| `color-drafting` | #8DB4DC | Room outlines, room labels in 3D |
| `color-model` | #D5DEE8 | The scanned or extruded building (white card model) |
| `color-walkable` | #6F93B8 | Navmesh and walkable floor, always translucent |
| `color-exit` | #007A43 | Exit fills with white text (5.4:1), ISO 7010 green |
| `color-exit-bright` / `color-route` | #3DDB8E | Exit markers, labels and the escape route on dark (8.2:1) |
| `color-hazard` / `color-hazard-bright` | #C8261E / #FF7A5C | Fire, smoke, blocked door: fill / on dark |
| `color-warn` | #F5B83D | Validator warnings, agents that need attention |

Red and green are never the only signal. Exits carry the running-figure
pictogram or the word "Exit"; hazards carry a triangle or hatching. The bright
green (light) and the hazard orange (mid) also differ in lightness, so they
separate under deuteranopia and protanopia.

The `plan` group is the light, printable version used by `tools/render_plan.py`:
cool vellum paper (#F5F7FA), poured ink walls (#1B2633), room tints per room
type. Room tints avoid red and green so exits and hazards stay unambiguous.

## Type

- Barlow (sans, 400/500/600): all UI text. A DIN-lineage grotesque, the family
  that safety signage and ISO 3098 drawing lettering descend from. Fallback
  `DIN Alternate` on macOS keeps the look offline.
- Barlow Condensed (500/600): room names and labels drawn on the plan or in 3D,
  where width is scarce.
- IBM Plex Mono (400/500): measurements only (meters, seconds, counts). Never
  for labels.

Scale (px, classical): 12, 14, 16, 18, 21, 24, 36. UI body is 14; the route
readout number is 36. Sentence case everywhere. No all-caps labels, no tracking
tricks.

## Layout

```
+-------------------------------------------------------------+
| (R) Rehearsal  building name          Orbit  Plan  Level L1  |
+-------------------------------------------------------+-----+
|                                                       |Route|
|                3D stage, sheet frame                  |Layer|
|                                                       |Check|
|                                       +--title block-+|     |
|                                       | source, date ||     |
+---------------------------------------+--------------+------+
```

- One left-aligned top bar, one right rail (280 px), the stage fills the rest.
- The rail order is route readout, layers (the legend is the layer toggle
  list, each with its swatch), validator checks.
- The title block sits bottom-right of the stage, like a real sheet.
- Radii are 2 to 4 px. Drawings have corners; nothing is a pill.

## Lines in 3D and on the plan

- Walls and model: `color-model`, lit by a cool hemisphere light plus one key
  light. On the plan, walls are solid `plan-ink` poché.
- Rooms: 1 px `color-drafting` outlines with a condensed label (name, area).
- Grid: 1 m minor in `color-line`, 5 m major in `color-line-strong`.
- Route: 4 px `color-route`, drawn from the start point to the exit in 700 ms.
- Exits: green disc with a slow 2.4 s pulse ring; the only ambient motion.

## Motion

Motion explains, never decorates: the route draws itself so the eye follows
it, and exits pulse so they can be found. Both stop under
`prefers-reduced-motion: reduce`.

## Adopting it (WS-C)

```html
<link rel="stylesheet" href="design/tokens.css">
```
```css
body { background: var(--color-ground); color: var(--color-text); font: 14px/1.45 var(--font-sans); }
.metric { font-family: var(--font-mono); }
```
In Three.js read the same values at runtime:
`getComputedStyle(document.documentElement).getPropertyValue("--color-exit-bright")`,
or load `tokens.json`.

Agent states for the replay: calm `color-text`, moving `color-route`,
deciding `color-warn`, hurt or trapped `color-hazard-bright`, evacuated
`color-exit-bright` at 40 percent opacity.
