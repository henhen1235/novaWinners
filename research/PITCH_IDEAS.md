# Rehearsal: pitch ideas

Scope: ideation for the hackathon pitch. Product: scan a building with an
iPhone LiDAR (or upload a 2D blueprint), get a traversable scene, run an
evacuation where each occupant is an LLM agent that decides only at decision
points (alarm, fork, blocked path, smoke, someone needs help) while a navmesh
moves it between decisions on a lockstep clock. An adversary agent injects
failures, an optimizer agent proposes changes and re-runs. Output: metrics and
a 3D replay.

## What already exists (and what is new)

Commercial and research egress simulators:

- Pathfinder (Thunderhead Engineering): agent-based egress with steering and
  SFPE flow modes, pre-movement time distributions, group and social-influence
  behaviors. Ships a public verification and validation report, updated each
  release, covering the NIST evacuation test cases. This is the tool fire
  engineers already trust.
- MassMotion (Oasys/Arup): large crowd and pedestrian flow in stations,
  stadiums and airports. Strong on throughput and congestion, behavior is
  rule and cost-field based.
- buildingEXODUS (University of Greenwich FSEG): rule-based occupant
  behavior, includes toxicity and smoke effects on occupants.
- Open source: JuPedSim, Vadere (social force, optimal steps, cellular
  automata). FDS+Evac existed but is no longer maintained.
- All of these take a CAD/BIM model or a hand-drawn geometry. Building the
  model is the slow, expensive part. Behavior is parameters (speed, pre-movement
  time, exit preference weights), not reasoning about what an occupant hears.

LLM agents in evacuation (2025-2026 research):

- Ntarouis and Geraerts, "Generative agents in crowd simulation" (Utrecht,
  2026): perception, memory, reflection and planning modules with LLM agents
  for crisis scenarios.
- Yang, Zhang and Gu (EGU 2026 / RESS 2026): LLMs as the decision component in
  agent-based evacuation, pedestrians and vehicles, reported as more flexible
  than rule-based agents on a real disaster case.
- Mendoza et al. (arXiv 2606.14989, Berkeley): persona-conditioned LLM agents
  with a cognitive hierarchy for urban evacuation on a grid.
- PEBA (EMNLP 2025): language-agent crowds for an active-shooter school
  scenario, fidelity measured as a distribution of behaviors.
- arXiv 2509.21868: iterative design study of a 13,000-agent LLM simulation of
  a campus gathering with emergency-preparedness practitioners.

Scanning and drill apps:

- Apple RoomPlan: on-device LiDAR room capture with walls, doors, openings,
  windows and furniture as parametric objects. Many apps use it for floor plans.
- "Evacuation Plan App" (App Store): LiDAR floor plan plus manually marked
  exits and extinguishers. No simulation.
- Fire-drill software is mostly scheduling, roll call and compliance logging.

What we can honestly say is new: LLM evacuation agents are not new, and we
should not claim "first". The new part is the combination, in one loop that
runs in minutes on a phone scan:

1. Phone scan or blueprint to a validated, sim-ready navmesh with no CAD step.
2. LLM reasoning only at discrete decision points on top of classic movement,
   so physics stays checkable and cost stays small.
3. A visible adversary vs optimizer loop that finds and fixes failures.
4. Occupants you can interview afterwards, grounded in their decision log.

## Ideas (14)

Effort is team-hours on top of the base product. Payoff is what the judges see.

1. Interview the occupant
   - Click an agent in the replay and ask "why did you go left at 0:42?". The
     answer is generated from that agent's stored decision log (what it saw,
     its options, what it chose and its stated reason), with the tick cited.
   - Judges care: it makes the agentic part visible and inspectable, which no
     classic tool can do.
   - Effort 3-4 h. Payoff very high. Risk low: the log already exists if WS-C
     stores each decision; hallucination is contained by answering only from
     the log.

2. Classic sim vs LLM sim, side by side
   - Same building, same seed, same clock. Left: shortest path to nearest exit
     (the default most tools approximate). Right: LLM occupants. Divergences
     are highlighted: going back the way they came, waiting for a friend,
     following a rumor, ignoring a sign.
   - Judges care: it answers "why LLMs" in one picture instead of an argument.
   - Effort 3 h (the baseline is a one-screen agent on the same navmesh).
     Payoff high. Risk medium: if the scenario has no ambiguity the two panes
     look the same, so the demo scenario must include a rumor or blocked exit.

3. Accessibility personas and an equity metric
   - Wheelchair user (no stairs, needs clear width, slower), low vision (reads
     signs only up close, follows others), deaf or hard of hearing (misses an
     audio-only alarm, reacts to others or strobes), older adult, parent with a
     child. Report evacuation time per group, not only the median.
   - Judges care: shows who the building fails, which averages hide. Concrete
     and humane.
   - Effort 3 h. Payoff high. Risk medium: wording. Use movement values from
     the SFPE Handbook ranges, cite them, and describe personas by need, not
     by stereotype.

4. Rumor contagion and herding from speech
   - The adversary tells one agent "the east exit is blocked". Agents can say
     one short sentence to agents within earshot at decision points. The replay
     shows the rumor spreading as a color ripple, and the crowd piling on one
     exit.
   - Judges care: emergent multi-agent behavior that social-force models do
     not produce without hand-written rules.
   - Effort 3 h. Payoff high. Risk medium: needs an earshot radius and a cap
     on messages per tick to bound cost.

5. Adversary vs optimizer as a visible debate
   - A transcript panel: adversary proposes the worst failure given current
     metrics ("block door D3, the busiest exit"), optimizer proposes a change
     ("add a sign at the corridor fork pointing to D5; assign Room 2 to D5"),
     re-run, scoreboard of time to clear and stragglers per round. Three rounds.
   - Judges care: multi-agent AI with a measurable outcome, not chat theater.
   - Effort 4 h if both agents exist. Payoff high. Risk medium: token cost and
     optimizer proposing impossible changes; restrict to a menu of actions
     (sign, door width, exit assignment, staff warden position).

6. Live scan of the judging room
   - Scan the room during or just before the pitch, run the drill with the
     judges' seats as occupants.
   - Judges care: the most memorable possible demo, and it proves no CAD step.
   - Effort 2 h of rehearsal if WS-B M4 works; 0 h extra code. Payoff very
     high. Risk high: a live scan can fail or take too long. Mitigation: scan
     the same room in the morning and keep that run as the fallback.

7. Audience personas from a 30-second survey
   - QR code before the pitch: "Do you follow the crowd or the sign?", "First
     time in this building?", "Would you go back for your laptop?". Each answer
     becomes an occupant persona with the person's first name or a nickname.
   - Judges care: the audience sees itself in the sim.
   - Effort 3 h (form, mapping answers to persona text). Payoff high. Risk
     medium: few responses or privacy concerns; seed with defaults, no
     identifying data beyond a chosen nickname.

8. Photo of the posted evacuation map
   - Most buildings post an evacuation plan near elevators and stairs. Snap a
     photo and WS-A turns it into a scene, with the posted exits pre-tagged.
   - Judges care: "works in any building, today, without a scan".
   - Effort 4-6 h on top of the blueprint path. Payoff medium-high. Risk
     medium-high: posted maps are stylized and not to scale; needs a scale
     prompt (one known door width).

9. Advisory fire-code checks
   - Deterministic checks on the scene, not LLM: number of exits vs occupant
     load, door clear width, egress width capacity per occupant, travel
     distance along the navmesh, exit separation. Each line cites the code
     section and edition and says "advisory".
   - Judges care: a business angle (facility managers, insurers) with rules a
     judge can verify.
   - Effort 4 h. Payoff medium-high. Risk medium: wrong citations damage
     credibility; ship 3-5 checks and have someone read each cited section.

10. Decision-time breakdown (RSET with a "why" column)
    - Per agent: time spent noticing the alarm, deciding, walking, queuing.
      Pre-movement time emerges from decisions instead of being sampled from a
      distribution. Show it as stacked bars next to the Required Safe Egress
      Time figure fire engineers use.
    - Judges care: speaks the domain's language (RSET/ASET) and shows the LLM
      changes the number that matters most.
    - Effort 2 h. Payoff medium. Risk low.

11. Record-and-replay with a cost meter
    - Every LLM response is stored by hash of (model, prompt). A replay is
      exact and costs nothing; the UI shows "this run: 214 calls, $X, Y s".
    - Judges care: answers determinism and cost questions on screen before
      they are asked.
    - Effort 2 h. Payoff medium. Risk low.

12. Drill plan handout
    - One page for the facility manager: overloaded exit, the fork where people
      hesitate, recommended sign positions on the floor plan, which groups were
      slowest, what changed after the optimizer's fixes.
    - Judges care: a concrete deliverable a customer would pay for.
    - Effort 2 h. Payoff medium. Risk low.

13. Join as an occupant from your phone
    - A judge controls one occupant: at each decision point their phone shows
      the same options the LLM agents get.
    - Judges care: interactive and fun; human vs LLM choices compared.
    - Effort 5-6 h. Payoff high. Risk high: networking and timing in the room.

14. AR replay in the scanned room
    - Play the replay in AR on the iPhone, agents walking through the real room.
    - Judges care: very memorable.
    - Effort 6-10 h (RealityKit, anchoring the scene frame to the room). Payoff
      very high. Risk high. Only if everything else is done.

## Top 3 to build

Chosen for payoff per hour and because each one shows the LLM layer doing
something a classic model cannot. Ideas 6, 10 and 11 are close to free once
these exist and should ride along (6 as the demo setting, 11 as the cost meter).

### 1. Interview the occupant

- WS-C: store a decision record per call: tick, agent id, observation text,
  options, choice, reason, position. Click handler in the replay opens a chat
  panel; the prompt contains only that agent's persona and records and must
  cite ticks. Cache answers.
- WS-B: room names and types (already planned via the LLM semantics step) and
  exit sign text from photos, so answers say "the corridor outside the
  kitchen" instead of "room_3".
- WS-A: room labels and door names read from the blueprint, same purpose.

### 2. Classic vs LLM side by side, with a rumor scenario

- WS-C: baseline agent that walks the shortest navmesh path to the nearest
  exit and reroutes only when the path is physically blocked; run it on the
  same clock and seed; split-screen replay; divergence markers where the two
  agents with the same id choose differently. Rumor injection from the
  adversary, earshot radius, one short message per agent per decision.
- WS-B: the demo building (the scanned judging room or our office floor) with
  at least two exits and one fork; navmesh and exits validated by the contract.
- WS-A: a second building from a blueprint with a less obvious layout, to show
  the effect is not one scene.

### 3. Accessibility personas and per-group metrics

- WS-C: persona fields: speed factor, can_use_stairs, min_clear_width_m,
  alarm_perception (audio, visual, both), sign_read_range_m. Navmesh query
  filters edges narrower than min_clear_width_m and stairs for wheelchair
  users. Report evacuation time per persona group and list anyone who could
  not reach an exit.
- WS-B: door `width_m`, obstacles that narrow passages, stairs, so the
  clear-width filter works on real scans.
- WS-A: door widths, stairs, ramps and elevators from the blueprint legend.

## Positioning statement

Rehearsal turns a two-minute iPhone scan of any building into an evacuation
drill run by LLM occupants with their own personalities, so a facility
manager can see where people hesitate, follow a rumor or get stuck, and test a
fix, before a real alarm.

## Pitch narrative (3 minutes)

- 0:00-0:20 Hook. "Fire drills test whether people know the drill. They don't
  test what happens when the main exit is blocked and someone shouts that the
  other one is too." One sentence on why: real evacuations go wrong on
  decisions (people go back the way they came in, wait for others, believe
  what they hear), not on walking speed.
- 0:20-0:45 Gap. Professional egress tools (Pathfinder, MassMotion, EXODUS) are
  validated and good at flow, but need a CAD model and treat behavior as
  parameters. Building the model costs days. Most buildings never get one.
- 0:45-1:45 Demo (script below): scan, scene, drill, adversary, interview.
- 1:45-2:15 How it works. Navmesh and lockstep clock do the physics; the LLM
  is called only at decision points, about 5 per occupant per run, so a
  40-person drill is roughly 200 calls. Every response is recorded, so replays
  are exact. Classic baseline runs alongside for comparison.
- 2:15-2:40 Results from our runs: time to clear before and after the
  optimizer's changes, slowest group before and after, number of agents
  misled by the rumor. Use real numbers from the morning runs, with the seed
  count.
- 2:40-3:00 Who pays and what it is not. Facility managers, schools, offices
  and insurers who want drill planning between expensive engineering studies.
  It is a rehearsal and planning tool; it does not certify a building. Close on
  the positioning statement.

## 60-second demo script

- 0-10 s: "This is this room, scanned this morning on an iPhone." Show the 3D
  model and the navmesh with exits tagged. (If live scan works, do it here.)
- 10-20 s: Press Run. 30 occupants, personas shown on hover: "Priya, first day,
  follows others", "Sam, wheelchair user", "Lee, fire warden".
- 20-30 s: Adversary blocks the nearest exit and plants "the back door is
  locked" with one agent. Rumor ripple spreads; crowd stalls at the fork.
  Split screen: classic agents on the left simply reroute.
- 30-40 s: Click one occupant. Ask "Why didn't you take the back door?"
  Answer: "At 0:38 Lee told me it was locked, and I saw three people turn
  around, so I followed them."
- 40-55 s: Optimizer proposes two changes (sign at the fork, warden at the
  back door). Re-run. Scoreboard: time to clear, slowest group, agents misled.
- 55-60 s: "Scan to answer: under five minutes. This run cost $X." Show the
  cost meter.

## Likely judge questions

Accuracy and validation: how do you know it is right?
- Movement is classic and checkable: navmesh paths, walking speeds and door
  flow limits from the SFPE Handbook (maximum specific flow about 1.3
  persons/s per m of effective width). We can run a few NIST egress
  verification cases (free walk in a corridor, flow through a door) on the
  movement layer and show the numbers. Decision behavior is not validated
  against real evacuations, and we say so. We report a range over several
  seeds and always show the classic baseline next to it.

Why LLMs instead of rule-based agents?
- Rules cover what the modeler anticipated. LLM occupants react to content: a
  rumor, a staff instruction that conflicts with a sign, a friend still in the
  room. Personas are plain text, so a facility manager can describe their
  occupants without tuning weights. And each choice comes with a reason you can
  read. We keep the LLM out of physics and use it only for discrete choices.

Cost and latency?
- About 5 calls per occupant per run with short prompts on a small model
  (Haiku class via OpenRouter). The clock pauses while a tick's decisions are
  in flight and calls run in parallel. The demo replay is precomputed, so
  playback is instant. The cost meter shows measured cost per run.

Determinism?
- LLM output is not deterministic even at temperature 0, so we record every
  response keyed by model and prompt hash and replay from that record; movement
  uses a seeded RNG. A replay is exact. For conclusions we run N seeds and
  report median and spread, the same way classic tools report stochastic runs.

Privacy of scans?
- RoomPlan runs on the device. Scans stay on our machine; nothing is uploaded
  to a third party except short text descriptions of rooms and sign photos the
  user chooses to include. The LLM never sees the mesh or coordinates. Vertex
  colors can be dropped to remove anything visual. Survey personas use a
  nickname and three answers, nothing else.

You are making safety-critical claims. How do you word them?
- We say "in our simulation", "candidate bottleneck" and "worth checking",
  never "safe", "compliant" or "certified". Code checks are labeled advisory
  with the code edition cited. Rehearsal is for drill planning and for deciding
  where to spend an engineering study, not a replacement for a fire protection
  engineer or the authority having jurisdiction.

What if the scan is wrong?
- The pipeline validates the scene (every room reaches an exit, door widths in
  range, navmesh matches walkable area) and shows warnings. A person confirms
  exits with a click-to-tag step. Our synthetic benchmark measures door recall
  and walkable overlap against ground truth.

Is this better than a real drill?
- It complements one. A real drill tests people once a year under no stress;
  Rehearsal tests the building against failures you cannot stage, such as a
  blocked exit or a rumor, and tests fixes before buying signs or moving doors.

## Sources

- Pathfinder verification and validation: https://files.thunderheadeng.com/support/documents/pathfinder-verification-validation-2024-2.pdf
- Ntarouis and Geraerts, Generative agents in crowd simulation: https://ojs.cvut.cz/ojs/index.php/APP/article/view/11557
- Yang, Zhang and Gu, EGU26-3725: https://meetingorganizer.copernicus.org/EGU26/EGU26-3725.html
- Mendoza et al., persona-conditioned evacuation agents: https://arxiv.org/pdf/2606.14989
- PEBA, EMNLP 2025: https://aclanthology.org/anthology-files/pdf/emnlp/2025.emnlp-main.1562.pdf
- LLM agent simulations for emergency preparedness policy: https://arxiv.org/abs/2509.21868
- Evacuation Plan App: https://apps.apple.com/us/app/evacuation-plan-app/id6689522614
- MassMotion, buildingEXODUS, JuPedSim, Vadere, FDS+Evac, SFPE Handbook flow
  values, NFPA 101 / IBC egress rules: from prior knowledge, not re-checked in
  this pass. Verify any number or code section before it goes on a slide.
