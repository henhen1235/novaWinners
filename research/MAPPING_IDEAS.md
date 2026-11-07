# Mapping-side ideas (WS-A blueprint, WS-B LiDAR)

Question: what can the capture/mapping side add, before any LLM-agent sim,
that judges remember? Scope: WS-A and WS-B only. Date: 2026-11-07.

Ranking rule (per coordinator): score = value / effort, where value = mean of
demo payoff and user value (1-5 each) and effort counts unbuilt prerequisites.
Uniqueness breaks ties. Prior art is listed as "reuse", not as a penalty.

Markers: [V] verified in a source I or a helper opened; [X] checked by a
throwaway experiment in this repo's venv; [S] secondary source only (forum,
exam-prep site, state adoption), read the adopted code text before a slide;
[U] unverified.

## Facts that shape every idea

- Both LiDAR paths write `"points_of_interest": []`
  (`ingest_lidar/pipeline.py:240`, `roomplan_to_scene.py:425`). Any idea that
  needs signs, extinguishers or pull stations has no data on a scan until we
  add tap-to-tag (idea 9). Visibility of exit doors needs no POIs.
- The fixture passes every egress code check easily [X]. On
  `tiny_office` the max travel distance is 12.5 m (limit 61 m), exit
  separation 16.0 m vs 18.7 m diagonal (ratio 0.86, need 0.5), occupant
  load 12. A small clean office gives a green report. What a scan sees and a
  plan cannot is the as-is state: furniture in corridors, blocked or narrowed
  exits, ceiling heights. Build on that.
- RoomPlan recommends at most about 186 m2 (2,000 sq ft) total per structure
  and 50 lux or more [V, WWDC23 10192]. Our demo floor must fit that.
- Our app already keeps one ARSession across rooms
  (`ScanModel.swift:50`), so anything tapped during the scan lands in the
  same world frame as `structure.json`.

## Ranked table

| # | Idea | Demo | User | Effort h | Score | Uniq | Risk | Feeds sim? |
|---|------|------|------|----------|-------|------|------|------------|
| 1 | Lose-one-exit / single-point-of-failure doors and rooms | 4 | 4.5 | 1 | 4.3 | 4 | low | yes: adversary target list |
| 2 | Occupant load per room (IBC Table 1004.5 factors) | 2 | 4 | 0.75 | 4.0 | 1 | low | yes: agent counts (already in IDEAS.md) |
| 3 | As-is pinch-point map (clear width, flow capacity per route) | 4 | 5 | 1.5 | 3.0 | 4 | low | yes: door/corridor flow limits |
| 4 | Smoke clock per room (Tanaka-Yamana, ceiling height from scan) | 4 | 4 | 2 | 2.0 | 4 | medium | yes: time-varying hazard, tenability |
| 5 | Visibility: "can you see an exit / sign from here" map + decision points | 4 | 4 | 2 | 2.0 | 3 | low | yes: agent perception |
| 6 | AR dollhouse on the judges' table (USDZ, AR Quick Look) | 5 | 3 | 2 | 2.0 | 2 | medium | consumes sim output later |
| 7 | Advisory code red-pen (travel distance, separation, capacity, dead ends, door width/swing) | 4 | 5 | 2.5 | 1.8 | 3 | medium | yes: capacity, loads |
| 8 | Phone-to-browser pairing by QR (from IDEAS.md) | 3 | 2 | 1.5 | 1.7 | 1 | low | no |
| 9 | Tap-to-tag safety equipment in the app (raycast -> POI) | 3 | 4 | 2 | 1.8 | 2 | medium | yes: signs and equipment in perception |
| 10 | Printable evacuation plan poster (ISO 23601 style) from the scan | 4 | 4 | 2.5 | 1.6 | 3 | low | no |
| 11 | Accessible-route audit (ADA widths, turning space, steps) | 3 | 4 | 2.5 | 1.4 | 3 | medium | yes: wheelchair persona routing |
| 12 | Sign placement optimizer (set cover on visibility) | 4 | 4 | 3 (incl. #5) | 1.3 | 3 | low | yes: optimizer action menu |
| 13 | Equipment coverage (extinguisher walking distance, pull stations) | 3 | 4 | 3 (incl. #9) | 1.2 | 3 | medium | weak |
| 14 | Gaussian-splat photoreal view with the clean model overlaid | 4 | 2 | 3 | 1.0 | 2 | medium | no |
| 15 | Rescan diff: new obstructions in egress paths | 3 | 4 | 4 | 0.9 | 3 | high | yes |
| 16 | Alarm audibility map (attenuation from horn positions) | 3 | 3 | 3.5 | 0.9 | 4 | high | yes: deaf/HoH personas |
| 17 | Photo of the posted evacuation map -> scene | 4 | 4 | 5 | 0.8 | 2 | high | yes |
| 18 | Vision-LLM auto-detection of equipment from frames | 4 | 4 | 5 | 0.8 | 2 | high | yes |
| 19 | Fire-marshal narration (LLM reads the audit JSON aloud) | 3 | 2 | 3.5 (incl. #7) | 0.7 | 2 | medium | no |
| 20 | As-designed vs as-is (register blueprint to scan) | 4 | 3 | 5 | 0.7 | 3 | high | indirect |
| 21 | Full-scale AR replay in the real hallway | 5 | 3 | 8 | 0.5 | 3 | high | consumes sim output |
| 22 | Multi-phone simultaneous capture | 4 | 2 | 7 | 0.4 | 2 | high | no |

Idea 2 ranks second by formula but is a 40-line helper; it is folded into
the sections for 1, 3 and 7 below. The top-5 sections are 1, 3, 4, 5, 6.

## Quick wins (<= 2 h, clear product value, novelty ignored)

1. Lose-one-exit analysis (1 h). networkx on the room-door graph.
2. Occupant load per room (0.75 h). Area / factor, `extensions.occupancy`.
3. Pinch-point map (1.5 h). Distance transform on walkable.
4. Smoke clock per room (2 h). Closed-form equation, verified constants.
5. Exit visibility map (2 h). Prototype exists, 3 s per floor at 5 cm.
6. Static AR dollhouse (1.5 h). RoomPlan already exports `structure.usdz`;
   add colored room tiles and exit markers, AirDrop to a phone.
7. Phone-to-browser QR pairing (1.5 h). Smooth live scan of the judging room.
8. Tap-to-tag equipment in the app (2 h, Swift). The only way scans get POIs.
9. Evacuation poster (2 h for a basic version: plan, routes, "you are here",
   exits; ISO symbols later).

## Top 5

### 1. Lose-one-exit and single points of failure (1 h, WS-A + WS-B shared)

Pitch: "Block any one door. These three offices lose every way out, and the
building keeps only 47% of its exit width." On `tiny_office` both numbers
are real: the corridor is an articulation point, so a fire or blockage in
the corridor leaves all six offices with no route out, and losing the main
entrance leaves 47% of the exit width.

Algorithm:
1. Graph G: nodes = rooms + "outside"; edges = doors (attrs: id, width_m).
   Use `nx.MultiGraph`: both fixture exits join corridor and outside, and a
   plain Graph merges them into one edge that then looks like a bridge [X].
   Bridge detection on a MultiGraph: check parallel-edge count, or run it on
   a Graph whose edges carry a door count.
2. Rooms with no path to "outside" after removing each door d: one BFS per
   door (`nx.connected_components(G - d)`). Bridges of G (`nx.bridges`) are
   exactly the doors whose loss strands someone; skip bridges into a leaf
   room (a one-door office's own door is trivially a bridge); articulation points
   (`nx.articulation_points`) are rooms whose fire strands others ("every
   route from Office 3 passes through the lobby").
3. For each exit e: remaining exit width / total exit width, as a
   resilience number, not a code verdict. The code rule (IBC 1005.5, from
   memory [U]) compares the remainder with 50% of the REQUIRED capacity,
   which a small office meets easily. On `tiny_office` [X]: lose the 1.8 m
   main entrance and 1.6 m remains, 47% of the exit width, still 26x the
   required width for 12 occupants. Show both numbers.
4. Travel-distance delta per room with exit e removed (geodesic field, see
   idea 7), shown as "+11 m for Office N3".

Data: rooms, doors.connects, exits.width_m. Same on blueprint and scan.
Demo (10 s): click a door, the rooms it strands turn red, a bar shows exit
width left. Feeds sim: this is the adversary's ranked candidate list
("block door_08: strands 0 rooms, cuts capacity to 47%"), so the adversary
starts from geometry instead of guessing. Risk: low. Uniqueness: egress
tools compute it implicitly; nobody shows it from a phone scan in seconds.

Build: WS-A and WS-B share one module (`tools/weak_spots.py` reading any
scene.json), output to `extensions.weak_spots`, overlay PNG; viewer click.

### 2. As-is pinch-point map (1.5 h, mostly WS-B)

Pitch: "The plan says this corridor is 1.8 m. The scan says a filing cabinet
makes it 0.74 m, below the 36 in (0.915 m) accessible minimum, and halves its
flow."

Algorithm:
1. Rasterize walkable before erosion (or add back the 0.25 m erosion:
   clear width = 2 * (EDT + 0.25 m)) on the 5 cm grid the pipeline already has.
2. Skeleton (`skimage.morphology.skeletonize`) of free space; width along
   the skeleton = 2 * EDT.
3. For each room-to-exit route (navmesh path), take the minimum width on
   the path = pinch. Flag widths < 1.12 m (44 in IBC 1020.2 corridor, [S]),
   < 0.915 m (36 in, ADA 403.5.1 [U, values known, not opened]),
   < 0.813 m (32 in door clear width, IBC 1010.1.1 [S], ADA 404.2.3).
4. Capacity at the pinch: SFPE maximum specific flow about 1.3 persons/s per
   m of effective width (per PITCH_IDEAS.md, [U] re-check), effective width
   = clear width minus boundary layers (about 0.15 m per wall side, [U]).
   Report persons/min per route and the room whose route is narrowest.

Data the scan has and the plan lacks: furniture and clutter in walkable.
Demo: heat strip along corridors, red dot with "0.74 m, 46 p/min". Feeds
sim: per-edge width and flow cap for the navmesh; wheelchair persona filter.
Risk: walkable is eroded 0.25 m for the agent radius, so widths must add it
back; RoomPlan's parametric boxes overstate furniture size a little.

### 3. Smoke clock per room (2 h, WS-B first, WS-A with a default ceiling)

Pitch: "If a fire starts here, smoke reaches head height in 50 seconds.
This 2.4 m ceiling gives 38 s; a 4 m ceiling would give 64 s."

Equation [V, Tanaka and Yamana 1985, Fire Science and Technology 5(1):31-40,
Eqs. 1.6-1.9, read from the PDF]: closed room, no venting, constant layer
density, uniform plan area A, fire Q = Q0 t^n:

    k = Cm * (rho_a^2 g / (cp Ta))^(1/3) / rho_s,  Cm = 0.21
    t(Z) = (n+3)/2 * [ (Z^(-2/3) - H^(-2/3)) / (k Q0^(1/3) / A) ]^(3/(n+3))

With rho_a 1.2, g 9.81, cp 1.0 kJ/kg K, Ta 293 K: Cm(...)^(1/3) = 0.0764
kg/(s kW^(1/3) m^(5/3)) [X], which matches the Zukoski plume constant 0.076,
a consistency check. rho_s = 1.1 gives k = 0.0695.

Results on `tiny_office` (H = 3.0 m, layer to Z = 1.8 m) [X]:

| room | A m2 | steady 500 kW | fast t^2 (alpha 0.0469) | medium t^2 (0.0117) |
|---|---|---|---|---|
| office 18.7 m2 | 18.7 | 9.9 s | 49.6 s | 65.5 s |
| office 22.5 m2 | 22.5 | 11.9 s | 55.5 s | 73.3 s |
| corridor | 29.3 | 15.6 s | 65.0 s | 85.8 s |

20 m2 room, fast fire: ceiling 2.4 m -> 38 s, 3.0 -> 52 s, 4.0 -> 64 s,
6.0 -> 76 s [X]. A from-memory NFPA 92 steady-fire correlation gave times
about 30% shorter [U, equation not verified, do not cite]. The t^2 alpha
values (fast 0.0469, medium 0.0117 kW/s^2) are standard design values [U,
not re-opened].

Why it matters: EVAC_PSYCHOLOGY.md gives pre-movement medians of minutes;
the room of fire origin has under a minute. The clock makes that gap
visible per room.

Honest limits, say them on the slide: room of origin only, closed room,
no leakage, no door flow to neighbours, constant smoke density (Tanaka and
Yamana say this holds "during the early stage of fire"). Multi-room smoke
spread needs a two-zone model; NIST CFAST 7.7.7 is public domain [V] but has
no macOS binary (Windows only; macOS makefiles exist, not built) [V]. Japan's
evacuation safety verification method (MLIT Notification 1441, 2000) uses the
same idea: evacuation must finish before smoke descends to a hazardous
height [S, JICA report; hazard height value not confirmed].

Data: ceiling height. Contract has one per level; the scan has it per cell
(occupancy upper band). Add `extensions.smoke.rooms[room_id] = {ceiling_m,
t_head_height_s: {fast, medium}}`. Blueprints usually lack ceiling heights
(only sections and RCPs show them), so WS-A uses a 2.7 m default and says so.

Build: WS-B per-room max height (median of top band per room polygon, 0.5
h); formula + table + colored overlay (1 h); viewer legend (0.5 h). WS-A
reuses the module with the level ceiling. Feeds sim strongly: smoke layer
height per room over time = what agents perceive ("smoke at head height in
Office N2") and when a room becomes untenable.

### 4. Visibility: exit and sign isovists, decision points (2 h)

Pitch: "Green: you can see a way out from here. Red: you are relying on
memory." Then: "Our agents see exactly this, nothing more."

Prototype [X] (`isovist.py` in the session scratchpad, numpy + shapely + cv2,
3.0 s on 43k cells at 5 cm, 2 signs): raster line-of-sight from each target
to every walkable cell, walls and obstacles >= 1.6 m opaque, doors open.
Result on `tiny_office`: corridor 100% sees an exit sign within 30.48 m;
offices 0-6%. Isovist area on a 0.5 m lattice (72 rays) took 0.2 s: 19 to
56 m2, median 25.5 m2.

Rules to cite:
- IBC 2021 1013.1 [S]: no point in an exit access corridor more than 100 ft
  (30.48 m) or the sign's listed viewing distance from the nearest visible
  exit sign. Rooms needing only one exit are exempt, so apply it to
  corridors and to rooms with 2+ required exits, or the map shows false red
  (our prototype's offices would all be "red" otherwise).
- Visibility Catchment Area (FSEG): the floor region from which a sign is
  legible; with observation angle the VCA is a circle of diameter = max
  viewing distance; 15.2 cm letters legible at 30 m [V, fseg.gre.ac.uk].
- Only about 38% of participants noticed a static exit sign in view, 97%
  of those followed it (Xie et al. 2012, Fire and Materials 36:367-382);
  dynamic signs: 77% noticed (Galea et al. 2014, Fire Safety Science 11) [V].
- Isovist measures correlate with locomotion and experience ratings in 16
  virtual scenes (Wiener et al. 2007, Perception 36:1066) [V abstract, r
  values not seen]. Hospital wayfinding moved from local visible cues to
  global integration as people learned the building (Haq and Zimring 2003)
  [U, abstract snippet only].

On scans without sign POIs, use exit doors as targets ("can you see an exit
door") until tap-to-tag (idea 9) exists. Decision points: junctions of the
walkable skeleton (degree >= 3) plus doors; store per point what is visible
(exits, signs, rooms, distances) in `extensions.visibility.decision_points`.
That is the observation string WS-C's prompt template already asks for
(EVAC_PSYCHOLOGY.md, "NEARBY: {visible_people_and_what_they_do}").

Reuse: pysovist (MIT, exact 2D isovists from wall segments, isovist_field
for many observers) [V]; depthmapX v0.9.1 (GPLv3, macOS CLI binary; call as
an external tool, do not link) [V]; PyVisiLibity (LGPL-3.0, stale since
2020) [V]. Our raster version is 60 lines and already fast enough.

Build: WS-B and WS-A share the module; WS-B adds the skeleton junctions
(0.5 h), overlay PNG and GeoJSON (0.5 h), viewer toggle (0.5 h).

### 5. AR dollhouse on the judges' table (2 h static, +1.5 h animated)

Pitch: "Point your phone at the table." The scanned floor appears as a
dollhouse; rooms are colored by smoke clock, pinch points glow, and later
the drill plays on it.

Verified so far [X]: usd-core in `.venv` authors an animated USDZ: 30
agents, translate timeSamples every 12 frames over 240 frames at 24 fps,
packaged with `UsdUtils.CreateNewARKitUsdzPackage`, 23 KB, 0 errors from
all 30 UsdValidation validators (including the usdz package validators).
Note: this usd-core has no `UsdUtils.ComplianceChecker` (the old
`usdchecker --arkit` path), so ARKit-specific checks need a device.

Apple limits [V]: about 50 meshes per USDZ, under 100,000 polygons,
textures 1024 px (max 2048), at most six textures. Transform animation
playback in AR Quick Look is not documented by Apple [U]; a developer-forum
thread reports "basic animation support", no integer-only time codes, and
tight memory limits on animation length and object count. Test on a device
in the first 20 minutes. If 30+ separate agents fail, put all agents in one
mesh driven by UsdSkel (one joint per agent) or drop to 10-15 agents.
Use UsdGeomMesh for agents, not Capsule gprims, to be safe [U].

Build: static first. WS-B: start from RoomPlan's `structure.usdz` or our
`display.glb` (decimate to < 100k triangles), add one colored floor quad per
room and exit arrows (1 h, usd-core); AirDrop to the iPhone, open in Files
(0.5 h). Animated version after WS-C has trajectories: one translate op per
agent with timeSamples (1.5 h). Full-scale AR in the real hallway (idea 21)
needs the inverse of `extensions.lidar.input_to_scene` and the same
ARSession or ARWorldMap relocalization; leave it.

## Other ideas, one line each

- 7 Advisory code red-pen. Prototype [X] ran on the fixture in 0.5 s with
  scikit-image `MCP_Geometric` geodesic fields. Values [S]: IBC 2021 Table
  1017.2 travel distance B 200 ft / 300 ft sprinklered (A, E, M 200 / 250);
  Table 1006.2.1 common path B 75 ft (OL > 30, unsprinklered) / 100 ft;
  1007.1.1 separation 1/2 diagonal, 1/3 sprinklered [U, not found in
  sources opened]; 1020.5 dead ends 20 ft, 50 ft in sprinklered B, E, F,
  I-1, M, R-1, R-2, S, U [S, ICC interpretation BU 21-68-23]; Table 1004.5
  business 150 ft2 gross (changed from 100 in 2021), classroom 20 net,
  unconcentrated assembly 15 net [S]; 1005.3.2 0.2 in per occupant [U];
  1010.1.2.1 swing in direction of egress at OL >= 50 [U]. Word every line
  "advisory, IBC 2021 as published, your adopted code and AHJ govern".
  Needs a building with problems to be dramatic; real findings on scans
  will come from pinch points and blocked exits, not from the plan.
- 9 Tap-to-tag. A button row during the scan (Exit sign, Extinguisher, Pull
  station, AED): `ARView.raycast` from screen center, write kind and world
  position into `scan_notes.json`; the converter applies the same transform
  as walls. Same ARSession, so same frame [U until tested on device].
- 10 Evacuation poster. Plan + routes + "you are here" + exits + equipment;
  print one for the judging room. Posting is required in some occupancies
  (for example R-1 guestrooms under local IFC adoptions) [S]; ISO 23601 is
  the symbol standard [U].
- 11 Accessible route. Clear width 36 in, turning circle 60 in (EDT >= 0.76
  m), door 32 in; ramps 1:12, vertical change 1/4 in, beveled to 1/2 in
  [S for 303]. A LiDAR mesh (1-2 cm noise) can see a 15 cm step but not a
  6-13 mm threshold, and RoomPlan floors are flat; do not claim threshold
  checks.
- 12 Sign optimizer. Greedy set cover over candidate wall positions until
  every corridor cell sees a sign within 30.48 m; also feeds the WS-C
  optimizer's "add sign" action.
- 13 Equipment coverage. NFPA 10 Class A extinguisher within 75 ft (22.9 m)
  walking distance [S, table number disputed between sources]; NFPA 72
  pull station within 5 ft of each exit and 200 ft travel [U].
- 14 Splats. Spark (MIT; PLY, SPZ, SPLAT, KSPLAT, SOG; three.js) [V];
  GaussianSplats3D is no longer developed [V]; Scaniverse exports splat PLY
  [V]; Polycam "Splat PLY" needs a paid plan [V, third-party doc]. Needs a
  second capture and manual alignment; pure visuals.
- 15 Rescan diff and 20 as-designed vs as-is: both need 2D registration
  (wall-line RANSAC + ICP on rasterized walls). Construction deviation tools
  do this for BIM. Good second-day feature.
- 16 Alarm audibility. Needs horn positions (no POI kind for horns in the
  contract) and a crude attenuation model; credibility risk.
- 17 Posted-map photo. Prior work to reuse: Peter, Haala et al., ISPRS 2010
  ("Indoor navigation and modeling using photographed evacuation plans and
  MEMS IMU") [V]; Hassaan et al., Sensors 2023, symbol detection on 403
  German emergency plans plus 5,000 synthetic [V]. Plans are stylized and
  not to scale; needs a known door width.
- 18 Vision-LLM detection. Frame capture plus depth in Swift, then LLM
  labels and geometry places. Weqaa (YOLOv8n, extinguisher condition,
  CC-BY) is condition not location [V]. Tap-to-tag gets 80% of the value.
- 21 Full-scale AR and 22 multi-phone: multi-device RoomPlan capture is not
  documented; Apple's routes are one shared ARSession or ARWorldMap
  relocalization [V]. Out of scope today.

## Combo recommendation

"Your building's weak spots, before anyone runs a drill": ideas 1 + 3 + 4,
optionally 5 for the visual.

One overlay, four colors, one card:
- Red doors: lose this door and someone is stranded, or the share of exit
  width left (idea 1).
- Orange strips: pinch points with width and persons/min (idea 3).
- Room tint: seconds until smoke reaches head height if the fire starts
  here (idea 4, ceiling height from the scan).
- Then the "weak spots card": three lines, each with a number and a rule.

Why these three: about 4.5 h total, all deterministic and testable, all
work on both blueprint and scan, two of them (pinch points, per-room
ceiling) use data only a scan has, and each becomes a WS-C input (adversary
targets, edge flow caps, smoke timeline). Add idea 5 (visibility) when time
allows; it is the perception layer for the agents. Show the result as the AR
dollhouse (idea 6) only after the static version works on a device.

Second combo if the live demo is the priority: tap-to-tag (9) + exit
visibility (5) + evacuation poster (10): scan the judging room, tag the two
exit signs, hand the judges a printed evacuation plan of the room they sit
in.

## Pitch lines

- "Before a single simulated person moves, the scan already tells you three
  things: which door is a single point of failure, where the corridor is
  narrower than the plan says, and how many seconds each room has once
  smoke starts."
- "A floor plan shows the building as designed. The scan shows the filing
  cabinet in the corridor."
- "Ceiling height is time. Our scan measures it in every room; a 2.4 m
  ceiling gives about 38 seconds, a 4 m ceiling about 64."
- "Only about 38% of people notice a static exit sign (Xie et al. 2012).
  So our map shows where you can actually see a way out, and our agents see
  exactly that."
- "Advisory, not certification: every check names its code section and
  edition; the authority having jurisdiction decides."

## Sources

- Tanaka and Yamana 1985, Smoke control in large scale spaces (Part 1):
  https://www.jstage.jst.go.jp/article/fst/5/1/5_1_31/_article (PDF read,
  Eqs. 1.6-1.9)
- RoomPlan multi-room and size guidance, WWDC23 10192:
  https://developer.apple.com/videos/play/wwdc2023/10192/ and
  https://developer.apple.com/documentation/roomplan/scanning-the-rooms-of-a-single-structure
- AR Quick Look limits:
  https://developer.apple.com/documentation/arkit/adding-visual-effects-in-ar-quick-look-and-realitykit ;
  animation thread: https://developer.apple.com/forums/thread/652829
- Xie et al. 2012 via Filippidis et al. 2021:
  https://gala.gre.ac.uk/id/eprint/35904/7/35904_FILIPPIDIS_Exploring_the_potential_effectiveness.pdf
- Galea, Xie, Lawrence 2014: https://publications.iafss.org/publications/fss/11/1129
- FSEG Visibility Catchment Area: https://fseg.gre.ac.uk/fire/visibility_catchment_area.html
- Wiener et al. 2007: https://doi.org/10.1068/p5587 ; Emo 2014:
  https://doi.org/10.3390/bs4030167 ; Haq 2003 (EPB):
  https://econpapers.repec.org/RePEc:sae:envirb:v:30:y:2003:i:6:p:841-863
- Peter et al. 2010: https://www.isprs.org/proceedings/XXXVIII/part4/files/Peter.pdf ;
  Hassaan et al. 2023: https://doi.org/10.3390/s23198344
- pysovist: https://github.com/leaguilar/pysovist ; depthmapX:
  https://github.com/SpaceGroupUCL/depthmapX ; Spark: https://github.com/sparkjsdev/spark
- CFAST: https://pages.nist.gov/cfast/downloads.html
- IBC values (secondary): ICC interpretation BU 21-68-23
  https://www3.iccsafe.org/cs/committeeArea/pdf_file/BU_21_68_23.pdf ;
  Table 1017.2 via 2025 CBC excerpt
  https://gocodebook.com/us/california/california-building-code/means-of-egress/exit-access-travel-and-corridors/exit-access-travel-distance ;
  occupant load factor change: https://www.houstonpermittingcenter.org/media/9311/download
- ADA 303 (secondary): https://www.corada.com/documents/2010ADAStandards/303
- Japan smoke-descent method (secondary): https://openjicareport.jica.go.jp/pdf/11710415_02.pdf
- UpCodes Copilot: https://cms-v2.up.codes/features/copilot ; Pathfinder
  imports: https://thunderheadeng.com/pathfinder/features ; Matterport
  Property Intelligence:
  https://matterport.com/news/matterport-launches-property-intelligence-transforming-real-estate
