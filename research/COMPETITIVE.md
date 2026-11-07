# Competitive analysis: Rehearsal vs evacuation simulators and hackathon prior art

Scope: what exists, what we can honestly claim, what judges will expect that we
lack, and what to build in the last ~4 hours (13:00 to 17:00, 2026-11-07).
Builds on research/PITCH_IDEAS.md (prior-art pass, positioning line). Researched
in about 30 minutes.

Tags: [V] opened the source or a search summary quoting it today. [U] from
memory or a snippet only; do not put on a slide without checking.

## Headline

Nobody we found combines (a) phone scan or blueprint to a validated sim-ready
scene with no CAD step, (b) LLM occupants that decide only at decision points
on top of classic movement, and (c) an adversary and optimizer loop that
changes the building and re-runs. Each piece on its own exists somewhere,
including in hackathon repos pushed this week. The pitch is the loop and the
input time, not any single part.

Three findings that change the plan:
- `MJones235/monument-evac-sim` already does "classic vs LLM on the same
  movement engine" (JuPedSim) and fits both to real drill data (Proulx 1991,
  Monument Metro) [V]. Side-by-side alone is not novel; we use it as a demo
  device, not a claim.
- `TheMayukhG/Evacuation-Model-with-Misinformation-spread` (updated
  2026-11-06) is a school evacuation with SIR-style rumor contagion of "false
  claims of blocked exits", written to be "demonstrated and explained in 2
  minutes" to judges [V]. Likely a concurrent hackathon entry. Rumor contagion
  is not our novelty; the novelty is that our rumor is a sentence an agent
  reasons about and can later explain.
- Pathfinder imports OBJ and glTF/GLB and has a geometry-from-images/PDF
  workflow [V], so "only we can use a scan or a plan" is false. What is true:
  for non-IFC input Pathfinder treats everything as obstructions until a user
  marks doors and floors and adds missing exits by hand [V]. Our pipeline
  detects doors, rooms and exits and validates the result in about 2 s.

## Comparison matrix

Columns: input-to-sim time (from building to first run), geometry source,
behavior realism, emergency types, explainability (can you see why an
occupant did something), scenario authoring, cost, demo-ability in 3 min.

| Tool / project | Input-to-sim | Geometry source | Behavior | Emergencies | Explainability | Scenario authoring | Cost | Demo-ability |
|---|---|---|---|---|---|---|---|---|
| Pathfinder (Thunderhead) | Hours to days; IFC best, other formats need manual door/floor tagging [V] | IFC, DXF, DWG, FBX, DAE, OBJ, glTF, PyroSim/FDS, images/PDF [V] | Steering + SFPE modes, groups, assisted evacuation, reacts to doors/signs/alerts [V]; published V&V on IMO/NIST cases [V] | Fire (fire-condition exposure via PyroSim) [V], general egress | Positions, room clearing, door flow as CSV/JSON [V]; no reasons | GUI, per-occupant profiles and behaviors | ~$2,500/yr node-locked, $8,350 perpetual (site script, may be stale) [V]; 30-day trial [V] | High polish, but a recorded video of a CAD model |
| MassMotion (Oasys/Arup) | Hours; IFC import maps geometry, Revit links/portals auto-detected [V] | IFC/CAD [V] | Cost-field agents, strong congestion/throughput [U] | Egress, crowd flow; used for active-shooter research at USC [V] | Maps and charts, no reasons [U] | GUI, events and timetables [U] | Not public; "Flow" variant < 1/3 price (2015) [V] | Good visuals, CAD-bound |
| buildingEXODUS (Greenwich FSEG) | Hours; DXF, IFC, SMARTFIRE, FDS geometry [V] | CAD/BIM [V] | Rule-based expert system, toxicity/FED, heat [V]; 25,000-agent high-rise [V] | Fire with toxicity coupling [V] | Rule traces, no language reasons [U] | GUI | Commercial license [U]; newest release note found 2020 [V] | Low (Windows, dated) |
| Simulex (IES VE) | Hours; floor plans in IES VE [V] | CAD [V] | Particle model, queuing, overtaking [V] | Fire egress | Times and congestion | GUI | Part of IES VE license [U] | Low |
| AnyLogic Pedestrian Library | Hours; draw or import CAD [U] | CAD/manual | Social-force style, scriptable in Java [U] | Anything you code | What you code | Java + GUI | PLE free for learning, watermark, size limits [V]; Pro thousands/yr [U] | Medium |
| Legion / STEPS / EVACNET | Hours to days | CAD [U] | Legion: validated agent sim, can import FDS [V]; STEPS: grid [V]; EVACNET: network flow [V] | Fire egress, stations | Flow results only | GUI / tables | Commercial [U]; EVACNET legacy | Low |
| JuPedSim | Minutes for a coder; walkable polygons (WKT/shapely) in Python [V, third-party example] | Hand-made polygons | CFSM / GCFM microscopic models [V] | None built in | Trajectories | Python | Free, open source [V] | Low without a UI |
| Vadere | Minutes to hours, topography editor | Manual/JSON | OSM, gradient navigation, social force [V] | None built in | Trajectories | GUI/JSON | Free [V] | Low |
| Menge | Hours, XML scenes | Manual | Modular goal/plan/motion plug-ins [V] | None | Trajectories | XML | Free [V] | Low |
| FDS+Evac | Discontinued after FDS 6.7.7 [V] | FDS meshes | Coupled fire/evac | Fire | Low | Text input | Free, unmaintained | None |
| LLM evac papers: Mendoza et al. 2606.14989 [V], Calzolari et al. 2606.31038 [V], Li et al. 2509.21868 [V], Yang EGU26 [V], PEBA EMNLP 2025 [V] | Researcher-built | Grid city (Mendoza), Unity office (Calzolari), modeled stadium (Li) | Persona/OCEAN LLM agents; Li: GPT-4.1 decides, rule controller moves [V] | Fire/hazards, active shooter (PEBA), campus emergencies | Language reasons, not exposed as a product feature | Code | Research; Li ran 13,000 agents, no cost reported [V] | Papers, no live demo |
| monument-evac-sim (MJones235) | Researcher-built station | Hand-built JuPedSim geometry | Rule state machine vs LLM, both fit to Proulx drills [V] | Fire alarm drills | Manifest of LLM usage per run [V] | YAML | Open source, Azure LLM | Videos, CLI |
| AgentEvac (denoslab) | Road network | SUMO roads | LLM driver agents, record/replay of decisions [V] | Wildfire | Decision records [V] | Config | Open source | Low |
| OpenDisaster (Devpost) [V, mirror page] | Any city from OSM | OSM + elevation + satellite | 8 named LLM agents with memory, VLM perception, ElevenLabs voices | 4 disaster types, stochastic fire | Decision logs, replay analytics | Pick location | Hackathon | High, city scale, few agents |
| Fire CV (Devpost) [V, snippet] | Floor plans to Three.js 3D of a CMU building | 2D plans | AI-generated fire and smoke scenarios, path planning | Fire | Low | Preset | Hackathon | Medium-high |
| EvacMap, PathHero, ZPM (Devpost) [V, snippets] | Annotated plan images to GeoJSON/route | Plan images, OCR/OpenCV | Shortest route, no occupants | Fire | None | None | Hackathon | Medium |
| Misinformation evac (TheMayukhG) [V] | Bitmap floor plan, precomputed flow fields | Image | Social force + SIR rumor tiers, 360 students | Fire + rumor | Equations shown to judges | Keys | Hackathon-style | High, 2-min script |
| scan2net (ecalogero) [V] | Point cloud to walkable polygons to route network | PLY/XYZ scan | None (feeds OrientSim) | None | None | None | Open source | Low |
| mc2sim (Taichiops) [V] | Minecraft world to building model to evac sim, in browser | Minecraft voxels | Literature defaults | Egress | Low | None | Free web app | Fun, gimmick |
| Unity-FireEvacSim (kayo2e) [V] | Evacuation guide map image to grid | Image | PPO-controlled emergency lights | Fire | None | None | Open source | Medium |
| Evacuation Plan App (App Store) [V] | Minutes, LiDAR | iPhone LiDAR | No simulation; mark exits/extinguishers | None | None | Manual markers | Free + IAP | Static map |
| Polycam / Mappedin Scan / Matterport [V] | Minutes to a plan | LiDAR / photogrammetry | No simulation; Polycam has "Life Safety" tags, Mappedin safety markers | None | None | None | Subscription | Floor plans only |
| Drill compliance (SafetyCulture, FieldProxy, logs) [V] | n/a | None | Records real drills: alarm time, total time, roll call | Whatever was drilled | None | Checklists | Low SaaS | Boring |
| School safety maps (Raptor + Critical Response Group, DHS tabletop kits) [V] | Weeks, professional mapping | Survey/plans | None (maps for responders, human tabletop) | Active intruder, all-hazards | Human discussion | Facilitated | Grants/contracts | Low |
| Rehearsal (us) | ~2 min scan + ~2 s ingest + validator; blueprint PDF in seconds | RoomPlan, any mesh, vector PDF | Navmesh movement + LLM decisions at decision points, research-backed personas | Fire, smoke/alarm, active intruder (planned), placed on the map | Per-decision log, interview, cost meter (planned) | Plain-language chatbot to validated scenario.json (planned) | Cents per run (gemini-2.5-flash-lite ~$0.004 per 20-agent tick, LLM_LATENCY.md) | Live scan of the judging room |

## Differentiators that survive scrutiny

Each holds only if it is on screen at demo time. Order is strength of evidence.

1. Scan or blueprint to a validated, sim-ready scene in seconds, no CAD step.
   Evidence: our benchmark on 30 synthetic scans (door recall 0.979, precision
   0.961, walkable IoU 0.956, wall error 12 mm median, <= 2.3 s);
   1.67M-triangle mesh in 4.8 s. Pathfinder's non-IFC path needs manual
   door/floor typing and missing exits added by hand [V]. scan2net stops at a
   route network with user-linked exits [V]; Evacuation Plan App and Polycam
   stop at a map [V]. Say "minutes, not days", and show the validator OK.
2. The model proposes, code checks. LLMs decide only at discrete points
   (alarm, fork, blocked path, smoke, someone needs help); a navmesh and
   lockstep clock move people, so movement can be checked against SFPE door
   flow and corridor speed. Li et al. split it the same way (GPT-4.1 decides,
   rule controller moves) [V], so this is a credible design, not a first; our
   addition is that the same contract validator gates scenes, scenarios and
   optimizer fixes.
3. Adversary vs optimizer loop with measured before/after. Classic tools run
   sensitivity analysis over scenarios a human writes [V for Pathfinder
   sensitivity]. The LLM papers simulate; none we found proposes building or
   procedure changes and re-runs. Constrain the optimizer to a menu (sign,
   warden, exit assignment, unblock/widen door) so every fix is executable.
   Caveat: repo `vishesh25004370-KIET/SmartEvac` is titled "Adaptive
   Evacuation Simulation & Failure Discovery" with no README [V]; do not say
   "nobody does failure discovery".
4. Interview the occupant, answered only from its decision log with the tick
   cited. Pro tools output positions and timings, not reasons [V, Pathfinder
   CSV/JSON]. OpenDisaster and AgentEvac keep decision logs [V], but neither
   shows a grounded Q&A over them. This is the most visible "why LLMs" moment.
5. Plain-language scenarios on named spaces, multiple emergency types. "Fire
   in the kitchen, intruder at the east entrance" becomes a validated
   scenario.json against LLM-labeled room names (SCENARIOS_AND_MODELS.md).
   Pro tools are fire-egress first and GUI-authored; active-intruder planning
   today is human tabletop exercises and responder maps [V]. Only claim the
   types that run on demo day (fire and alarm-only for sure, intruder if done).

## Claims we must not make

- "First LLM evacuation simulator" or "first to model rumors" (PEBA, Mendoza,
  Calzolari, Li, monument-evac-sim, the misinformation repo).
- "First scan-to-simulation" (scan2net, Pathfinder OBJ/glTF import).
- "Classic tools can't model social behavior" (Pathfinder has groups,
  assisted evacuation, reactions to signs and alerts [V]). Say "they encode
  behavior as parameters you set in advance; ours reacts to what it hears".
- "Validated", "safe", "compliant", "certified". Movement checks yes;
  decision behavior is not validated against real evacuations, and we say so.
- "Realistic panic". The literature says mass panic is a myth
  (EVAC_PSYCHOLOGY.md); Calzolari et al. let agents "panic" [V]. We
  deliberately do not, and that is a credibility point.
- Scale claims beyond what we ran (Li ran 13,000 agents; EXODUS 25,000).
  Quote our own N and wall-clock.
- Smoke or toxicity physics. A Tanaka-Yamana smoke clock is a closed-form
  estimate, not CFD; buildingEXODUS couples real toxicity models [V].
- Code citations (NFPA 101, IBC) unless someone read the section today.

## Gaps judges will expect, effort to close today

| Gap | Who has it | Effort | Do today? |
|---|---|---|---|
| Movement sanity checks: one agent walks 40 m at set speed; door flow <= ~1.3 p/s/m (IMO MSC.1/Circ.1533 test 1 style) | Pathfinder publishes these [V] | 45 min (headless runs, one table) | Yes, one slide |
| Density / usage heatmap over the floor | Pathfinder, MassMotion [U for exact feature names] | 45 min (accumulate agent cells per tick, color the navmesh) | Yes, it reads instantly |
| Exit usage counts and door flow over time | Pathfinder room clearing and door flow text [V] | 30 min | Yes, part of the scoreboard |
| RSET-style breakdown: deciding vs walking vs queuing per agent | Domain standard (RSET/ASET) | 45 min if the decision log has timestamps | Yes if WS-C log exists |
| Multiple seeds with spread | All pro tools, monument-evac-sim [V] | 30 min with cached decisions; cost cents | 3 seeds, show median and range |
| One behavior check (familiar-exit imbalance or alarm-only vs warden pre-movement) | monument-evac-sim fits real drill data [V] | 1 h | Only if P0 and P1 are done |
| Stairs, multi-floor, elevators | All pro tools | Days | No. Say "single floor today" |
| Fire/smoke CFD, FED | EXODUS, Pathfinder+PyroSim | Days | No. Smoke clock only, labeled estimate |
| Scale (hundreds+) | All pro tools | Unknown | No. Show cost per agent and the math |

## Prioritized plan, 13:00 to 17:00

Freeze features at 16:00. 16:00 to 16:40: record the fallback video, rehearse
the 3-minute pitch twice. 16:40 to 17:00: submit. Order is judge impact per
hour. I could not see the WS-C code from this repo, so WS-C items assume a
working sim loop exists or is close; if it does not run end to end by 14:30,
everything below P0 is cut.

### P0, by 14:30 (no demo without these)
- WS-C: one scene end to end: scene.json -> 20-40 agents -> replay in the
  existing viewer (blueprint/viewer already draws paths and exits). One
  emergency placed in a room. Decisions recorded and replayable from cache.
- WS-B: a real scan, today. Best: the judging room or our table area via
  RoomScanner -> ingest_lidar -> validator OK -> viewer. This is
  differentiator 1 on screen. Fallback: tiny_office synthetic, said honestly.
- WS-A: freeze the blueprint path; produce the office_vector scene for WS-C
  as the second building (shows it is not one hand-tuned scene).

### P1, 14:30 to 15:30 (the "AI agents" score)
- WS-C: adversary blocks the busiest exit and plants one rumor sentence;
  classic shortest-path baseline on the same clock (cheap, same navmesh).
  Optimizer: one round, menu of 3 actions, re-run, scoreboard (time to clear,
  stragglers, agents misled). This is the core loop; protect it.
- WS-C: interview panel: click agent, answer from its log, cite the tick.
  Cache answers for the demo.
- WS-A + WS-B (shared, 1 h): lose-one-exit / single points of failure on the
  room-door graph (networkx articulation points, MAPPING_IDEAS.md #1). Output
  is the adversary's target list, so the adversary's first move is explained
  by geometry, not chosen at random. Visible as red doors.

### P2, 15:30 to 16:00 (the "completeness" score)
- WS-C: density heatmap + exit usage bars + cost meter ("N calls, $X, Y s").
- WS-C or whoever is free: the 2 movement checks as a one-slide table.
- WS-C: scenario chatbot, minimal: one sentence -> scenario.json -> validator
  -> run. Only if P1 works; otherwise show a prewritten scenario.json.

### Cut or reframe
- Cut: AR dollhouse, AR replay, multi-floor/stairs, join-from-phone,
  audience survey, photo of posted map, code red-pen (citation risk),
  earthquake/flood/gas types, visibility map, tap-to-tag.
- Reframe model casting: one toggle at most ("occupants on 2 models"), or a
  single line in "how it works". It is a research curiosity, not a judge win.
- Reframe smoke clock: only if WS-B is idle after SPOF; one number per room
  as a deadline label ("smoke reaches 1.8 m in ~38 s"), labeled estimate.
- Reframe pinch points: skip unless the real scan has furniture in a
  corridor; then it is the best "only a scan sees this" moment (1.5 h).
- Emergency types: fire and alarm-only for sure; active intruder only if the
  P1 loop is done (it is the most memorable second type, but behavior rules
  differ: hide is correct, threat moves).

## Judging fit

| Criterion | How the demo hits it |
|---|---|
| Technical difficulty | Scan -> validated navmesh in seconds with a ground-truth benchmark; lockstep clock with parallel LLM calls and a tick deadline; record/replay determinism |
| Use of AI / agents | Three agent roles with distinct jobs: occupants, adversary, optimizer; plus LLM room labeling and the scenario chatbot. "Model proposes, code checks" |
| Impact | Most buildings never get an egress model because CAD modeling costs days and Pathfinder runs ~$2,500/yr; schools and offices plan intruder response with tabletop kits [V]. Accessibility personas show who the building fails |
| Completeness | One flow, no slides between steps: scan, validate, place emergency, run, break, fix, re-run, numbers. Heatmap and exit counts look like a real tool |
| Presentation | Real room on screen, one interview question, one before/after number |
| Originality | The loop on a phone scan; grounded interview; no panic myth. Name the prior art in one sentence so judges see we know it |

Three strongest moments:
1. "This is this room, scanned this morning": scan -> validator OK -> 3D
   navmesh in seconds. Lands differentiator 1 and pre-empts "is it real".
2. The adversary blocks the exit our SPOF analysis flagged and plants "the
   back door is locked"; classic agents reroute, LLM agents stall at the
   fork; the optimizer adds a warden and a sign; scoreboard drops (use real
   numbers from 3 seeds).
3. Click one occupant: "Why didn't you take the back door?" Answer cites tick
   and the person who told it. Close with the cost meter.

## Sources

- Pathfinder CAD/BIM import and manual steps: https://www.thunderheadeng.com/docs/2026-1/pathfinder/geometry/generating-from-cad
- Pathfinder features, trial: https://www.thunderheadeng.com/pathfinder
- Pathfinder pricing (site script): https://www.thunderheadeng.com/docs/2026-1/purchasing-licensing/purchasing/ and https://store2.thunderheadeng.com/
- Pathfinder V&V and door flow: https://www.thunderheadeng.com/docs/2025-1/pathfinder/appendices/verification-validation/flow-rate
- MassMotion IFC/Revit: https://www.pbctoday.co.uk/news/bim-news/efficiency-crowd-simulation-software/34145/ and https://aecmag.com/news/news-oasys-launches-low-cost-mass-motion-flow-beta-online/
- buildingEXODUS: https://fseg.gre.ac.uk/exodus/work.html, https://fseg.gre.ac.uk/fire/news_archive.html
- Simulex: https://www.iesve.com/software/virtual-environment/applications/simulex
- Legion Evac: https://itsinternational.com/its6/products/legions-next-gen-evacuation-software
- STEPS/EVACNET/Simulex classification: https://lup.lub.lu.se/record/637658
- AnyLogic editions: https://anylogic.help/anylogic/ui/editions.html
- JuPedSim: https://pypi.org/project/jupedsim ; Vadere: https://github.com/pedestrian-dynamics-HM/vadere ; Menge: https://gamma.cs.unc.edu/Menge/
- FDS+Evac discontinued: https://pages.nist.gov/fds-smv/ , https://groups.google.com/g/fds-smv/c/0nh_v6gt4II
- Mendoza et al.: https://arxiv.org/abs/2606.14989 ; Calzolari et al.: https://arxiv.org/abs/2606.31038 ; Li et al.: https://arxiv.org/html/2509.21868v2 ; Yang EGU26: https://meetingorganizer.copernicus.org/EGU26/EGU26-3725.html ; AAMAS 2026 demo: https://ifaamas.org/Proceedings/aamas2026/pdfs/SEYR5220.pdf
- monument-evac-sim: https://github.com/MJones235/monument-evac-sim ; AgentEvac: https://github.com/denoslab/AgentEvac
- Misinformation evac: https://github.com/TheMayukhG/Evacuation-Model-with-Misinformation-spread ; scan2net: https://github.com/ecalogero/scan2net ; mc2sim: https://github.com/Taichiops/mc2sim-app ; Unity-FireEvacSim: https://github.com/kayo2e/Unity-FireEvacSim-pipeline ; SmartEvac: https://github.com/vishesh25004370-KIET/SmartEvac
- Devpost: https://devpost.com/software/fire-cv , https://devpost.com/software/evacmap , https://devpost.com/software/path-hero-7u9szm , https://devpost.com/software/zero-panic-in-movement-zpm , OpenDisaster (mirror of devpost.com/software/open-disaster): https://kelaenderkoe.pages.dev/Using-https-devpost.com/software/open-disaster . Devpost blocked direct fetches (403); details are from search snippets.
- Evacuation Plan App: https://apps.apple.com/us/app/evacuation-plan-app/id6689522614 ; Polycam life safety tags: https://learn.poly.cam/hc/en-us/articles/44278971384852 ; Mappedin: https://www.mappedin.com/resources/blog/your-guide-to-scanning-spaces-using-mappedin-ios-app/
- Drill logs: https://safetyculture.com/library/property-and-facilities-management/fire-drill-log-aayzfz92gqxzt6xl , https://www.fieldproxy.ai/automations/fire-drill-coordination
- School safety: https://schoolsafety.gov/resource/active-shooter-exercise-starter-kits , https://fortune.com/2024/03/08/school-shootings-spurring-states-fund-digital-building-maps-prepare-first-responders/ , USC MassMotion active shooter: https://viterbischool.usc.edu/?p=79097
