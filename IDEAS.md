# Ideas backlog (after the core works)

Core first: real scan -> valid scene.json -> WS-C sim. Ideas below wait until then.
Sources: research/PITCH_IDEAS.md, research/EVAC_PSYCHOLOGY.md, research/LLM_LATENCY.md.

## WS-B (scan side): AI around the geometry
- Occupancy and persona seeding: LLM reads each room (type, size, furniture,
  exits nearby) and proposes who is in it (count, archetype from
  EVAC_PSYCHOLOGY.md: staff, regular, visitor, older adult, mobility impaired,
  parent with child, skeptic). Output in `extensions.occupancy` for WS-C. ~30 min.
- AI scan inspector: vision model reviews debug/overview.png and flags likely
  missed doors, phantom rooms, leaky walls; pipeline verifies each claim
  geometrically before applying. "Agents checking agents".
- Natural-language corrections: "east door is locked", "boxes in the kitchen
  hallway" -> structured edits (also usable by the WS-C adversary).
- Exit sign reading from photos listed in scan_notes.json (vision LLM).
- Live scan of the judging room the morning of the demo (keep a recorded fallback).

## Scenarios and model casting (user idea, 2026-11-07; research: research/SCENARIOS_AND_MODELS.md)
- Pinpoint an emergency at a location (room id/name or a clicked point), with a
  type: fire, smoke only, active intruder, earthquake, gas leak, flood, bomb
  threat. Each type triggers different research-backed behavior (e.g. fire:
  evacuate away from smoke; active intruder: run/hide/fight, avoid the threat,
  lock down; earthquake: drop/cover/hold, evacuate after shaking).
- Scenario chatbot: knows every labeled space in scene.json (room names come from
  our LLM labeling) and turns "fire in the kitchen, intruder at the east
  entrance, then deploy 20 people on floor 2" into a structured scenario.json
  (events + occupant placements) that the sim validates and runs.
- Model casting: give different occupants different LLMs, using documented
  model tendencies (risk-taking, compliance, cooperation) as a source of
  behavioral diversity, alongside explicit persona prompts.

## From IBS27/rumi (HackMIT 2026; NO LICENSE: ideas only, do not copy code)
- Phone-to-browser pairing: RoomScanner uploads the scan straight to the web
  app (QR code / short code), no AirDrop. Makes a live judging-room scan smooth.
- "The model proposes; code checks" as a pitch line: LLM proposes semantics,
  occupants and fixes; geometry + contract validator accept or reject.
- First-person view in the viewer/replay: see the evacuation through one
  occupant's eyes.

## WS-C (sim side), from research
- Interview the occupant: click an agent, ask why it went left; answers come
  only from its decision log.
- Classic shortest-path vs LLM agents side by side, with a rumor scenario.
- Accessibility personas with per-group metrics (who never got out).
- Pre-movement delay sampled in code (lognormal), not left to the LLM
  (LLMs are too rational); familiar-exit bias; herding under poor signage.
- Latency: gemini-2.5-flash-lite with reasoning off, fixed reaction delay of
  k ticks, record/replay every decision, 2 s tick deadline with fallback rule.
- RSET breakdown: deciding vs walking vs queuing time; cost meter on screen.
- Validation tests: pre-movement distribution, familiar-exit imbalance
  (Station fire analogue), herding under poor signage.

## Pipeline hardening (nice to have)
- Multi-level scans and Path B stair detection.
- Diagonal walls as clean walls (Hough fallback when not Manhattan).
- Point-cloud specific orient tuning.
