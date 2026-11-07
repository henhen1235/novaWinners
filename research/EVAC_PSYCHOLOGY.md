# Evacuation psychology for Rehearsal occupants

Scope: what real evacuation research says about how people behave in building
fires, which numbers a sim can use, how to validate, and how to turn it into
LLM persona and decision prompts. Researched 2026-11-07 in about 20 minutes.

Tags used below:
- [V] checked today against the linked source (abstract, report text or a
  reliable secondary citing it).
- [U] from memory or only a secondary snippet; unverified, check before
  putting the number on a slide.

## 1. Core findings

Protective Action Decision Model (PADM). Lindell and Perry, "The Protective
Action Decision Model: Theoretical Modifications and Additional Evidence",
Risk Analysis 32(4):616-632, 2012, doi 10.1111/j.1539-6924.2011.01647.x [V]
(https://pubmed.ncbi.nlm.nih.gov/21689129/). People act on environmental cues,
social cues and warnings, filtered through threat perception, protective
action perception and stakeholder (who to trust) perception. The decision
steps usually written as: (1) is there a real threat, (2) do I need to act,
(3) what can I do, (4) what is best, (5) do I need to act now [U on exact
wording; this is the commonly taught version, e.g. the NWS training PDF
https://training.weather.gov/wdtd/courses/woc/human-factors/crisis-comms-sm/risk-assess/story_content/external_files/Protective%20Action%20Decision%20Model.pdf].
When any step is uncertain, people seek information instead of moving. This
is the design hook: the LLM should only move when the persona has passed all
steps.

Pre-movement delay is often the largest part of evacuation time, and it is
driven by cues and information, not by walking.
- WTC 2001 (NIST NCSTAR 1-7, Averill et al. 2005) [V]
  (https://nvlpubs.nist.gov/nistpubs/Legacy/NCSTAR/ncstar1-7.pdf): reported
  evacuation delay was mean 5.61 min, median 3 min, mode 1 min in WTC 1
  (range 1-80 min) and mean 6.04 min, median 4 min in WTC 2 (range 1-75).
  Median time to initiate in WTC 1 was 3 min below floor 77 and 5 min near
  the impact zone. 30 percent of survivors reported helping others and 20
  percent being helped before they started. 6 percent reported a mobility
  impairment that slowed them. Two thirds had done at least one drill in the
  prior year.
- Kuligowski and Mileti 2009, Fire Safety Journal [V, abstract]
  (https://doi.org/10.1016/j.firesaf.2008.10.001): longer pre-evacuation time
  with more cues, more information gathered and sought, more pre-evacuation
  actions, and lower starting floor. Community-disaster (PADM-style) theory
  holds for building fires.
- Kuligowski and Hoskins, NIST TN 1664 [V, summary]
  (https://www.nist.gov/document/tn1664pdf): in a high-rise office fire,
  waiting, helping and preparation actions drove longer delay; information
  seeking alone did not, overall.
- 1993 WTC bombing (alarm and PA dead): start times ranged 0 to over 4 h in
  Tower 1, means 11 min (T1) and 25 min (T2) [V, via BRANZ SR143, Whiting
  2005, https://d39d3mj7qio96p.cloudfront.net/media/documents/SR143_Review_of_international_research_efforts_-_Occupant_pre-movement-respons.pdf].
- Unannounced university drill: pre-movement 10 to 200 s; 54 percent did two
  actions before leaving, 28 percent one or none, 18 percent three or more
  (Gwynne et al. 2003, via SR143) [V].
- Retail with trained staff: staff switched to evacuation mode in about 30 s
  and 500 customers were out in 2 min 45 s; about 70 percent of shoppers
  reported no commitment to their activity. IKEA drills: most customers
  responded within 30 s, the slowest were people in the checkout queue and in
  the restaurant (Shields 1998, Frantzich 2001, via SR143) [V].
- High-rise residential (voice message ordered evacuation, Proulx 1998, via
  SR143) [V]: 83 percent tried to leave, 82 percent of those got dressed or
  collected belongings first, only 54 percent of those who tried got out
  (rest turned back into apartments because of smoke).

The panic myth. Disaster sociology (Quarantelli and others) found "mass
panic" is rare; people are mostly orderly and helpful [U for Quarantelli
specifics]. Drury, Cocking and Reicher's social identity model of collective
resilience: shared fate creates a shared identity, which increases helping
and coordination. London 7/7 bombings study (90 survivor accounts plus 56
witnesses) found selfish behavior rare and mutual help common [V, summary]
(https://www.sussex.ac.uk/broadcast/read/588). Disaster myths paper listing
panic, civil disorder and helplessness as myths [V]
(https://www.csap.cam.ac.uk/media/uploads/files/1/10.1111-jasp.12176-disaster-myths-as-published-early-view.pdf).
Sim implication: do not add a "panic" state. Delay, wrong exit choice and
crowding at familiar exits are what kill time.

Affiliation and familiarity. Sime 1985, "Movement toward the familiar",
Environment and Behavior 17:697-724 [V, abstract]
(https://www.scilit.net/publications/9cc5853ed484fe8b67f75c3699ddaa56):
Summerland fire data; exit proximity mattered, but exit choice was mediated by
person and place affiliation (people go to the people they came with and to
the way they came in). Station nightclub fire 2003: about two thirds of
occupants tried the main entrance (the way they came in), a crush there began
before 90 s and nearly stopped flow (NIST NCSTAR 2) [V]
(https://nvlpubs.nist.gov/nistpubs/Legacy/NCSTAR/ncstar2v1.pdf). Exits near
the stage and bar were under-used [U on exact split].

Herding and social influence. Kinateder et al. VR tunnel fire (n=40): people
followed a virtual agent's route [V]
(https://pred.uni-regensburg.de/id/eprint/9581/). Comunale, Kinateder and
Warren (APS 2017): participants preferred the familiar exit, more so when
virtual neighbors went there; neighbors going to the unfamiliar exit cancelled
the familiarity effect [V]
(https://www.psychologicalscience.org/conventions/archive/2017annual/paper/4005/).
Helbing, Farkas and Vicsek 2000, Nature 407:487-490: social force model with a
herding parameter; reproduces faster-is-slower clogging at bottlenecks and
shows a mix of individual and herding behavior is optimal in smoke [V]
(https://arxiv.org/abs/cond-mat/0009448). A 2024 retrospective says the
empirical evidence for faster-is-slower in real crowds is mixed [V]
(https://collective-dynamics.eu/index.php/cod/article/view/A168).

Role and commitment. Staff and wardens start earlier and steer others;
visitors wait for cues; people finish committed tasks (queue, meal, purchase,
dressing). Supported by the retail and IKEA data above and the office vs
apartment drill comparison in SR143 (offices with wardens fast, apartments
slow and full of pre-movement tasks) [V].

Normalcy bias. People first interpret ambiguous cues as normal (alarm test,
"probably an electrical smell"). SR143 cites cases where a smell was
attributed to an electrical fault and called in to an electrician, about 25
min before action [V]. Generic term "normalcy bias" is common in the
literature [U for a specific citation].

Signage. VR and drill studies find many people do not notice exit signs and
follow people instead; a 2024 VR study fragment reports about 30 percent of
layout-familiar participants noticed the sign [U, not confirmed which study].

Smoke. Jin 1978 (J Fire Flamm 9:135-157): walking speed falls as smoke
density rises, faster drop in irritant smoke; people walk along walls, arms
out [V, via secondary https://arxiv.org/html/2404.11439v1]. Fridolf et al.
2013, Fire Safety Journal 59: rail tunnel with extinction coefficient about
2.2 1/m, average speed about 0.9 m/s [V, abstract]
(https://lup.lub.lu.se/search/publication/84f69efb-9a14-4144-8c22-32a115ccf77a).
Turning back in smoke is common in residential fires (Proulx 1998 above, 46
percent of those who tried did not get out) [V]. Bryan's finding that about
a quarter of people moved through smoke, more at shorter visibility [U].

Disability and age. WTC: 6 percent reported a mobility impairment that slowed
them; slow occupants slowed others in the stair [V, NCSTAR 1-7]. Boyce,
Shields and Silcock 1999, Fire Technology 35:51-67, measured horizontal and
ramp speeds for 155 people with and without locomotion disability [V for
existence, doi 10.1023/A:1015339216366]; their speeds are [U]: roughly 0.8
m/s unassisted with disability, 0.57 m/s with a stick, 0.69 m/s electric
wheelchair, 1.25 m/s without disability.

Groups. Families and groups move together and wait for their slowest member;
WTC reported median group size 7 for those in groups [V, NCSTAR 1-7 text,
context not fully read]. Parents re-entering for children is why IKEA
announced that the play area was already evacuated [V, SR143].

## 2. Parameters for the sim

| Parameter | Value | Source |
|---|---|---|
| Free walking speed, adult | mean 1.34 m/s, sd 0.26, min 0.5 | Weidmann 1993 via Vadere [V secondary] https://gitlab.lrz.de/vadere/vadere/-/issues/85 |
| Speed-density | Kladek form: v = v0 (1 - exp(-1.913 (1/rho - 1/5.4))), v0 1.34, rho_max 5.4 p/m2 | Weidmann via https://arxiv.org/pdf/1107.5246 [V params, formula U] |
| Max flow through door | about 1.3 p/s/m effective width at about 1.9 p/m2 | SFPE hydraulic model [U value; Pathfinder confirms 1.88 p/m2 max-flow density https://www.thunderheadeng.com/docs/2025-1/pathfinder/appendices/technical-reference/sfpe] |
| SFPE speed-density | S = k - 0.266 k D, k = 1.40 corridor, 1.00 to 1.23 stairs by riser/tread | SFPE Handbook [U] |
| Stair descent, office drills | mean 0.44 m/s, sd 0.19; local 0.10 to 1.7 | NIST TN 1839 [V] https://nvlpubs.nist.gov/nistpubs/TechnicalNotes/NIST.TN.1839.pdf |
| Stair descent, real fire incl. stops | median 1.2 floors/min, about 0.2 m/s along slope | NCSTAR 1-7 [V] |
| Stair ascent | about 0.5 to 0.7 of descent speed | [U] |
| Speed in smoke | 0.9 m/s at Ks 2.2 1/m (tunnel); drop with irritant smoke | Fridolf 2013 [V]; Jin 1978 [V qualitative] |
| Mobility impaired | 0.57 (stick) to 0.8 m/s level; wheelchair about 0.69 | Boyce 1999 [U numbers] |
| Pre-movement, office w/ wardens, unannounced drill | most under 1 to 2 min | SR143 [V qualitative] |
| Pre-movement, university drill | 10 to 200 s | Gwynne 2003 via SR143 [V] |
| Pre-movement, retail w/ trained staff | most under 30 to 60 s, queue/restaurant longest | SR143 [V] |
| Pre-movement, real high-rise office fire | median 3 to 4 min, mode 1 min, long tail to 80 min | NCSTAR 1-7 [V] |
| Pre-movement, residential, night | many over 10 min; tasks: dress, valuables, kids, pets | SR143 [V] |
| Distribution shape | lognormal or loglogistic fits most occupancies | Forssberg et al. 2019 Fire Technology [V summary]; Lovreglio et al. 2019 FSJ 105 (112 cases, 13,591 people, 16 countries) [V] https://doi.org/10.1016/j.firesaf.2018.12.009 |
| Use of familiar (entry) exit | about 2/3 at the Station fire | NCSTAR 2 [V] |
| Helping before leaving | 30 percent helped someone, 20 percent were helped | NCSTAR 1-7 [V] |

Suggested lognormal starting points (our calibration, not from a standard;
fit to the anchors above): office drill with wardens median 45 s, sigma 0.6;
retail with staff median 30 s, sigma 0.7; real office fire, ambiguous cues
median 180 s, sigma 0.9; residential awake median 120 s, sigma 1.0;
residential asleep median 300 s, sigma 1.0. ISO/TR 16738 and PD 7974-6:2019
give official design values by behavioural scenario; we could not access
them today [U], so do not quote them.

## 3. Validation

How the field does it. IMO MSC.1/Circ.1533 (2016) Annex 3 has the most-used
component tests (speed in a corridor, exit flow, counterflow, pre-movement
distribution, exit allocation) [V]
(https://www.deutsche-flagge.de/de/redaktion/dokumente/interpretationen/solas/ii-2/msc-1-circ-1533-revised-guidelines-on-evacuation-analysis-for-new-and-existing-passenger-ships.pdf).
NIST TN 1822 (Ronchi, Kuligowski, Reneke, Peacock, Nilsson 2013) adapts them
to buildings and adds emergent-behavior tests and data sets [V summary]
(https://portal.research.lu.se/en/publications/assessing-the-verification-and-validation-of-building-fire-evacua/).
RiMEA (German guideline) has a similar test list [V]. Pathfinder publishes its
results on the IMO and NIST cases each release [V]
(https://www.thunderheadeng.com/docs/2025-1/pathfinder/appendices/verification-validation/imo).
Data sets: WTC (NCSTAR 1-7), Station fire (NCSTAR 2), NIST high-rise drill
data (TN 1839), Lovreglio pre-evacuation database.

Three tests a hackathon team can run in an afternoon:
1. Pre-movement distribution test. Office floor, 50 agents, alarm only vs
   alarm plus warden. Plot time to first movement. Pass if alarm-only is
   right-skewed with median in minutes and alarm plus warden shifts the
   median under 1 min (directionally matches SR143 and NCSTAR 1-7). Also
   report: fraction who did a pre-movement task.
2. Familiar exit imbalance test (Station fire analogue). Room with a main
   entrance (where all agents entered) and two unused side exits of equal
   width. Pass if the main entrance takes well over half of agents (target
   near 2/3) while a "shortest path" baseline splits evenly. Then add a
   warden at a side exit and show the imbalance drops (Kinateder social
   influence).
3. Herding under poor signage. Two-exit room, one exit signed and hidden.
   Seed 3 agents who know the hidden exit. Compare exit split with and
   without visible signage. Pass if poor signage increases following and
   variance between runs. Faster-is-slower is a fourth option, but it is a
   physics (navmesh/contact) effect, not an LLM effect, and the evidence for
   it in real crowds is mixed, so do not lead with it.

Also run IMO test 1 style sanity checks (one agent walks 40 m at its speed;
door flow under 1.3 p/s/m) so judges see the movement layer is not wrong.

## 4. LLM agent specific

- PEBA / PEvo (EMNLP 2025, arXiv 2509.16457) [V]
  (https://arxiv.org/html/2509.16457v1): 80 LLM agents, active shooter
  school. Agents almost always hid if a spot was near or ran if an exit was
  visible; rarely froze. Explicit "behave like X" instructions were often
  ignored when they conflicted with context. Iteratively rewriting personas
  toward a target behavior distribution cut KL divergence from 4.61 to 0.20.
  Lesson: measure fidelity as a distribution, and steer with personas, not
  orders. Their target distribution is expert elicited, not observed.
- Argyle et al. 2023, "Out of One, Many", Political Analysis [V]
  (https://par.nsf.gov/servlets/purl/10444291): conditioned GPT-3 reproduces
  subgroup survey patterns ("algorithmic fidelity").
- Aher, Arriaga and Kalai, ICML 2023 [V]
  (https://proceedings.mlr.press/v202/aher23a.html): 3 of 4 classic studies
  replicated; "hyper-accuracy distortion" in newer models, i.e. too correct.
- Distributional fidelity fails even when subgroup means look right (2026
  preprint on 2,414 WVS respondents) [V summary]
  (https://www.alphaxiv.org/abs/2607.18310); LLM-generated persona text adds
  bias (arXiv 2503.16527) [V] (https://arxiv.org/html/2503.16527v1); LLM
  social sims need explicit validity boundaries (arXiv 2506.19806) [V]
  (https://arxiv.org/pdf/2506.19806).
- Cooperation bias is contested: hyper-cooperative in iterated prisoner's
  dilemma in some studies, greedy in others [V summary, same search].
- Mendoza et al., arXiv 2606.14989: persona-conditioned LLM agents with a
  cognitive hierarchy for urban evacuation, calibrated on empirical data [V]
  (https://arxiv.org/pdf/2606.14989).

Known failure for us: an LLM told "there is a fire alarm" will evacuate
immediately by the nearest exit. That is exactly the optimistic, too-rational
occupant the research says does not exist. Countermeasures: (a) do not tell
the agent the ground truth, only its cues; (b) sample pre-movement delay from
the persona distribution in code and only ask the LLM what to do during and
after it; (c) offer only the exits the persona knows; (d) calibrate the
output distribution, not individual answers.

## 5. Design

### Persona archetypes

| Archetype | Pre-movement (lognormal median, sigma) | Knows exits | Affiliation | Speed m/s | Smoke tolerance | Trusts |
|---|---|---|---|---|---|---|
| Staff / fire warden | 20 s, 0.5 | all | sweeps area, directs others | 1.3 | medium, will turn back above Ks 1 | alarm, PA, own training |
| Regular employee | 60 s, 0.7 (drill) / 180 s, 0.9 (ambiguous) | entry + 1 | coworkers nearby | 1.3 | medium | warden, coworkers, then alarm |
| First-time visitor | 90 s, 0.8 | entry only | own companions; follows crowd | 1.3 | low | staff, crowd |
| Committed task (queue, meal, meeting host) | 120 s, 0.8 | entry only | stays with task until a person asks | 1.2 | low | staff in person |
| Parent with child | 90 s, 0.8 plus search for child | entry + nearest | moves toward child first, never leaves without | 0.9 (carrying) | low, turns back | own eyes, partner |
| Older adult | 120 s, 0.9 | entry | spouse or companion | 0.8; stairs 0.3 | low | staff, PA |
| Mobility impaired | 120 s, 0.9 | entry, elevator | waits for helper or refuge | 0.6 level; stairs needs help | low | staff, phone |
| Skeptic (normalcy bias) | 300 s, 1.0 | entry + 1 | none | 1.3 | high (walks through smoke) | own cues only; ignores alarm until smoke or two people say go |

All rows are our synthesis of Section 1 sources, not published archetypes.
Speeds are rough means; sample individual values with sd about 0.2.

### Decision-point prompt

```
SYSTEM (cached): You are simulating one building occupant in an emergency.
Research says people rarely panic; they delay, seek information, finish
tasks, go to familiar people and exits, and help others. Answer as THIS
person, not as a safety expert. You only know what is in YOUR CUES.

PERSONA: {archetype}, {age}, {role}, with {companions}. Knows exits:
{known_exits}. Trusts: {trusted_sources}. Current activity: {task}.
YOUR CUES (t={t}s): {cues}   e.g. "alarm sounding 40s; coworker said
'probably a drill'; faint smell; nobody near you is moving"
NEARBY: {visible_people_and_what_they_do}

Think in order, one short line each:
1 THREAT: do I believe something is really wrong? (yes/unsure/no)
2 RISK TO ME: does it affect me here? (yes/unsure/no)
3 OPTIONS: which of my known options are possible?
4 BEST: which fits me, my companions, my role?
5 NOW: must I act now, or can I finish/check first?

Output JSON only:
{"padm": [t, r, "now"|"later"], "action": one of
 ["CONTINUE_TASK","SEEK_INFO","GATHER","GO_TO_PERSON","HELP","EVACUATE_KNOWN_EXIT",
  "FOLLOW_OTHERS","TURN_BACK"], "target": id|null, "say": "<=10 words"}
```

Notes: the delay sampled in code is a floor, not the whole answer; the LLM
can extend it with SEEK_INFO or CONTINUE_TASK. "say" feeds other agents' cues,
which is how warden instructions and "probably a drill" spread.

### Metrics to report

- RSET breakdown: detection/alarm time, pre-movement (per persona), travel,
  queueing at doors. Show pre-movement share of total.
- Time to first movement: distribution per persona vs the anchor ranges.
- Exit utilization imbalance: share per exit, plus max share / ideal share.
- Helping events: count of HELP and GO_TO_PERSON actions; compare to
  NCSTAR's 30 percent helped-others anchor.
- Turn-backs in smoke, agents never evacuated, peak density at each door
  (flag above 4 p/m2).

## 6. Pitch framing

Paragraph: Most egress tools move dots at a set speed and assume people leave
the moment the alarm sounds. Decades of evacuation research say otherwise. At
the World Trade Center, the median survivor waited 3 to 4 minutes before
starting down, and 30 percent stopped to help someone. At the Station
nightclub, about two thirds of people tried to leave by the door they came
in. Rehearsal's occupants are LLM agents whose personas and decision steps
come from that research (the Protective Action Decision Model, affiliation,
social influence), so the sim shows the delays and bottlenecks a floor plan
alone hides. It is a rehearsal and a design-conversation tool, not a
certified engineering analysis: we check it reproduces known patterns, not
that it predicts your building to the second.

Slide bullets:
- People do not panic, they delay: occupants follow the PADM steps (is it
  real, does it affect me, what now) and their pre-movement times match
  published drill and WTC ranges.
- Familiar beats nearest: agents head for the way they came in and follow
  others, reproducing the Station-fire exit imbalance a shortest-path model
  misses.
- Honest about limits: validated on three known phenomena, movement layer
  checked against IMO and NIST style tests; not a substitute for a
  Pathfinder study or a fire engineer.
