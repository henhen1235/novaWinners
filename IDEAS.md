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
