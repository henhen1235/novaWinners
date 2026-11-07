# Rehearsal pitch: speaker notes

Deck: open `docs/pitch/index.html`, press `F` for fullscreen. Right / Space / PageDown next,
Left / PageUp back, `1`-`8` jump to a slide, Home / End.

Budget: 5:00. Spoken script about 4:30, 0:30 buffer. Slides 5 to 7 carry the demo: either
the recorded footage in their media slots, or switch to the live viewer for those three
slides. Word counts assume about 150 words a minute.

| Slide | Topic | Time | Ends at |
|---|---|---|---|
| 1 | Squirrel Hill fire | 0:25 | 0:25 |
| 2 | The stakes | 0:25 | 0:50 |
| 3 | Rehearsal | 0:20 | 1:10 |
| 4 | How it works | 0:40 | 1:50 |
| 5 | Tepper (demo) | 0:35 | 2:25 |
| 6 | Ask why (demo) | 0:40 | 3:05 |
| 7 | Exit blocked (demo) | 0:45 | 3:50 |
| 8 | Accessibility, close | 0:40 | 4:30 |
| | Buffer | 0:30 | 5:00 |

If you run long, shorten slide 4 to one sentence per stage and drop the cost line on slide 7.

---

## 1. Squirrel Hill fire (0:25)

"On September 9th last year, a fire broke out in Squirrel Hill, just a few miles from here.
Firefighters evacuated the residents, including people with mobility issues.

Every building has an evacuation plan on paper. That night tested whether it worked."

## 2. The stakes (0:25)

"Not every fire ends that way. In 2024 there were about 343,000 residential building fires
in the U.S., and 2,975 people died in them.

Knowing where the exits are is not the hard part. The hard part is knowing what happens
when one of them can't be used."

## 3. Rehearsal (0:20)

"So how do you test an evacuation plan without a real emergency? That's Rehearsal. We turn
a real building into a simulation, so we can see what happens when something goes wrong,
and try a fix before anyone needs it."

## 4. How it works (0:40)

"It works in five steps.

First, we bring a building in, either from an iPhone LiDAR scan or from its floor-plan PDFs.

Second, we build a 3D model: rooms, doors, exits, and on multi-floor buildings, stairs.
Software checks it before anything runs, for example that every room has a way out.

Third, we simulate the people. Each occupant is an AI that decides what to do from what it
can see and hear. The simulator handles the actual walking, queuing at doors and stairs.

Fourth, the stress test. One AI agent tries to break the building: it blocks an exit or
starts a fire. A second agent proposes fixes, like wardens or signs, and we re-run.

Finally, you get replays that show who got out, who didn't, and which fix helped."

Transition: "Here's a real building."

## 5. Tepper (0:35)

Media slot `tepper`: replay of the Tepper fire in the viewer, floors side by side.

"This is CMU's Tepper building. We rebuilt five floors straight from CMU's own floor-plan
PDFs: 633 rooms, 22 exits, 12 stair connections, and the checker found zero errors.

Here a fire starts on the third floor and you can watch people move floor by floor down the
stairs. Every dot is one person making their own decisions."

If live: open the Tepper side-by-side URL from `out/demo/urls.txt`, press play at 16x.

## 6. Ask why (0:40)

Media slot `interview`: clicking an occupant in the hall run and asking "Why did you go that
way?" in the interview panel.

"Each AI occupant decides from what it can actually perceive: the alarm, smoke it can see,
what people nearby say, which exits it knows about.

So we can click on someone and ask why. The answer can only use what that person saw and
decided at the time, and we check it. If an occupant says it left because it saw the fire,
we re-run that moment without the fire. When we did, its choice changed seven times out of
seven. When we removed a belief it had mentioned, it changed zero times out of seven. So the
answer tells you which reasons really mattered."

If live: pause the replay, click one occupant, type the question. It needs network and the
API key; if it hangs, keep talking and move on.

## 7. Exit blocked (0:45)

Media slots `hallBaseline`, `hallAttacked`, `hallFixed`: the three replays of the recorded
36-occupant hall run, same camera angle.

"Now the stress test. This is a real hall we scanned with an iPhone, with 36 AI occupants.
With every exit open, 34 of 36 get out.

Then our adversary agent picked the exit that 23 people were using, nearly 40 percent of
the exit width, and locked it. Only 15 got out.

The optimizer can only choose fixes a building could really make. It sent a key holder to
reopen the exit, posted two wardens, and added exit signs. Re-run: 33 of 36.

That whole run was 400 AI decisions and cost under 9 cents. It's one run, so we read it as
a finding, not a guarantee."

## 8. Accessibility, close (0:40)

Media slots `tepperNoPlan`, `tepperPlan`: the two Tepper fire replays, ending on floor 4.

"Averages hide who gets left behind. Back in Tepper, we ran a fire with 173 occupants. This
run uses simple fixed rules instead of AI, so it's fast and repeatable.

Without an assistance plan, 169 of 173 got out. Three of the four left inside were
wheelchair users waiting at stair refuges on the fourth floor. With a plan, staff carried
all three down the stairs: 172 of 173.

That's what Rehearsal is for. It's a planning tool, not a certification. It shows you who a
building leaves behind, before a night like Squirrel Hill. Thank you."

---

## Demo media and fallback

The media slots are filled by the `media` object at the bottom of `index.html` (put files in
`docs/pitch/img/`). Each slot should show the run the slide's numbers come from:

| Slot | Run | Numbers on the slide |
|---|---|---|
| `cover` | Any viewer view of Tepper or the hall | none |
| `tepper` | Tepper fire, floors side by side | 633 rooms, 22 exits |
| `interview` | Interview panel on the recorded hall LLM run | none |
| `hallBaseline`, `hallAttacked`, `hallFixed` | `loop_hall_r0_baseline`, `loop_hall_r1_attack`, `loop_hall_r1_fixed` | 34, 15, 33 of 36 |
| `tepperNoPlan`, `tepperPlan` | `tepper_fire_noplan`, `tepper_fire_plan` on the current Tepper model | 169 and 172 of 173 |

Never fill the hall slots with the offline tiny_office loop, and never put the Tepper LLM
loop footage on slide 8 (that slide's numbers are the rule-based run).

If footage is missing or the live viewer fails, don't apologize or debug. Stay on the slide
and narrate the result: "In the replay you'd see..." followed by the slide's numbers. The
script above already works without the video.

---

## Judge Q&A (deeper technical answers, about 20 s each)

**How does a scan become a model?** We find which way is up and snap the walls to their main
directions, rasterize the scan into a walkable grid, then detect walls, rooms, doors and
obstacles. A vision model (OWLv2) finds exit signs and removes people who were standing in
the scan. On 30 synthetic scans with known answers, door recall is 0.996, precision 0.982,
and median wall error 6 mm, at 2 to 8 seconds per scan. A real 1.67-million-triangle iPhone
mesh takes 4.8 s. Real hall scan: 19 people removed, 4 exit signs found.

**And floor plans?** CAD-exported PDFs keep their layers. We read room outlines, door symbols
and stair treads, and set each floor's scale from the printed room areas. Floors are aligned
and joined at the stairs. LLMs are not used for geometry.

**What gets validated?** One frozen scene format is shared by every input, scenario and fix.
The validator checks things like every room reaching an exit, door widths in range, and a
connected walkable mesh. Optimizer fixes are patches that go through the same checks before
they run. Tepper passes with 0 errors and some warnings (suspected wall gaps, unknown spaces
near doors) that are shown, not hidden.

**Is the LLM moving the people?** No. The simulator moves everyone on a 0.5-second clock:
shortest paths on the walkable mesh, door flow limits, stairs, refuges, staff carrying people
who can't use stairs. The LLM is only asked at decision points: an alarm, smoke, a blocked
path, a fork, someone needing help. It never outputs coordinates.

**What does the LLM actually see?** Code computes perception (what's in view, what's
audible), beliefs with a source and confidence, and fear or anxiety from published appraisal
models. The LLM gets a closed list of options and answers the five questions of the
Protective Action Decision Model (is it real, do I need to act, what can I do...). It has to
cite evidence ids, and code checks each id exists and was visible to that person. Bad output
is repaired once, then a persona rule decides. Every response is recorded, so a replay is
exact and free.

**Is the behavior realistic?** Not validated against real evacuations yet, and we say so. We
calibrated delay: our LLM occupants' median time before moving is 63.8 s against a research
target of about 60 s. There is no "panic" state on purpose; research finds mass panic rare
and delay common. In a 304-call test, the persona explained about three times more of each
choice than which model played it.

**How do the adversary and optimizer work?** Both are LLM agents using tool calls, with a
fixed heuristic fallback. The adversary can block an exit, start a fire, or plant a rumor,
and must justify the target with numbers from the building analysis or the last run. The
optimizer picks from seven fixes a facility can do: wardens, signage, a PA announcement,
unlocking an exit, drilling a familiar exit, widening a door, or an assisted-evacuation plan.

**Cost and speed?** Hall run: 400 decisions, $0.086, about 0.94 s per decision on
gemini-2.5-flash-lite. Calls run in parallel. Replays cost nothing.

**Did you run the attack and fix loop on Tepper too?** Yes, with 60 AI occupants, single run:
55 of 60 at baseline, 41 of 60 after an exit block, a classroom fire and a rumor, and 55 of
60 after the fixes (exit reopened, a PA countering the rumor, one warden). The optimizer did
not pick the assisted plan, so the three mobility-impaired occupants still waited at refuges.
That is a reason a person should review the recommendations.

**Is this Pathfinder?** Pathfinder and similar tools are trusted for crowd flow and publish
verification. They need hours to days of CAD modeling and treat behavior as parameters. We
don't claim better physics. We add going from a scan or plan to a checked model in seconds,
occupants you can question, and agents that look for failures and test fixes.

**Fire and smoke?** Scheduled events that block rooms and doors, plus a simple smoke-timing
estimate. No fire physics. Elevators are recalled in a fire and not used.

## Numbers and where they come from

| Number | Run | Source |
|---|---|---|
| 633 rooms, 22 exits, 12 stair links, 0 errors | Tepper model, 5 floors, from floor-plan PDFs | `samples/cmu/tepper/report.md`, `scene.json` |
| 34, 15, 33 of 36; 400 decisions, $0.086 | Scanned hall, 36 LLM occupants, gemini-2.5-flash-lite, seed 42, single run | `ANDREW_AGENT.md`, `docs/pitch/RESEARCH.md` |
| Exit used by 23 people, 38.8% of exit width | Same hall run, adversary's cited reason | same |
| 169 and 172 of 173 | Tepper fire, rule-based occupants, seed 42, single run | `ANDREW_AGENT.md` section 7, `docs/pitch/RESEARCH.md` |
| 7 of 7 vs 0 of 7 | Interview re-runs: fire percept removed vs stated belief removed | `README.md` |
| 63.8 s vs about 60 s | LLM pre-movement median | `README.md` |
| 343,000 fires, 2,975 deaths | U.S. residential building fires, 2024 | Team-supplied; see below |

## Still to verify before judging

- Slide 2 statistics (343,000 fires, 2,975 deaths, 2024) are not in the repo. Confirm the
  source (likely NFPA or USFA) and that the deaths figure covers the same category.
- The 173-occupant Tepper run: `sim/examples/tepper_fire.json` in the repo spawns 170
  occupants, so the 169/173 and 172/173 run (Karthik, after the Tepper reader fixes) came from
  a scenario not committed here. Make sure the `tepperNoPlan` and `tepperPlan` footage is
  from that same run, or change the numbers to match the footage.
- "Five floors": the model covers floors 1 to 5. Don't say "all floors" unless confirmed.
- Hall adversary: the docs don't say whether the recorded hall attack came from the LLM
  adversary or its heuristic fallback. The slide says "adversary agent", which is true
  either way; check `out/loop/demo_hall/report.md` before saying "the AI chose".
- Squirrel Hill details beyond the slide (building, cause, injuries) are not in the repo;
  say only what is on the slide.
