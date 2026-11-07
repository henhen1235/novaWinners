# Scenarios and model casting

Scope: (1) how different emergency types change protocol and real behavior,
and what the sim needs for each; (2) a scenario.json schema and a tool-calling
chatbot that builds it from plain language against scene.json; (3) whether
casting different LLMs as different occupants adds realistic diversity, with a
small measured experiment. Researched 2026-11-07 in about 35 minutes. Builds on
research/EVAC_PSYCHOLOGY.md (fire behavior, PADM, personas) and
research/LLM_LATENCY.md (model speed, cost).

Tags: [V] checked today against the source or a search summary quoting it.
[U] from memory or common official guidance not re-checked today; check
before it goes on a slide. [H] our own heuristic for the sim, not from a
source.

## 1. Emergency types

### 1.1 Summary

| Type | Official protocol | What people actually do | Trigger cues | Event location changes routes by | Key difference vs fire |
|---|---|---|---|---|---|
| Fire (flaming) | Leave by nearest safe exit, close doors behind you, no elevators, stay low in smoke, test doors before opening, if trapped seal the door and signal (NFPA/fire service) [U] | Delay minutes, finish tasks, use familiar exit (about 2/3 at the Station fire), turn back in smoke [V, EVAC_PSYCHOLOGY.md] | Alarm, smell, visible smoke, others moving, staff | Fire room and its exits unusable; smoke spreads along the door graph, stairs contaminated if doors held open | Baseline |
| Smoke only / alarm only | Treat every alarm as real; same as fire [U] | Alarm alone is a weak cue: normalcy bias, information seeking, longer pre-movement (WTC median 3 to 4 min) [V, EVAC_PSYCHOLOGY.md] | Alarm only, or smell only | Alarm only: no blocked areas. Smoke only: same spread as fire, no heat | No physical threat to route around; delay is the whole story |
| Active intruder / shooter | Run, Hide, Fight (DHS, FBI) [U for booklet text]; also Avoid, Deny, Defend (ALERRT) [U]. Run if there is a safe path, leave belongings, hands visible to police; hide out of view, lock and barricade, silence phone; fight only as last resort | Incidents are short: 69 percent of FBI 2000-2013 incidents with known duration ended in 5 min or less (44 of 63), 23 in 2 min or less [V] (https://www.fbi.gov/file-repository/as-study-quick-reference-guide-updated1.pdf). VR study (n=162 office workers and teachers): countermeasures, role and building change run/hide/fight choice and response time [V, Sci Rep 2022 via DOAJ https://doaj.org/article/6cf10ca474874268b82ae886ee3007d5]. Columbine library agent model (Purdue): more hiding, more casualties in that layout; running usually best [V summary] (https://www.purdue.edu/newsroom/releases/2019/Q2/lessons-from-columbine-new-technology-provides-insight-into-run,-hide,-fight-during-active-shooter-situations.html). Gunshots often first read as fireworks or construction [U] | Bangs, screams, people running, PA "lockdown", phone alert | The threat moves. Exits whose path passes the intruder are worse than none; exits on the far side become attractive; rooms with lockable doors become refuges | Hiding is a correct protective action, not a delay. Sound, not smoke, is the cue field. Information is often wrong (multiple-shooter rumors) [U] |
| Earthquake | Drop, Cover, Hold On; do not run outside; doorways are not safer; stay put until shaking stops; then leave slowly watching for falling debris, expect aftershocks [V] (https://shakeout.org/dropcoverholdon) | Christchurch Hospital CCTV, 22 Feb 2011: 1 of 68 codable people did full DCH, about a quarter took some protective action [V summary] (https://ir.canterbury.ac.nz/handle/10092/9077, Lambie et al.). Freezing was the most common response (34 percent) in Christchurch and Tohoku footage [V summary] (https://link.springer.com/doi/10.1007/s11069-015-1967-4) | Shaking itself, everywhere at once | During shaking almost no movement. After: jammed doors, debris in corridors, damaged stairs, facade side of building hazardous | In-place first, evacuate second; simultaneous onset (no pre-movement spread from cue arrival); route damage is random, not spreading |
| Gas leak (natural gas, indoors) | Leave immediately, do not operate switches, phones or flames inside, call the utility or 911 from outside [U, utility guidance] | People investigate, discount odor, odor fatigue [U] | Rotten-egg smell (mercaptan), hissing | Avoid the source room; natural gas is lighter than air and rises, propane is heavier and pools low [U] | Low perceived urgency, often no alarm; ignition is the hazard, so "leave fast and quietly" |
| Hazmat release (outside) | Shelter in place: go inside, close windows and doors, shut off HVAC, pick an interior room; for heavy gases like chlorine go to an upper floor [U, CDC/Ready.gov] | Graniteville 2005 chlorine spill: reverse 911 shelter-in-place message to about 3,600 homes; in a survey of 574 residents most left, 52 percent sought shelter with relatives [V summary] (https://hazards.colorado.edu/uploads/basicpage/qr178.pdf). Shelter-in-place is hard to sell; people want to leave [U as a general claim] | Smell, sirens, phone alert, PA | Exits on the plume side are the dangerous ones; going outside is worse than staying | The correct action is NOT to evacuate; success = staying in, closing up |
| Flood | Move to higher floors, not a closed attic; never walk or drive through moving water (6 inches of moving water can knock an adult down) [U, NWS "Turn Around Don't Drown"] | Underestimate depth and speed of water; most deaths are in vehicles [U] | Water entering, alerts | Lowest level and ground exits fail first; stairs up become the route | Vertical evacuation; exits can become the hazard |
| Bomb threat / suspicious package | Facility decides evacuate vs shelter vs search; do not touch the item; keep radios and phones away from the item; evacuate along routes that avoid it to an assembly point outside the standoff distance [U, CISA bomb threat guidance; standoff values not verified here] | Most threats are hoaxes; evacuations are slow and low-urgency, staff-led [U] | Phone call, note, PA instruction, a found item | No-go radius around the item; assembly point moves | Announcement-driven start, no physical cue for most occupants |
| Power outage in the dark | Emergency lighting must run 1.5 h at about 1 foot-candle average (NFPA 101) [U]; elevators stop | Not an evacuation by itself; people wait, use phone lights, follow voices and walls [U] | Lights out, PA if powered | Signs visible only if illuminated or photoluminescent; elevators unavailable | Speed and sign visibility drop for every route; more following [H] |

### 1.2 Simulation rules per type

| Type | Event parameters (scenario.json `params`) | Cue model | Movement/route rules |
|---|---|---|---|
| fire | `growth`: slow, medium, fast, ultrafast (t-squared alpha 0.003, 0.012, 0.047, 0.188 kW/s2) [U, standard design values]; `detect_s` (alarm delay, default 60); `smoke_door_s` (time for smoke to pass an open door, default 45) [H]; `closed_door_factor` 10 [H] | Smell when smoke is 1 room away, visible smoke in the room, alarm at `detect_s` | Fire room impassable; smoky rooms cost x3 and cut speed to 0.9 m/s [V Fridolf, via EVAC doc]; turn-back allowed |
| smoke | as fire without heat | as fire | as fire, fire room passable but smoky |
| alarm_only | `levels` | alarm everywhere at t_start | no hazard; delay from persona distribution |
| active_intruder | `entry` (exit/door/room), `behavior`: waypoints, seek_nearest_group, wander; `waypoints` (room ids); `speed_m_s` 1.4 [H]; `audible`: level, building, or radius_m; `resolve_s` (police end; sample so 69 percent end by 300 s [V anchor]) | Gunshot cue to all rooms on the same level, adjacent levels through stairwells [H]; "saw intruder" if line of sight | Edges within N m or line of sight of the intruder cost x20 [H]; lockable rooms become HIDE targets; FIGHT only if intruder enters the agent's room |
| earthquake | `duration_s` 15 to 45; `intensity` MMI 6 to 9; `door_jam_p` 0.05 to 0.3 by MMI [H]; `debris_rooms` optional; `aftershocks_s` list | Shaking everywhere at t_start | Speed 0 during shaking for DCH/freeze, 0.3 m/s stumbling for movers [H]; after shaking, a 30 to 120 s check/help phase [H], jammed doors removed from the graph |
| gas_leak | `gas`: natural_gas, propane; `rate`: low, med, high | Smell in the source room, spreads 1 room per 60 to 120 s [H] | Source room avoided; no elevator; no alarm unless someone pulls it |
| hazmat_external | `side`: exit id or compass side; `heavier_than_air` bool; `protocol`: shelter_in_place | PA or phone alert at t_start; smell near plume-side exits | Correct action = go to an interior room; plume-side exits cost x20 |
| flood | `start_level`; `rise_m_per_min`; `max_depth_m` | Water visible in rooms on the start level | Wading speed falls with depth, room impassable above about 0.5 m [U]; route goal becomes the highest level, not an exit |
| bomb_threat | `kind`: phoned, package; `item` location; `standoff_m`; `decision`: evacuate, shelter, search | PA/staff instruction at t_start; only staff know the item location | No-go disc of `standoff_m`; assembly point outside it |
| power_outage | `levels`; `emergency_lighting` bool | Lights out | Speed x0.6 [H]; exit signs visible only with emergency lighting; elevators removed |

Events stack: "earthquake then gas leak" or "fire alarm pulled during an
intruder event" are common real combinations [U for frequency], so the
scenario file is a list of timed events, not one type.

Contract gaps found (for a future docs/team/CONTRACT_CHANGE_REQUEST.md, not urgent):
Door has no `lockable` flag (needed for HIDE), Room.type has no kitchen,
meeting, or retail values (fine, the chatbot matches `name`), and Meta has no
compass bearing, so "east exit" needs a convention (Section 2.3).

## 2. Scenario chatbot

### 2.1 scenario.json sketch

```json
{
  "schema_version": "0.1",
  "scene": {"name": "nova_office", "sha256": "<scene.json hash>"},
  "seed": 42,
  "duration_s": 900,
  "compass": {"east": "+x", "north": "-z", "source": "convention"},
  "events": [
    {"id": "e1", "type": "fire", "t_start_s": 0,
     "at": {"room": "r_07"},
     "params": {"growth": "fast", "detect_s": 60, "smoke_door_s": 45}},
    {"id": "e2", "type": "active_intruder", "t_start_s": 60,
     "at": {"exit": "ex_east"},
     "params": {"behavior": "seek_nearest_group", "speed_m_s": 1.4,
                "audible": "level", "resolve_s": 300}}
  ],
  "announcements": [
    {"t_s": 90, "levels": ["L2"], "source": "pa", "text": "Lockdown. Lock doors, stay out of sight."}
  ],
  "occupants": [
    {"where": {"level": "L2"}, "count": 20,
     "personas": {"regular_employee": 0.6, "visitor": 0.3, "warden": 0.1},
     "models": {"google/gemini-2.5-flash-lite": 0.7, "anthropic/claude-haiku-5.5": 0.3},
     "familiar_exit": "ex_main"}
  ],
  "assumptions": ["'east exit' resolved to ex_east (largest x among exits)"]
}
```

Field rules:
- `at` is exactly one of `{"room": id}`, `{"exit": id}`, `{"door": id}`,
  `{"level": id, "point": [x, z]}` (a clicked point).
- `where` is one of `{"room": id}`, `{"level": id}`, `{"all": true}`;
  `count` is spread over rooms by area.
- Adversaries are events (`active_intruder`), not occupants. Their motion is
  scripted in code (Section 3.5), never an LLM.
- `assumptions` lists every guess the chatbot made, shown to the user before
  the run.

### 2.2 Validation against scene.json (code, not the LLM)

1. `scene.sha256` matches the loaded scene.
2. Every room, exit, door and level id exists; a `point` lies inside
   `walkable[level]`.
3. `t_start_s` and `t_s` in [0, duration_s]; event ids unique.
4. Event `type` in the enum; `params` validated per type (Pydantic
   discriminated union on `type`, like contract/models.py).
5. Occupants: count > 0; total per room at most 2 per m2 of `area_m2`
   (hard error) [H]; persona and model weights each sum to 1; models in an
   allow-list (the ones we have tested).
6. Event placed in an `outside` or `stairwell` room: allowed only for types
   where it makes sense (intruder entry yes, fire in stairwell warn).
7. Errors return as structured strings (`"occupants[0].where.level: unknown
   level 'L3'; known: L1, L2"`) so the LLM can fix or ask.

### 2.3 Name resolution

Done in code by a `resolve_location` tool, so the LLM never invents ids:
exact id, then case-insensitive name, then token match on name
("kitchen" matches "Staff Kitchen"), then room type ("the stairwell"),
filtered by level when given. "Floor 2" maps to levels sorted by
`elevation_m` (ground = floor 1, US convention, state it in
`assumptions`), or to a level whose `name` contains "2". Compass words use
the convention +x = east, -z = north (top-down view in the viewer) until the
scene has a real bearing; the chatbot must echo that assumption. Exits also
match on `label`. Zero matches or more than one match means the LLM must call
`ask_user` with the candidates; it may not guess.

### 2.4 Tools (OpenAI-compatible, works on OpenRouter)

Three tools. The full scenario is resubmitted each time; it is small, and
patch formats add bugs.

```json
[
  {"type": "function", "function": {
    "name": "resolve_location",
    "description": "Look up rooms, exits, doors or levels in the loaded scene by a user's words. Always call this before using an id.",
    "parameters": {"type": "object", "additionalProperties": false,
      "required": ["text"],
      "properties": {
        "text": {"type": "string", "description": "User words, e.g. 'kitchen', 'east exit', 'floor 2'"},
        "kind": {"type": "string", "enum": ["room", "exit", "door", "level", "any"]},
        "level": {"type": "string", "description": "Level id to filter by, if known"}}}}},
  {"type": "function", "function": {
    "name": "ask_user",
    "description": "Ask one clarifying question when a location is unknown or ambiguous, or a required value is missing. Offer the candidates.",
    "parameters": {"type": "object", "additionalProperties": false,
      "required": ["question"],
      "properties": {
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}, "maxItems": 6}}}}},
  {"type": "function", "function": {
    "name": "submit_scenario",
    "description": "Submit the complete scenario. Returns 'ok' or a list of validation errors to fix.",
    "parameters": {"type": "object", "additionalProperties": false,
      "required": ["events", "occupants", "duration_s", "assumptions"],
      "properties": {
        "duration_s": {"type": "integer", "minimum": 60, "maximum": 3600},
        "events": {"type": "array", "items": {"type": "object", "additionalProperties": false,
          "required": ["id", "type", "t_start_s", "at"],
          "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "enum": ["fire", "smoke", "alarm_only", "active_intruder", "earthquake",
                     "gas_leak", "hazmat_external", "flood", "bomb_threat", "power_outage"]},
            "t_start_s": {"type": "number", "minimum": 0},
            "at": {"type": "object", "description": "Exactly one of room, exit, door, or level+point",
              "properties": {"room": {"type": "string"}, "exit": {"type": "string"}, "door": {"type": "string"},
                             "level": {"type": "string"},
                             "point": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}}},
            "params": {"type": "object", "description": "Type-specific; omit to use defaults"}}}},
        "announcements": {"type": "array", "items": {"type": "object",
          "required": ["t_s", "text"],
          "properties": {"t_s": {"type": "number"}, "levels": {"type": "array", "items": {"type": "string"}},
                         "source": {"type": "string", "enum": ["pa", "staff", "phone_alert"]},
                         "text": {"type": "string", "maxLength": 160}}}},
        "occupants": {"type": "array", "items": {"type": "object", "additionalProperties": false,
          "required": ["where", "count"],
          "properties": {
            "where": {"type": "object", "properties": {"room": {"type": "string"}, "level": {"type": "string"}, "all": {"type": "boolean"}}},
            "count": {"type": "integer", "minimum": 1, "maximum": 2000},
            "personas": {"type": "object", "additionalProperties": {"type": "number"}},
            "models": {"type": "object", "additionalProperties": {"type": "number"}},
            "familiar_exit": {"type": "string"}}}},
        "assumptions": {"type": "array", "items": {"type": "string"}}}}}}
]
```

### 2.5 System prompt and example

```
SYSTEM: You turn a user's description of an emergency drill into a scenario
for the Rehearsal evacuation simulator. The building is fixed (below).
Rules:
- Use only ids returned by resolve_location. Never invent ids.
- If a place is unknown or matches more than one thing, call ask_user with
  the candidates. Do not guess.
- Times are seconds from scenario start. "at 1 minute" = 60.
- Unstated values: use defaults (omit params) and write each guess into
  assumptions.
- Personas available: warden, regular_employee, visitor, committed_task,
  parent_with_child, older_adult, mobility_impaired, skeptic. If the user
  gives no mix, use 0.6 regular_employee, 0.25 visitor, 0.1 warden,
  0.05 mobility_impaired.
- Call submit_scenario once everything is resolved; if it returns errors,
  fix them or ask the user.
BUILDING: {levels: L1 "Ground" elev 0, L2 "Floor 2" elev 3.5;
 rooms: r_07 "Staff Kitchen" L1, r_12 "Open Office" L2, ...;
 exits: ex_main "Main entrance" L1 at [2.1, 0.0], ex_east "East door" L1 at [31.0, 8.2]}
 (generated from scene.json; for big buildings send levels + exits only
  and let resolve_location do rooms)

USER: fire in the kitchen, active intruder entering at the east exit at
t=60 s, 20 people on floor 2
```

Expected tool sequence: `resolve_location("kitchen", "room")` gives r_07
(one match); `resolve_location("east exit", "exit")` gives ex_east;
`resolve_location("floor 2", "level")` gives L2; then `submit_scenario`
with the JSON in 2.1 and assumptions such as "fire growth default fast",
"persona mix default". If there were two kitchens, the next call is
`ask_user("Which kitchen?", ["Staff Kitchen (Ground)", "Kitchenette (Floor 2)"])`.

Model for the chatbot: any tool-capable model works; this is one call per
user turn, so latency does not matter. Use a stronger model than the
occupant model (for example claude-haiku-5.5 or gemini-3.1-flash-lite with
reasoning on). Not tested today.

## 3. Model casting

### 3.1 Cheap candidates on OpenRouter (live /api/v1/models, 2026-11-07)

460 models listed. Picks for many-agent use, all with tool and structured
output support except where noted. Latency is from our experiment below
(16 concurrent calls, temperature 1, about 250 token prompts, 80 max output),
so it is a rough single run, not a benchmark.

| Model | $/M in / out | Reasoning control | p50 / max latency (s) | Notes |
|---|---|---|---|---|
| google/gemini-2.5-flash-lite | 0.10 / 0.40 | `enabled: false` | 0.60 / 0.78 | Default per LLM_LATENCY.md; fastest here |
| openai/gpt-oss-120b | 0.026 / 0.095 | mandatory, `effort: low` | 0.84 / 3.19 | Cheapest; needs max_tokens 400 for hidden reasoning |
| anthropic/claude-haiku-5.5 | 0.10 / 0.50 | `effort: none` | 0.80 / 1.04 | Most consistent per cell |
| deepseek/deepseek-v4-flash | 0.091 / 0.182 | `effort: none` | 0.95 / 11.5 | One 11.5 s tail call |
| meta-llama/llama-4-scout | 0.10 / 0.30 | none | 1.35 / 1.88 | No structured outputs flag |
| mistralai/mistral-small-3.2-24b-instruct | 0.075 / 0.20 | none | 0.78 / 1.22 | Sometimes wraps JSON in code fences |
| qwen/qwen3.5-flash-02-23 | 0.065 / 0.26 | `enabled: false` | 1.06 / 1.46 | |
| x-ai/grok-4.20 | 1.25 / 2.50 | off by default | 0.77 / 2.34 | 10x the price of the rest; cheapest Grok listed |
| openai/gpt-5-nano | 0.05 / 0.40 | mandatory, min `minimal` | about 1.05 (LLM_LATENCY.md) | Not in experiment |
| openai/gpt-6-luna | 0.10 / 0.50 | `effort: none` allowed | not measured | Newest OpenAI small model |
| z-ai/glm-4.7-flash | 0.061 / 0.40 | toggle | not measured | |

### 3.2 What the literature says about model-family tendencies

| Dimension | Finding | Source | Tag |
|---|---|---|---|
| Big Five | Personality measurements of LLMs are reliable and valid only for larger, instruction-tuned models and specific prompt setups; traits can be steered to target levels by prompting | Serapio-Garcia et al. 2023, arXiv 2307.00184 | [V summary] |
| Big Five | Scores depend on format: forced-choice separates models more than Likert and is less temperature sensitive; GPT-3.5 Likert scores look like an artifact. Models: Llama-3.1-8B, GLM-4, GPT-3.5, GPT-4o, Claude-3.5-Sonnet, DeepSeek-V3 | Li et al., Findings of ACL 2025 (2025.findings-acl.480) | [V summary] |
| Risk | No universal profile: two GPT models more risk-averse than human benchmarks, Gemini 2.0 Flash more risk-seeking | arXiv 2506.23107 (cross-cultural lottery study) | [V summary] |
| Risk | Cambridge Gambling Task: Claude bet high (over 60 percent), GPT moderate (under 50 percent) | Jia et al., arXiv 2406.05972 | [V summary] |
| Risk | Risk attitude is trivially set by fine-tuning, and models can report it | Betley et al., arXiv 2501.11120 | [V summary] |
| Cooperation | Repeated 2x2 games: LLMs do well in self-interested games; GPT-4 unforgiving in iterated PD (defects forever after one defection); poor at coordination (Battle of the Sexes); "social chain-of-thought" prompting changes it | Akata et al., Nature Human Behaviour 2025, doi 10.1038/s41562-025-02172-y | [V summary] |
| Cooperation | Llama2 and GPT-3.5 more cooperative and forgiving than humans; Llama3 uncooperative and exploitative unless the partner always cooperates. Same family, opposite sign across versions | Fontana, Pierri, Aiello, ICWSM 2025, arXiv 2406.13605 | [V summary] |
| Strategy | GTBench, 10 games: open models trailed GPT-4 in complex games until Llama-3-70B; CoT does not always help | Duan et al., NeurIPS 2024, arXiv 2402.12348 | [V summary] |
| Sycophancy | All five assistants tested (Claude 1.3/2, GPT-3.5/4, Llama-2-70B) shift toward the user's stated view; traced to human preference data | Sharma et al. 2023, arXiv 2310.13548 | [V summary] |
| Conformity | LLM agents conform to a majority in multi-agent settings; rate rises with majority size and interaction time; "enhanced persona" and reflection reduce it | Weng, Chen, Wang, ICLR 2025, arXiv 2501.13381 (BenchForm) | [V summary, no per-model rates read] |
| Refusal | Over-refusal and safety correlate (Spearman 0.89 across 32 models); Claude models most over-refusing, Mistral most permissive | Cui et al., OR-Bench, arXiv 2405.20947 | [V summary; ranking via a third-party review] |
| Persona effect | Persona variables explain under 10 percent of annotation variance in subjective NLP data; persona prompting gives modest but significant gains | Hu and Collier, ACL 2024 | [V] |
| Persona fidelity | LLM stand-ins for groups flatten and misportray identity groups | Wang, Morgenstern, Dickerson, Nature Machine Intelligence 2025 | [U] |
| Evac-specific | PEBA/PEvo: 80 LLM agents in an active-shooter school; persona rewriting toward a target distribution cut KL from 4.61 to 0.20 | arXiv 2509.16457 | [V, EVAC_PSYCHOLOGY.md] |

Reading: the documented tendencies are real but unstable. They flip between
versions of one family (Llama2 vs Llama3), between task framings (lottery vs
gambling task), and between measurement formats (Likert vs forced choice).
None of these papers measured emergency behavior, and every finding is about
models that are now 1 to 3 generations old. Casting "Claude = cautious, Grok =
bold" would rest on evidence that does not transfer.

### 3.3 Experiment (run today, $0.021 total)

Design: 8 models x 4 personas (none, anxious first-time visitor, veteran
warden, deadline-focused skeptic) x 3 scenarios x 3 samples, temperature 1,
JSON action from a fixed option list. 304 calls including the adversary test.
Script and raw output: /private/tmp/claude-501/-Users-karthikvattem-projects-nova/552f7d88-55d2-4092-aa65-2bf9d2393f83/scratchpad/exp/
(calib.py, results*.jsonl; scratchpad, copy to tools/ if we keep it).

Scenarios (all floor 2, office 2.12): intruder (bangs and "he has a gun" from
the lobby, coworkers running to the east stair, main stair leads into the
lobby, lockable room 3 m away); fire_ambiguous (alarm 60 s, faint smell,
"probably a drill", nobody moving); cornered (intruder has broken into the
room you hid in, 2 m away, chair and extinguisher in reach).

How much of the action choice each factor explains (information gain in bits,
minus the mean under 300 label shuffles to remove small-sample bias):

| Scenario | H(action) | Persona | Model | Left within a model x persona cell |
|---|---|---|---|---|
| intruder | 2.44 | 0.55 | 0.20 | 0.54 |
| fire_ambiguous | 2.48 | 1.04 | 0.32 | 0.34 |
| cornered | 2.25 | 0.56 | 0.17 | 0.51 |

Persona explains about 3x what model choice explains, in every scenario.

Modal action per model (3 samples each):

| Model | intruder: none / anxious / warden / skeptic | fire: none / anxious / warden / skeptic |
|---|---|---|
| claude-haiku-5.5 | RUN_EAST / FOLLOW / HIDE_LOCKED / HIDE_LOCKED | EVAC_NEAREST / FOLLOW / EVAC_NEAREST / SEEK_INFO |
| deepseek-v4-flash | RUN_EAST / FOLLOW / mixed (HELP, 911, FIGHT) / HIDE_LOCKED | mixed / WAIT / EVAC_NEAREST / SEEK_INFO |
| gemini-2.5-flash-lite | FOLLOW / FOLLOW / mixed / FOLLOW | EVAC_NEAREST / WAIT / EVAC_NEAREST / CONTINUE |
| llama-4-scout | RUN_EAST / HIDE_LOCKED / RUN_EAST / HIDE_LOCKED | SEEK_INFO / SEEK_INFO / EVAC_NEAREST / CONTINUE |
| mistral-small-3.2 | mixed / FOLLOW / HIDE_LOCKED / HIDE_LOCKED | WAIT / WAIT / EVAC_NEAREST / CONTINUE |
| gpt-oss-120b | FOLLOW / SEEK_INFO / CALL_911 / CONTINUE_TASK | EVAC_NEAREST / SEEK_INFO / EVAC_NEAREST / CONTINUE |
| qwen3.5-flash | HIDE_LOCKED / FOLLOW / HELP / mixed | SEEK_INFO / SEEK_INFO / HELP / CONTINUE |
| grok-4.20 | RUN_EAST / FOLLOW / RUN_EAST / HIDE_LOCKED | SEEK_INFO / FOLLOW / HELP / CONTINUE |

Findings:
- Refusals: 0 of 304. All 8 models answered the cornered scenario (fight,
  tackle, freeze) and, framed as an abstract training sim, all 16 adversary
  calls picked a room to move the intruder to. One Mistral reason read
  "Find and eliminate hiding occupants": fine for a log, bad on a demo
  screen. Refusal risk on the occupant side looks low with this framing; the
  OR-Bench Claude over-refusal finding did not show up for Haiku 5.5 here.
- Persona effects replicate across models: anxious visitor gives
  FOLLOW_OTHERS (intruder, 6 of 8 models modal) and FREEZE (cornered, 22 of
  24 calls); skeptic gives CONTINUE_TASK under an ambiguous alarm (6 of 8).
  The anxious FREEZE is a stereotype applied too uniformly (real
  earthquake footage shows freezing as the top response at 34 percent, not
  over 90 percent).
- Model effects show where the persona does not pin the action. The warden
  in the intruder case: Claude and Mistral hide, Llama and Grok run, Qwen and
  DeepSeek help others, gpt-oss calls 911. With no persona under an
  ambiguous alarm: Gemini, gpt-oss and Claude evacuate at once (the
  too-rational occupant EVAC_PSYCHOLOGY.md warns about), Llama, Qwen and Grok
  seek information, Mistral waits.
- Failure: gpt-oss-120b skeptic picked CONTINUE_TASK in 2 of 3 intruder
  calls despite gunshots and a scream. Persona text can override the cue;
  the persona must say what overrides skepticism ("two people say go, or
  you see or hear the threat").
- Within-cell spread at temperature 1 is 0.3 to 0.5 bits; Claude Haiku was
  the most repeatable (3 of 3 same action in most cells).

Limits: 3 samples per cell, one prompt wording, option lists given to the
model (open-ended answers might differ), no ground-truth human
distribution for these exact cues.

### 3.4 Recommendation

Persona prompts plus code-sampled traits are the primary driver; model mix is
secondary variance, not a casting device.

1. Persona first. Each occupant gets a persona from the EVAC_PSYCHOLOGY.md
   archetypes, with traits sampled in code (pre-movement delay, known exits,
   trusted sources, speed) and short persona text that also states what
   overrides it. This is where the measurable, controllable 3x effect is.
2. One default model for all routine decisions (gemini-2.5-flash-lite, per
   LLM_LATENCY.md). Replay and debugging stay simple.
3. Optional model mix of 2 to 3 models (for example flash-lite 60 percent,
   claude-haiku-5.5 25 percent, llama-4-scout or qwen3.5-flash 15 percent)
   assigned at random per occupant, independent of persona. Its job is to
   break single-model monoculture (identical answers to identical cues),
   not to represent a human type. Do not label occupants "the Claude
   person".
4. Calibrate to distributions, not single answers: fix target action shares
   per persona and cue (from the research, or expert guesses like PEBA), then
   adjust persona text until each model in the mix lands inside the band
   (PEvo-style rewriting).
5. Adversary is scripted code (waypoints or "seek nearest group" on the
   navmesh), never an LLM: deterministic, replayable, and no generated text
   about targeting people on screen. It did not refuse today, but there is
   nothing to gain from letting it choose.

Why not model casting as the main lever: our data says model explains about a
third of what persona does; the literature shows family tendencies flip with
versions and framing; and mixing models multiplies latency tails (DeepSeek
had an 11.5 s call), output-format quirks (Mistral code fences, gpt-oss
hidden reasoning) and cost (Grok 10x). Honest pitch line: "we use several
models so no single model's quirks decide the outcome", not "each model is a
personality".

### 3.5 15-minute calibration experiment (repeatable)

Run before each demo build or model change. About 450 calls, under $0.05.

- Models: the 3 to 5 in the planned mix. Personas: 3 (warden, regular
  employee, skeptic) plus none. Scenarios: fire_ambiguous, intruder,
  earthquake_after_shaking. 10 samples per cell at the production
  temperature, production prompt and action enum.
- Measure per cell: action distribution, invalid/refusal rate, p50 and max
  latency. Per scenario: persona and model information gain with the
  shuffle correction (code in calib.py), and Jensen-Shannon distance
  between models for the same persona.
- Pass: invalid plus refusal under 2 percent; persona gain at least 2x
  model gain; no persona with immediate evacuation above 50 percent under
  alarm-only cues (too-rational check); warden never CONTINUE_TASK;
  skeptic leaves when the cue includes seeing the threat; per-model JSD
  for the same persona under 0.5 (else that model needs its own persona
  wording or leaves the mix).
