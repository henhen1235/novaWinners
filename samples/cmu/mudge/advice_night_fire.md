# What went wrong and what to change

303 of 305 people got out, but the last one left at 542 s. Smoke in the fire room (r_A105) reaches 1.8 m at about 121 s, so the margin is -421 s. Two problems drive this: slow pre-movement (median 71 s, p90 166 s, visitors slowest) and two mobility-impaired people left waiting at stair refuges because there is no assisted evacuation plan. Exit width and queueing are not the problem: there was almost no queueing and exit capacity is well above what is needed. Several single rooms, if lost, cut off many other rooms.

## Findings

- **Required egress time is far longer than the time before smoke arrives** (E1, E6): The last person left at 542 s. Smoke reaches 1.8 m in r_A105 at 121 s, giving a margin of -420.9 s.
- **Mobility-impaired people left at refuges** (E2, E7, E8, E9, E27): Two people, a_029 in r_A300B and a_253 in r_C200D, were still waiting for help at 600 s. No assisted evacuation plan exists.
- **Pre-movement time dominates; visitors are slowest** (E1, E3, E4, E5): Median pre-movement was 71 s with p90 at 166 s. Visitors had a median of 90.5 s, and the last visitor left at 542 s. Staff were quick (27.5 s). Queueing was close to zero.
- **Exit use is uneven; three exits unused** (E12, E20, E24): exit_1_02 reached 1.20 of the 1.3 p/s/m cap. exit_1_08, exit_1_10 and exit_1_12 were not used at all, and exit_1_07 (2.0 m wide) took only 6 people.
- **Single points of failure** (E28, E29, E30): Losing r_B19 strands 30 rooms; losing r_A300 strands 22 rooms.

## For the safety team

1. **Assisted evacuation plan with named helpers for the L2 and L3 refuges** (assisted_plan, effort medium; basis P4; evidence E7, E8, E9, E27). Without assigned helpers, people at refuges wait indefinitely. Expected: The two refuge stragglers are evacuated. Measured: not simulated.
2. **Direct PA message at alarm** (pa_procedure, effort low; basis P2; evidence E1, E5, E6). Pre-movement time dominates the total. Expected: Lower median and p90 pre-movement; earlier last exit. Measured: not simulated.
3. **Fire warden on L3 to sweep the floor** (staffing, effort low; basis P3; evidence E3, E29). Wardens get people moving faster and direct them to exits. Expected: Shorter pre-movement on L3. Measured: not simulated.
4. **Signs toward the unused exit_1_08** (signage, effort low; basis P5; evidence E24). Occupants know few exits, so some exits go unused. Expected: Load spread more evenly across exits. Measured: not simulated.

## For the architect

1. **Add exit at door_3_089 (r_A310) with an outside stair** (add_exit, effort high; basis P7; evidence E29, E8). r_A300 is a single point of failure for 22 rooms. Expected: An L3 route that does not depend on r_A300. Measured: not simulated.
2. **Add exit at door_2_166 with an outside stair** (add_exit, effort high; basis P7; evidence E32). r_A203B is a single point of failure for 9 rooms. Expected: An L2 route that bypasses r_A203B. Measured: not simulated.
3. **Widen exit_1_02 to 1.38 m** (widen_door, effort medium; basis P1; evidence E12). Its peak flow (1.20 p/s/m) is close to the 1.3 p/s/m cap. Expected: More capacity at the busiest exit. Measured: not simulated.
4. **Add exit at door_A_071 (LA) with an outside stair** (add_exit, effort high; basis P7; evidence E25, E28). r_B19 is a single point of failure for 30 rooms, and the LA stairs show queues. Expected: A second way out of LA. Measured: not simulated.

A new exit is always tested together with signs toward it in rooms within 30 m, because occupants mostly know only the exit they came in by.

## Evidence

- E1: RSET overall: alarm at 0 s; pre-movement median 71.0 s (p90 165.5); travel median 32.5 s; queueing median 0.0 s (p90 0.0); last exit 542.0 s; evacuated 303/305 by 600.0 s.
- E2: RSET mobility_impaired (2 people, 0 out): pre-movement median 321.5 s, travel median None s, queueing median None s, last exit None s.
- E3: RSET regular (274 people, 274 out): pre-movement median 69.8 s, travel median 32.5 s, queueing median 0.0 s, last exit 376.0 s.
- E4: RSET staff (2 people, 2 out): pre-movement median 27.5 s, travel median 17.2 s, queueing median 0.2 s, last exit 72.0 s.
- E5: RSET visitor (27 people, 27 out): pre-movement median 90.5 s, travel median 35.5 s, queueing median 0.0 s, last exit 542.0 s.
- E6: ASET estimate: smoke from the fire in r_A105 (t=0 s) reaches 1.8 m in that room after 121.1 s (closed-room zone estimate, analysis/smoke.py, room of origin only). Against the last exit (542.0 s) the ASET-RSET margin is -420.9 s (negative: not everyone is out in time).
- E7: Stragglers: 2 of 305 still inside at the end; by persona {'mobility_impaired': 2}; by state {'waiting_at_refuge': 2}; by room {'r_A300B': 1, 'r_C200D': 1}.
- E8: Straggler a_029 (mobility_impaired): ends waiting_at_refuge on L3 in room r_A300B; last decision at t=85.5 s: GO_TO_REFUGE, evidence ['p1'], why: 'I cannot take the stairs; I wait at the landing for help.'.
- E9: Straggler a_253 (mobility_impaired): ends waiting_at_refuge on L2 in room r_C200D; last decision at t=555.5 s: GO_TO_REFUGE, evidence ['p1'], why: 'I cannot take the stairs; I wait at the landing for help.'.
- E10: Exit exit_1_04 (L1, 1.526 m): 50 people (17%); peak flow 0.72 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 2 queued within 5 m.
- E11: Exit exit_1_16 (L1, 1.526 m): 46 people (15%); peak flow 0.59 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E12: Exit exit_1_02 (L1, 0.92 m): 41 people (14%); peak flow 1.20 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E13: Exit exit_1_13 (L1, 0.917 m): 32 people (11%); peak flow 0.76 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E14: Exit exit_1_09 (L1, 0.8 m): 30 people (10%); peak flow 0.62 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 0 queued within 5 m.
- E15: Exit exit_1_01 (L1, 0.8 m): 23 people (8%); peak flow 0.62 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E16: Exit exit_1_14 (L1, 0.8 m): 20 people (7%); peak flow 0.50 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E17: Exit exit_1_17 (L1, 0.8 m): 20 people (7%); peak flow 0.50 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E18: Exit exit_1_03 (L1, 0.8 m): 17 people (6%); peak flow 0.50 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E19: Exit exit_1_15 (L1, 1.02 m): 11 people (4%); peak flow 0.29 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E20: Exit exit_1_07 (L1, 2.0 m): 6 people (2%); peak flow 0.10 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 0 queued within 5 m.
- E21: Exit exit_1_06 (L1, 0.914 m): 4 people (1%); peak flow 0.22 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 0 queued within 5 m.
- E22: Exit exit_1_11 (L1, 0.8 m): 2 people (1%); peak flow 0.12 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 0 queued within 5 m.
- E23: Exit exit_1_05 (L1, 0.914 m): 1 people (0%); peak flow 0.11 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 0 queued within 5 m.
- E24: Unused exits (3 of 17): ['exit_1_08', 'exit_1_10', 'exit_1_12'].
- E25: Stair queues (max people queued within 5 m of a stair end): {'stair_A_1_C19F': 2, 'stair_1_2_C100D': 2, 'stair_A_1_C19C': 1, 'stair_A_1_B19C': 1, 'stair_A_1_B19': 1}.
- E26: Screening, not a code review: egress width 18.5 m of exits for 305 occupants; at 0.2 in per occupant (IBC 1005.3.2 as quoted in research/MAPPING_IDEAS.md, unverified) 1.55 m is needed: enough. Exits per level against occupant load: not checked (no factor in analysis/ or research/).
- E27: Screening, not a code review: accessible means of egress: 2 people end the run waiting at a stair refuge (rooms {'r_A300B': 1, 'r_C200D': 1}); no assisted evacuation plan is in the scenario
- E28: Single point of failure (room r_B19): losing it strands 30 rooms (40 occupants by IBC load) and removes 0% of exit width (analysis/spof.py).
- E29: Single point of failure (room r_A300): losing it strands 22 rooms (27 occupants by IBC load) and removes 0% of exit width (analysis/spof.py).
- E30: Single point of failure (room r_B308): losing it strands 11 rooms (14 occupants by IBC load) and removes 0% of exit width (analysis/spof.py).
- E31: Single point of failure (room r_B307A): losing it strands 10 rooms (13 occupants by IBC load) and removes 0% of exit width (analysis/spof.py).
- E32: Single point of failure (room r_A203B): losing it strands 9 rooms (18 occupants by IBC load) and removes 0% of exit width (analysis/spof.py).
- E33: Pinch points: not computed (more than 150 rooms).

Principles: P1: Door flow is capped near 1.3 persons/s per m of clear width (SFPE hydraulic model, research/EVAC_PSYCHOLOGY.md s2); more or wider exits raise capacity. P2: Pre-movement is often the largest part of evacuation time; direct instructions from staff or a PA shorten it (BRANZ SR143, research/EVAC_PSYCHOLOGY.md s1). P3: Offices with trained wardens start moving fast; wardens sweep areas and direct others (SR143, research/EVAC_PSYCHOLOGY.md s1 and s5). P4: People who cannot use stairs wait at refuges unless a plan assigns helpers; only about 30% get help spontaneously (NIST NCSTAR 1-7, docs/SIM_INTERFACES.md). P5: Occupants know few exits (the one they came in by, maybe the nearest); signs and drills add known exits (research/EVAC_PSYCHOLOGY.md s5 persona table). P6: Hearsay about blocked exits spreads; an official message can correct it (research/EVAC_PSYCHOLOGY.md, sim rumor model). P7: A room or door whose loss strands other rooms or removes exit width is a single point of failure (analysis/spof.py). P8: Required safe egress time must stay below available safe egress time with margin (SFPE / ISO TR 16738 timeline: detection + alarm + pre-movement + travel). P9: Egress width capacity of about 0.2 in (5 mm) per occupant (IBC 1005.3.2 as quoted in research/MAPPING_IDEAS.md, unverified).

Model anthropic/claude-opus-5.5; LLM cost $0.0841. Screening checks are not a code review; simulated effects use the rule policy, not LLM occupants.
