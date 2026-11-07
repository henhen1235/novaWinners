# Rehearsal pitch: speaker notes

Deck: open `docs/pitch/index.html` in a browser, press `F` for fullscreen.
Keys: Right / Space / PageDown next, Left / PageUp back, `1`-`9` jump to a sheet, Home / End.

Budget: 5:00 total. Slides 3:00, live demo 1:30, buffer 0:30.
Word counts assume about 150 words a minute. Say the numbers exactly as written.

| Sheet | Topic | Time | Ends at |
|---|---|---|---|
| 1 | Hook | 0:20 | 0:20 |
| 2 | Problem | 0:25 | 0:45 |
| 3 | What Rehearsal does | 0:20 | 1:05 |
| 4 | Live demo | 1:30 | 2:35 |
| 5 | Architecture | 0:25 | 3:00 |
| 6 | Occupant minds | 0:25 | 3:25 |
| 7 | Adversary vs optimizer | 0:25 | 3:50 |
| 8 | Accessibility and interviews | 0:25 | 4:15 |
| 9 | Limits, next, close | 0:15 | 4:30 |
| | Buffer | 0:30 | 5:00 |

If you are behind at the end of the demo, cut sheet 6 to its first and last sentence.

---

## 1. Hook (0:20)

"Fire drills test whether people know the drill. They can't test what happens when the
main exit is locked and someone says the other one is too.

What if you could rehearse an evacuation before the alarm is real? That's Rehearsal."

## 2. Problem (0:25)

"Evacuations go wrong on decisions, not walking speed. At the World Trade Center in 2001,
the median survivor in Tower 1 waited three minutes before starting to leave. At the
Station nightclub, about two thirds went for the door they came in. And mass panic is
rare: people wait, finish what they're doing, and believe what they hear.

Professional egress tools need days of CAD work and treat behavior as fixed parameters."

## 3. What Rehearsal does (0:20)

"Rehearsal takes an iPhone scan or a building's floor plans and makes a validated 3D model
in seconds. It runs a drill with AI occupants. Then an adversary agent attacks the
building, an optimizer agent fixes it, and we measure before and after. People left
behind are the finding, not a bug."

## 4. Live demo (1:30)

Handoff line: "Let me show you instead of telling you." Switch to the viewer window
(Alt+Tab). URLs are in `out/demo/urls.txt` after `scripts/demo/start.sh`; full script in
`docs/DEMO.md`.

**0:00-0:30 Tepper, five floors.** Open the Tepper side-by-side URL (`&view=side`), press
play, speed 16x.
"This is Tepper here at CMU, floors 1 to 5, built from its CAD floor plans: 576 rooms,
915 doors, 29 exits, zero validator errors. Fire on floor 3, 170 occupants moving across
floors and down stairs."

**0:30-1:00 Attacked vs fixed.** Open the compare URL (`&compare=` ... `names=Attacked,Fixed`).
"Same building, same seed. Left, the adversary locked the busiest exit. Right, the
optimizer's fix: a key holder, wardens, signage. You can watch where people stall."

**1:00-1:30 Interview.** Pause, click one occupant, type "Why did you go that way?"
"Every answer comes only from that occupant's decision log, and the reasons are tested by
removing them. I'll show you the numbers in a second."

Return to the deck: press `5`.

Which runs are on the laptop: the recorded 36-person LLM hall loop (`loop_hall_*`) and the
hall scan only exist where `out/loop/demo_hall/` and `samples/scans/hall_mesh/mesh.ply`
were copied before `setup.sh`. Without them the compare URL uses the offline tiny_office
loop. Then say "this is an offline run with rule-based occupants; the LLM numbers are on
the next slides." Never present an offline run as the LLM run.

### If the demo fails

Do not debug on stage. Say "Let me give you the recorded version" and either play
`out/demo/fallback.mp4` (if recorded) or stay on sheet 4 and narrate, pointing at the
three moments on the slide (about 45 s, then move on to sheet 5 and use the time saved):

"In the viewer you'd see Tepper at CMU, five floors from its floor plans: 576 rooms, 29
exits, zero validator errors, with 170 people moving down the stairs. Next, two runs side
by side: one where the adversary locked the busiest exit, and the optimizer's fix. Then I'd
click on one person and ask why they went that way. The answer comes only from their
decision log. The next slides have the measured results from those runs."

If only the interview fails (no network or API key): "The interview server needs network
for new questions," and go straight to sheet 8, which has the tested result.

## 5. Architecture (0:25)

"Here's how it works. Three inputs (iPhone LiDAR, CAD floor plans, blueprints) become one
frozen scene format, and a validator gates everything: zero errors or it doesn't run.
Geometry is computed, never generated. LLMs never output coordinates. They make
judgments, and code checks those too. On 30 synthetic scans, door recall is 0.996 and
median wall error is 6 millimeters. A real 1.67 million triangle scan ingests in 4.8
seconds."

Point at the green dashed loop: every attack and fix goes back through the same validator.

## 6. Occupant minds (0:25)

"Each occupant is code plus one LLM call. Code computes what research can measure: what
you can see, what you believe and from whom, how afraid you are. The LLM answers the five
questions of the Protective Action Decision Model and has to cite evidence ids, which we
validate. Calibrated this way, their median time to start moving is 63.8 seconds against
a research target of about 60. And no panic state, on purpose."

## 7. Adversary vs optimizer (0:25)

"This is a real scanned hall with 36 LLM occupants. Baseline: 34 of 36 get out. The
adversary locks the exit 23 people were using, 38.8 percent of the exit width: 15 of 36.
The optimizer can only pick fixes the code can execute. It unlocks the exit, posts two
wardens, adds signage: 33 of 36. 400 decisions cost 8.6 cents. Replays are free. Single
run, seed 42."

## 8. Accessibility and interviews (0:25)

"Averages hide who gets left behind. In Tepper, without an assisted-evacuation plan, 166
of 170 get out, and three wheelchair users are left waiting at stair refuges on floor 4.
With a plan, staff carry all three out.

And you can ask why. When we remove the fire from an occupant's view, its choice flips 7
times out of 7. Remove a stated belief: 0 out of 7. The answer tells you which reasons
actually mattered."

## 9. Close (0:15)

"Rehearsal is a planning tool, not a certification. Decisions aren't validated against
real evacuations yet, there's no fire physics, and these are single runs. Next: movement
verification and many seeds. Scan or plan in. Drill, break, fix, measure. Thank you."

---

## Likely judge questions (keep answers to ~20 s)

- **How do you know it's right?** Movement uses standard walking speeds and door-flow
  limits; decision behavior is not validated against real evacuations yet, and we say so.
  Pre-movement is calibrated to research (63.8 s vs about 60 s). Next step is standard
  movement test cases and multiple seeds.
- **Why LLMs instead of rules?** Rules cover what the modeler anticipated. An LLM occupant
  reacts to content: a rumor, a warden contradicting a sign. Personas are plain text, and
  every choice cites evidence we can test. Persona explains about 3 times more of the
  choice than which model plays it (304 calls).
- **Cost and determinism?** One LLM call per decision, about 0.94 s, $0.086 for the
  36-person run. Every response is recorded, so replays are exact and cost nothing.
- **Isn't this just Pathfinder?** Pathfinder is the trusted tool for flow and has published
  verification. Its non-IFC import needs doors and floors tagged by hand. We go from scan
  or plan to a validated model in seconds, and add the attack/fix loop and interviews. We
  don't claim to be first at LLM evacuation agents.
- **Fire and smoke?** Scheduled blocking events plus a closed-form smoke-clock estimate.
  No CFD.
- **Elevators?** Recalled in a fire and not used; occupants may walk to a lobby and turn
  back. A modeling choice.
- **Tepper adversary loop?** A 60-occupant LLM run was in progress at submission; do not
  quote numbers unless `out/loop/demo_tepper/report.md` exists and you have read it.

## Claims to avoid

"First", "validated", "certified", "compliant", "safe", "realistic panic", fire or smoke
physics, any scale beyond what ran (36 LLM occupants, 170 rule-based occupants).
