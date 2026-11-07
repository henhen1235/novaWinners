# What went wrong and what to change

1181 of 1200 people got out by 900 s. The last person left at 648.5 s, well after smoke reached 1.8 m in the rooms where it started: 104 s in r_2902 and 216 s in r_3500. Both safety margins (ASET minus RSET) are negative. Of the 19 people still inside, 16 are mobility impaired, and 14 of them were waiting at stair refuges with no plan to help them. Total exit width is enough. The real problems are the lack of assisted evacuation, slow pre-movement, a few narrow exits running at capacity, and corridor single points of failure.

## Findings

- **Negative ASET-RSET margin** (E1, E7, E8): The last exit at 648.5 s is far beyond the 104 s (r_2902) and 216 s (r_3500) smoke times, giving margins of -499 s and -432 s. Pre-movement has a p90 of 111 s.
- **Mobility-impaired occupants stranded at refuges** (E2, E9, E10, E12, E14, E15, E16, E17, E18, E19, E45): Only 34 of 50 mobility-impaired occupants got out. 14 people were left waiting at stair refuges, 7 of them in r_5902, and the scenario has no assisted evacuation plan. Two others were still queued for the elevator on L5.
- **Narrow exits at or above the flow cap** (E26, E29, E23): exit_2_12 (1.02 m) peaked at 1.37 p/s/m, above the sim's 1.3 maximum. exit_1_01 (1.10 m) had 8 people queued, and exit_2_02 reached the 1.3 cap.
- **Uneven exit use** (E20, E21, E39, E41): Two exits, exit_2_01 and exit_2_13, carried 34% of the people. exit_1_05 and exit_1_06 were nearly unused.
- **Corridor single points of failure** (E46, E47, E48, E50): Losing corridor c_4_001, c_5_002 or c_3_002 would cut off between 82 and 119 rooms each.
- **Occupants still deciding or waiting at the end** (E11, E13): Two people never left: a_0582 in r_2902 was still deciding (seeking information), and a_0767 in r_3401 was still waiting because they were not sure the alarm was real.

## For the safety team

1. **Assisted evacuation plan for stair refuges** (assisted_plan, effort medium; basis P4; evidence E45, E9). 14 people were left at refuges with no helpers assigned. Expected: Most refuge waiters get out. Measured: not simulated.
2. **Wardens on L5 to sweep floors and direct people** (staffing, effort low; basis P3; evidence E5, E1, E19). Staff start moving fastest (24 s median pre-movement), and wardens can direct others, including the people queued for the elevator. Expected: Shorter pre-movement p90. Measured: not simulated.
3. **Direct PA instruction at alarm** (pa_procedure, effort low; basis P2; evidence E1, E11, E13). Pre-movement is long, and some people were still unsure or seeking information at the end. Expected: Lower pre-movement time and fewer stragglers. Measured: not simulated.
4. **Sign the underused exit_1_05** (signage, effort low; basis P5; evidence E39, E20). exit_1_05 carried 4 people while exit_2_01 and exit_2_13 carried 34% between them. Expected: Spreads load more evenly across exits. Measured: not simulated.

## For the architect

1. **Widen exit_2_12** (widen_door, effort medium; basis P1; evidence E26). It ran above the 1.3 p/s/m cap. Expected: Widening to 1.5 m raises capacity by about 47%. Measured: not simulated.
2. **Widen exit_1_01** (widen_door, effort medium; basis P1; evidence E29). It had the longest queue (8 people). Expected: Widening to 1.65 m cuts the queue. Measured: not simulated.
3. **Add door_5_003 as an exit (needs an outside stair)** (add_exit, effort high; basis P7; evidence E18, E47, E9). L5 depends on corridor c_5_002, and the refuge at r_5901 had people waiting. door_5_003 is not at grade, so it needs an outside stair. Expected: A second egress path for L5 that bypasses c_5_002. Measured: not simulated.
4. **Add door_1_094 as an at-grade exit** (add_exit, effort medium; basis P1; evidence E10). The r_1903 stairwell refuge on L1 had a person waiting, and this door gives it direct at-grade egress. Expected: The L1 refuge waiter can leave directly. Measured: not simulated.

A new exit is always tested together with signs toward it in rooms within 30 m, because occupants mostly know only the exit they came in by.

## Evidence

- E1: RSET overall: alarm at 0 s; pre-movement median 38.0 s (p90 111.0); travel median 39.0 s; queueing median 0.5 s (p90 5.5); last exit 648.5 s; evacuated 1181/1200 by 900.0 s.
- E2: RSET mobility_impaired (50 people, 34 out): pre-movement median 44.0 s, travel median 81.8 s, queueing median 0.0 s, last exit 535.5 s.
- E3: RSET older_adult (64 people, 62 out): pre-movement median 38.0 s, travel median 58.8 s, queueing median 0.2 s, last exit 554.0 s.
- E4: RSET regular (909 people, 908 out): pre-movement median 40.0 s, travel median 37.2 s, queueing median 0.5 s, last exit 482.5 s.
- E5: RSET staff (64 people, 64 out): pre-movement median 24.0 s, travel median 31.2 s, queueing median 0.5 s, last exit 273.5 s.
- E6: RSET visitor (113 people, 113 out): pre-movement median 39.0 s, travel median 41.0 s, queueing median 0.0 s, last exit 648.5 s.
- E7: ASET estimate: smoke from the fire in r_3500 (t=0 s) reaches 1.8 m in that room after 216.4 s (closed-room zone estimate, analysis/smoke.py, room of origin only). Against the last exit (648.5 s) the ASET-RSET margin is -432.1 s (negative: not everyone is out in time).
- E8: ASET estimate: smoke from the smoke in r_2902 (t=45 s) reaches 1.8 m in that room after 104.2 s (closed-room zone estimate, analysis/smoke.py, room of origin only). Against the last exit (648.5 s) the ASET-RSET margin is -499.3 s (negative: not everyone is out in time).
- E9: Stragglers: 19 of 1200 still inside at the end; by persona {'mobility_impaired': 16, 'regular': 1, 'older_adult': 2}; by state {'waiting_at_refuge': 14, 'deciding': 1, 'waiting': 1, 'queued': 3}; by room {'r_5902': 7, 'r_4901': 2, 'c_5_002': 2, 'r_1903': 1, 'r_2902': 1, 'r_3902': 1, 'r_3401': 1, 'r_3903': 1}.
- E10: Straggler a_0262 (mobility_impaired): ends waiting_at_refuge on L1 in room r_1903; last decision at t=60.0 s: GO_TO_REFUGE, evidence ['p1'], why: 'I cannot take the stairs; I wait at the landing for help.'.
- E11: Straggler a_0582 (regular): ends deciding on L2 in room r_2902; last decision at t=900.0 s: SEEK_INFO, evidence ['p2'], why: 'SEEK_INFO (hazard).'.
- E12: Straggler a_0751 (mobility_impaired): ends waiting_at_refuge on L3 in room r_3902; last decision at t=323.5 s: GO_TO_REFUGE, evidence ['p1'], why: 'I cannot take the stairs; I wait at the landing for help.'.
- E13: Straggler a_0767 (older_adult): ends waiting on L3 in room r_3401; last decision at t=896.0 s: CONTINUE_TASK, evidence ['p1'], why: 'Not sure it is real yet; waiting for more cues.'.
- E14: Straggler a_0845 (mobility_impaired): ends waiting_at_refuge on L3 in room r_3903; last decision at t=188.5 s: GO_TO_REFUGE, evidence ['p1'], why: 'I cannot take the stairs; I wait at the landing for help.'.
- E15: Straggler a_0911 (mobility_impaired): ends waiting_at_refuge on L4 in room r_4901; last decision at t=167.0 s: GO_TO_REFUGE, evidence ['p1'], why: 'I cannot take the stairs; I wait at the landing for help.'.
- E16: Straggler a_0976 (mobility_impaired): ends waiting_at_refuge on L4 in room r_4902; last decision at t=286.0 s: GO_TO_REFUGE, evidence ['p1'], why: 'I cannot take the stairs; I wait at the landing for help.'.
- E17: Straggler a_0988 (mobility_impaired): ends queued on L5 in room r_5236; last decision at t=82.0 s: USE_ELEVATOR, evidence ['p1'], why: 'The elevator is the easiest way down for me.'.
- E18: Straggler a_1003 (mobility_impaired): ends waiting_at_refuge on L5 in room r_5901; last decision at t=235.5 s: GO_TO_REFUGE, evidence ['p1'], why: 'I cannot take the stairs; I wait at the landing for help.'.
- E19: Straggler a_1029 (older_adult): ends queued on L5 in room c_5_002; last decision at t=75.0 s: USE_ELEVATOR, evidence ['p1'], why: 'The elevator is the easiest way down for me.'.
- E20: Exit exit_2_01 (L2, 1.926 m): 199 people (17%); peak flow 1.09 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 3 queued within 5 m.
- E21: Exit exit_2_13 (L2, 1.837 m): 197 people (17%); peak flow 0.98 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E22: Exit exit_1_09 (L1, 1.788 m): 134 people (11%); peak flow 1.23 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E23: Exit exit_2_02 (L2, 2.0 m): 106 people (9%); peak flow 1.30 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 4 queued within 5 m.
- E24: Exit exit_2_08 (L2, 1.835 m): 85 people (7%); peak flow 0.60 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 0 queued within 5 m.
- E25: Exit exit_1_03 (L1, 1.823 m): 58 people (5%); peak flow 0.77 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E26: Exit exit_2_12 (L2, 1.021 m): 58 people (5%); peak flow 1.37 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 3 queued within 5 m.
- E27: Exit exit_1_04 (L1, 2.0 m): 54 people (5%); peak flow 0.60 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E28: Exit exit_1_07 (L1, 1.934 m): 54 people (5%); peak flow 0.67 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E29: Exit exit_1_01 (L1, 1.099 m): 50 people (4%); peak flow 1.27 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 8 queued within 5 m.
- E30: Exit exit_2_10 (L2, 1.837 m): 37 people (3%); peak flow 0.44 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E31: Exit exit_2_11 (L2, 1.837 m): 31 people (3%); peak flow 0.27 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E32: Exit exit_1_02 (L1, 1.831 m): 24 people (2%); peak flow 0.44 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 0 queued within 5 m.
- E33: Exit exit_1_08 (L1, 1.833 m): 22 people (2%); peak flow 0.27 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 0 queued within 5 m.
- E34: Exit exit_2_03 (L2, 0.8 m): 19 people (2%); peak flow 1.12 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E35: Exit exit_2_09 (L2, 1.837 m): 19 people (2%); peak flow 0.22 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E36: Exit exit_2_07 (L2, 0.8 m): 11 people (1%); peak flow 0.75 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E37: Exit exit_2_04 (L2, 0.8 m): 8 people (1%); peak flow 0.37 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E38: Exit exit_2_05 (L2, 0.8 m): 6 people (1%); peak flow 0.50 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E39: Exit exit_1_05 (L1, 2.0 m): 4 people (0%); peak flow 0.05 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 0 queued within 5 m.
- E40: Exit exit_2_06 (L2, 0.8 m): 3 people (0%); peak flow 0.25 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 1 queued within 5 m.
- E41: Exit exit_1_06 (L1, 2.0 m): 2 people (0%); peak flow 0.10 p/s/m over 10 s against the sim's 1.3 p/s/m maximum; max 0 queued within 5 m.
- E42: Unused exits (0 of 22): [].
- E43: Stair queues (max people queued within 5 m of a stair end): {'stair_1_2_901': 1, 'stair_1_2_903': 1, 'stair_2_3_901': 1, 'stair_2_3_903': 1, 'stair_3_4_901': 1}.
- E44: Screening, not a code review: egress width 34.4 m of exits for 1200 occupants; at 0.2 in per occupant (IBC 1005.3.2 as quoted in research/MAPPING_IDEAS.md, unverified) 6.10 m is needed: enough. Exits per level against occupant load: not checked (no factor in analysis/ or research/).
- E45: Screening, not a code review: accessible means of egress: 14 people end the run waiting at a stair refuge (rooms {'r_1903': 1, 'r_3902': 1, 'r_3903': 1, 'r_4901': 2, 'r_4902': 1, 'r_5901': 1, 'r_5902': 7}); no assisted evacuation plan is in the scenario
- E46: Single point of failure (room c_4_001): losing it strands 119 rooms (309 occupants by IBC load) and removes 0% of exit width (analysis/spof.py).
- E47: Single point of failure (room c_5_002): losing it strands 84 rooms (231 occupants by IBC load) and removes 0% of exit width (analysis/spof.py).
- E48: Single point of failure (room c_3_002): losing it strands 82 rooms (316 occupants by IBC load) and removes 0% of exit width (analysis/spof.py).
- E49: Single point of failure (room r_5254): losing it strands 29 rooms (88 occupants by IBC load) and removes 0% of exit width (analysis/spof.py).
- E50: Single point of failure (room r_5236): losing it strands 17 rooms (26 occupants by IBC load) and removes 0% of exit width (analysis/spof.py).
- E51: Pinch points: not computed (more than 150 rooms).

Principles: P1: Door flow is capped near 1.3 persons/s per m of clear width (SFPE hydraulic model, research/EVAC_PSYCHOLOGY.md s2); more or wider exits raise capacity. P2: Pre-movement is often the largest part of evacuation time; direct instructions from staff or a PA shorten it (BRANZ SR143, research/EVAC_PSYCHOLOGY.md s1). P3: Offices with trained wardens start moving fast; wardens sweep areas and direct others (SR143, research/EVAC_PSYCHOLOGY.md s1 and s5). P4: People who cannot use stairs wait at refuges unless a plan assigns helpers; only about 30% get help spontaneously (NIST NCSTAR 1-7, docs/SIM_INTERFACES.md). P5: Occupants know few exits (the one they came in by, maybe the nearest); signs and drills add known exits (research/EVAC_PSYCHOLOGY.md s5 persona table). P6: Hearsay about blocked exits spreads; an official message can correct it (research/EVAC_PSYCHOLOGY.md, sim rumor model). P7: A room or door whose loss strands other rooms or removes exit width is a single point of failure (analysis/spof.py). P8: Required safe egress time must stay below available safe egress time with margin (SFPE / ISO TR 16738 timeline: detection + alarm + pre-movement + travel). P9: Egress width capacity of about 0.2 in (5 mm) per occupant (IBC 1005.3.2 as quoted in research/MAPPING_IDEAS.md, unverified).

Model anthropic/claude-opus-5.5; LLM cost $0.1123. Screening checks are not a code review; simulated effects use the rule policy, not LLM occupants.
