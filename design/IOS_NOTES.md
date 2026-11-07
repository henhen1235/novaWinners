# RoomScanner styling notes (SwiftUI)

Suggestions for `ios/RoomScanner/RoomScanner/RoomScannerApp.swift`, to apply
after the demo build is installed. Nothing here changes scanning or export.
Values come from `design/tokens.json`.

## Palette as SwiftUI colors

Add one file, `Theme.swift`:

```swift
import SwiftUI

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }
    static let ground = Color(hex: 0x0F2135)
    static let surface = Color(hex: 0x142A40)
    static let line = Color(hex: 0x284460)
    static let textMuted = Color(hex: 0xA3B6CA)
    static let exit = Color(hex: 0x007A43)        // ISO 7010 green, white text 5.4:1
    static let exitBright = Color(hex: 0x3DDB8E)  // green text or strokes on dark
    static let hazard = Color(hex: 0xFF7A5C)
    static let warn = Color(hex: 0xF5B83D)
}
```

Green means "done or safe" only. Do not use it for neutral actions.

## Control panel

The bottom panel currently has five equal blue `.borderedProminent` buttons.
Give it one primary action per state and a quieter secondary row:

- Primary: Start while idle, Stop while scanning, Finish once a room is
  captured, Share after export. Style it `.borderedProminent` with
  `.tint(.exit)` only for Finish and Share (they mean "this building is
  ready"); use `.tint(.white)` with `.foregroundStyle(Color.ground)` for
  Start and Stop.
- Secondary: "Add another room" and Export as `.bordered` with
  `.tint(.textMuted)`.
- Use `.controlSize(.large)` on the primary button so it is easy to hit while
  walking, and keep every button at least 44 pt tall.

Panel surface: replace `.ultraThinMaterial` with
`Color.surface.opacity(0.92)` and a 1 pt `Color.line` stroke, corner radius 4,
so the panel reads as part of the same drawing sheet as the viewer:

```swift
.background(Color.surface.opacity(0.92), in: RoundedRectangle(cornerRadius: 4))
.overlay(RoundedRectangle(cornerRadius: 4).stroke(Color.line, lineWidth: 1))
.environment(\.colorScheme, .dark)
```

## Status line

- Status text: `.font(.callout)` is fine; set `.foregroundStyle(.white)`.
- Show room count and elapsed time in monospaced digits so they do not jitter:
  `Text("\(rooms) rooms").font(.system(.callout, design: .monospaced))`, or
  `.monospacedDigit()` on the existing text.
- When `scan.status` reports an error, prefix an
  `Image(systemName: "exclamationmark.triangle.fill")` in `.warn`; do not
  rely on color alone.

## Brand

A small header above the status text: the word "Rehearsal" in
`.font(.system(.headline, design: .default, weight: .semibold))` and, to its
left, the drawing reference bubble (a 22 pt circle with a horizontal line
through it and a filled triangle on the right), drawn with `Circle()`,
`Rectangle()` and a `Path`, stroked in `Color(hex: 0x8DB4DC)`. If time is
short, the word alone is enough.

## Unsupported device screen

Replace the plain `Text` with a centered `VStack` on `Color.ground`:
`Image(systemName: "iphone.gen3.radiowaves.left.and.right")` at 44 pt, the
existing sentence in `.textMuted`, and a second line telling the user what
to do instead: "Load a blueprint or a Polycam GLB on the laptop."

## Motion and accessibility

- No custom animation is needed; RoomPlan's own capture overlay already
  animates. If a "Room captured" confirmation is added, use
  `.transition(.opacity)` and respect `@Environment(\.accessibilityReduceMotion)`.
- Keep Dynamic Type working: do not set fixed font sizes on the status text.
