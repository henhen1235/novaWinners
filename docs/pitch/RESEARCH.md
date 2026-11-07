# The research behind Rehearsal, in plain terms

"You can't drill a 3 a.m. fire. You can rehearse one."

One slide-sized chunk per topic. Every citation below is copied from the repo's notes
(research/, docs/, DECISIONS.md, IDEAS.md, code docstrings). Numbers marked "single run"
are one seed; do not present them as averages. Where a value is ours and not from a
source, it says "(our assumption)".

## 1. How people really behave in emergencies

### People decide before they move

What it says: people act on cues (alarm, smoke, what others do) and work through a few
questions first: is it real, does it affect me, what can I do, must I act now. If any
answer is unsure, they look for information instead of leaving.

How we use it: every LLM occupant answers these questions in order and must say which
cue it acted on.

Source: Lindell and Perry, "The Protective Action Decision Model: Theoretical
Modifications and Additional Evidence", Risk Analysis 32(4):616-632, 2012.

### The wait, not the walk, takes the time

What it says: the delay before people start moving is often the biggest part of
evacuation time. At the World Trade Center in 2001 the median survivor waited 3 min
(WTC 1) to 4 min (WTC 2); 30 percent helped someone first.

How we use it: each persona has its own pre-movement delay, and the LLM can extend it.

Our number: LLM pre-movement median 63.8 s against a research target of about 60 s.

Source: NIST NCSTAR 1-7, Averill et al. 2005; Kuligowski and Mileti 2009, Fire Safety
Journal.

### Mass panic is rare

What it says: disaster research finds people mostly stay orderly and help each other.
In the London 7/7 bombings study, selfish behavior was rare and mutual help common.
What costs time is delay, the wrong exit and crowding at familiar doors.

How we use it: there is no "panic" state in the sim. Fear changes choices, not sanity.

Source: Drury, Cocking and Reicher, social identity model of collective resilience
(London 7/7, 90 survivor accounts); disaster myths paper (10.1111/jasp.12176).

### People follow others and the way they came in

What it says: people head for familiar exits and familiar people. At the Station
nightclub fire about two thirds tried the main entrance. In VR studies people followed
other people's routes.

How we use it: occupants know only the exits their persona would know, see who is moving
nearby, and hear what others say.

Source: Sime 1985, "Movement toward the familiar", Environment and Behavior; NIST NCSTAR
2; Kinateder et al. VR tunnel fire; Comunale, Kinateder and Warren, APS 2017.

### Families move together, people go back for things

What it says: groups wait for their slowest member, and parents go back for children.
In a high-rise residential drill, 82 percent of those who tried to leave first got
dressed or collected belongings.

How we use it: a child stays with its parent; personas can gather, help or turn back
before leaving.

Source: NIST NCSTAR 1-7 (groups); Proulx 1998 and the IKEA play-area example, both via
BRANZ SR143 (Whiting 2005).

## 2. Emotion and social contagion

What it says: emotions follow from how a person sizes up a situation (how threatening,
how certain, can I cope). Fear spreads between nearby people; a graded, distance-based
model matched real crowd footage better than an on/off "infection" model.

How we use it: fear and anxiety are computed in code from appraisals and from
neighbours, then handed to the LLM. The LLM never invents its own emotion level.

Source: Gratch and Marsella 2004 (EMA), Cognitive Systems Research; Bosse et al. 2013
(ASCRIBE), Autonomous Agents and Multi-Agent Systems; Tsai et al. 2013, same journal.

## 3. LLM occupants that cite evidence

What it says: an LLM told "there is a fire alarm" leaves at once by the nearest exit, the
too-rational occupant research says does not exist. Explanations a model writes are
often not what actually drove its answer.

How we use it: each occupant sees only its own cues, each with an id. Its answer must
cite those ids. Code checks every answer; a bad one gets one repair try, then a rule
fallback. LLMs never output coordinates.

Source: research/EVAC_PSYCHOLOGY.md section 4; Turpin et al. 2023 (unfaithful
explanations), cited in docs/AGENT_COGNITION.md.

## 4. Interviews you can trust

What it says: people and models both give reasons that did not drive their choice.
The accepted check is to remove a stated reason and see if the decision changes.

How we use it: when you ask an occupant "why that exit?", we re-run its decision with
each cited reason removed and label it necessary or not decisive.

Our number: removing the fire percept flipped the choice in 7 of 7 re-runs; removing a
stated belief flipped it in 0 of 7.

Source: Nisbett and Wilson 1977; DeYoung et al. 2020 (ERASER); Atanasova et al. 2023;
Madsen et al. 2024; Matton et al. 2025, as cited in docs/AGENT_COGNITION.md.

## 5. Different models behave differently

What it says: published studies find model "personalities" in games and gambling tasks,
but they flip between versions of the same family (Llama2 cooperative, Llama3
exploitative).

How we use it: we measured it ourselves, then tested giving each persona group its own
model in a fire drill.

Our number: in 304 calls (8 models x 4 personas x 3 scenarios), persona explained about
3x more of the choice than the model. Casting run, hall, 36 occupants, seed 42: 33/36
out with mixed models vs 34/36 with one model; the same two skeptics stayed.

Source: Akata et al., Nature Human Behaviour 2025; Fontana, Pierri, Aiello, ICWSM 2025;
Jia et al., arXiv 2406.05972; research/MODEL_CASTING_RUN.md.

## 6. Movement and flow

What it says: adults walk about 1.34 m/s on the level. Office stair descent in drills
averages 0.44 m/s. A door passes at most about 1.3 people per second per metre of width.
People with mobility impairments move at roughly 0.6 to 0.8 m/s.

How we use it: personas draw speeds around these values; doors and stairs (1.1 people/s/m)
cap flow; wheelchair users wait at stair refuges for a helper, who carries them more
slowly. In a fire, elevators are recalled; some people walk to the lobby, find it out of
service, and decide again.

Source: Weidmann 1993 via Vadere; NIST TN 1839; SFPE hydraulic model (value unverified
in our notes); Boyce, Shields and Silcock 1999 (speeds unverified). Elevator attempt rates
and stair flow cap: (our assumption).

## 7. Fire and smoke timing, and how many people a room holds

### The smoke clock

What it says: in a closed room, a standard fast-growing fire fills the ceiling with smoke
down to head height in a time you can estimate from room area and ceiling height.

How we use it: every room gets a "smoke reaches head height" time. It is an estimate from
one equation, not a fire model.

Our number: an 18.7 m2 room with a 3.0 m ceiling reaches 1.8 m in about 50 s.

Source: Tanaka and Yamana 1985, Fire Science and Technology 5(1):31-40; Zukoski plume
constant.

### Occupant load

What it says: building code sets how many people a room is designed for: floor area
divided by a factor per use (offices 150 ft2 per person gross, classrooms 20 ft2 net).

How we use it: we seed each room with that many occupants.

Source: IBC 2021 Table 1004.5, quoted from memory and not re-checked against the code
text (flagged in analysis/occupancy.py).

## 8. Weak spots in the building

### Single points of failure

What it says: if blocking one door or room cuts some rooms off from every exit, that door
is a single point of failure.

How we use it: we block each door and room in turn on the room-door graph and list who
gets cut off and how much exit width is lost.

Source: our method (analysis/spof.py), standard graph search.

### Pinch points

What it says: the narrowest spot on a route limits how fast people can get through it.

How we use it: for every room we find the narrowest clear width on its route to the
nearest exit and flag anything under 0.9 m (our threshold).

Source: our method (analysis/pinch.py); flow uses 1.3 people/s/m, SFPE maximum specific
flow, unverified.

## 9. Adversary vs optimizer

What it says: real tools simulate scenarios a human writes. Here one agent attacks the
building and another proposes fixes the facility could actually make, then the sim re-runs.

How we use it: attack, fix, re-run, compare.

Our number: Tepper, 60 LLM occupants, seed 42, single run. Baseline 55/60. Attacked
(exit blocked, classroom fire, rumor) 41/60. Fixed (exit reopened, PA countering the
rumor, one warden) 55/60. t90 fell from 480 s to 131.5 s. Cost $0.39. The optimizer did
not choose the assisted plan, so the 3 mobility-impaired occupants still waited at refuges.

Source: out/loop/demo_tepper/report.md (our run).

## 10. Who gets left behind: people who cannot use stairs

What it says: at the World Trade Center, 6 percent of survivors reported a mobility
impairment that slowed them, and slow occupants slowed others on the stairs.

How we use it: wheelchair users cannot take stairs alone. Across our buildings they are
the people left inside.

Our number: Tepper weekday fire, rule occupants, seed 42: 169/173 out, 172/173 with an
assisted-evacuation plan. Mudge House night fire: 80/82; the 2 left are mobility-impaired
residents at refuges. Tepper LLM drill: 3 still waiting after the fixes.

Source: NIST NCSTAR 1-7, Averill et al. 2005.

## 11. Getting buildings in

### iPhone LiDAR scan

What it says: a scan is a cloud of triangles with no idea of rooms or doors.

How we use it: we align the scan to its main wall directions (Manhattan frame), build a
walkable grid, detect doors and rooms, find exit signs and remove scanned people with
OWLv2 plus LiDAR depth.

Our number: 30 synthetic scans: walkable IoU 0.958 / 0.914, door recall 0.996, precision
0.982, median wall error 6 mm. Real 14 x 14 m hall: 19 people removed, 4 exit signs found.

Source: research/GENERALIZATION.md, research/CV_MAPPING.md (OWLv2 base ensemble,
Apache-2.0).

### Floor-plan PDFs

What it says: CAD-exported PDFs keep their layers (rooms, doors, stairs) and print each
room's area.

How we use it: we read the layers and set the scale per floor from the room-area labels.

Our number: Tepper: 5 floors, 633 rooms, 22 exits, 12 stair links, validator 0 errors.
Mudge House: 4 floors, 391 rooms, 17 exits.

Source: ingest_plans/README.md; counts from samples/cmu/*/scene.json.

## 12. Speed and latency

What it says: a sim with many LLM occupants is only as fast as its slowest call.

How we use it: calls run in parallel, the next decision is prefetched near doors, and the
clock never waits: after a 2 s deadline the rule fallback acts. Batching 20 agents in one
call measured about 2x slower than 20 parallel calls.

Our number: 40-person hall drill, real prompts, single runs:

| Model | p50 | p90 | Rejected (rule fallback) | Cost |
|---|---|---|---|---|
| Gemini 2.5 Flash-Lite | 0.85 s | 1.33 s | 6% | $0.026 |
| Llama 3.1 8B, fastest provider | 0.88 s | 1.12 s | 25% (skips citing evidence) | $0.016 |
| gpt-oss-20b | 1.30 s | 2.04 s | 8% | $0.010 |
| gpt-oss-120b | 1.81 s | 3.21 s | 2% | $0.046 |

48 simultaneous calls hit no rate limit (Flash-Lite p50 0.71 s, gpt-oss-120b 0.80 s).
Jev Router (TypeSafe's System One router on OpenRouter): 1.45 s p50 on a short prompt.
Takeaway: prompt size, not model size, is the bottleneck.

Source: docs/LIVE_MODE.md, research/LLM_LATENCY.md, today's measurements.

## 13. Competitive landscape

What it says: professional egress tools (Pathfinder, MassMotion, buildingEXODUS, Simulex,
AnyLogic) move agents well and some publish validation, but take hours to days of CAD
work, cost about $2,500 a year (Pathfinder), and output positions and times, not reasons.

How we use it: we do not claim better physics. We add capture in minutes, occupants whose
reasons you can question, and an adversary that finds failures.

Source: research/COMPETITIVE.md.

## 14. Extra: a fruit-fly brain as an occupant

What it says: the adult fruit-fly connectome can be simulated as a spiking network that
reproduces fly behaviour from its wiring.

How we use it: one occupant's choices come from that wired brain, with no goals or
language. A control fly has the same brain with scrambled wiring.

Our number: real-wiring flies 24 of 25 out; scrambled-wiring flies 0 of 20 (never moved).

Source: Shiu et al. 2024, Nature 634:210; MaleCNS v1.0 connectome (Janelia/Google);
research/FLY_AGENT.md.

## Numbers we can say

| Number | Run and conditions |
|---|---|
| Pre-movement median 63.8 s vs target about 60 s | LLM occupants, cue-based calibration (README Results) |
| 7/7 vs 0/7 | Interview ablation re-runs: fire percept removed vs stated belief removed |
| Persona explains about 3x more than model | 304 calls, 8 models x 4 personas x 3 scenarios, 3 samples, temperature 1 |
| 33/36 vs 34/36 out | Model casting vs one model, hall, 36 LLM occupants, seed 42, single run |
| 55/60, 41/60, 55/60; t90 480 s to 131.5 s; $0.39 | Tepper adversary vs optimizer, 60 LLM occupants, seed 42, single run |
| 169/173, 172/173 with plan | Tepper weekday fire, rule occupants, seed 42 |
| 80/82 | Mudge House night fire, rule occupants, seed 42 |
| 34/36, 15/36, 33/36; $0.086 | Hall adversary vs optimizer, 36 LLM occupants, gemini-2.5-flash-lite, seed 42 |
| IoU 0.958 / 0.914, door recall 0.996, precision 0.982, wall error 6 mm | 30 synthetic LiDAR scans |
| 19 people removed, 4 exit signs | Real 14 x 14 m hall scan, iPhone Mesh mode |
| 633 rooms, 22 exits, 12 stair links, 0 errors | Tepper, 5 floors, from floor-plan PDFs |
| 391 rooms, 17 exits | Mudge House, 4 floors, from floor-plan PDFs |
| p50 0.85 s, $0.026 | Flash-Lite, 40-person hall drill, single run |
| 24/25 vs 0/20 | Fruit fly, real vs scrambled wiring |

## Claims not to make

- "First" anything.
- "Validated", "certified" or "compliant".
- "Panic".
- Fire or smoke physics: the smoke clock is one equation, not a fire model.
- Scale beyond what we ran.
- Averages from single runs: say "single run, seed 42" where true.
- Not validated against real evacuations.
- A planning tool, not certification.
