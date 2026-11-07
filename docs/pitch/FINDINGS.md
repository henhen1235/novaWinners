# What the simulator found, and what it says to change

How it works, for any building:
1. Run the evacuation.
2. Compute an evidence pack in code: each person's time budget (pre-movement, walking,
   queueing), who is left and the reason in their last decision, exit use and flow
   against the door limit, the smoke clock, and single points of failure.
3. A language model proposes findings and changes. Each must cite that evidence and
   real ids from the building model.
4. The simulator re-runs every change it can apply and measures it. It can apply
   staffing, signage, a PA message, an assisted evacuation plan, a wider door, and a
   door on an outside wall turned into an exit. It also runs all applicable changes
   together. Anything else is marked "not tested in simulation".

The screening checks are not a code review. Measured effects come from rule-based
occupants over 3 seeds (42-44), compared with a fresh baseline on the same seeds, and
are medians. With 3 runs, a difference of one person can be chance.

## Mudge House (dorm), night fire, 305 people

What goes wrong:
- 303 of 305 get out. The 2 left are the mobility-impaired residents. They wait at
  stair refuges on floor 3 (stair A300B) and floor 2 (stair C200D), and nobody is
  assigned to help them.
- Time is lost before people move, not at the doors. Median pre-movement is 71 s, while
  the median walk is 33 s. In the recorded run the last person leaves at 9:02.
- Smoke in the fire room (storage A105, floor 1) reaches head height at about 2:01
  (closed-room estimate), 7 minutes before the last person is out.
- Exits are not the bottleneck. There is 18.5 m of exit width, no queues, and all flows
  are well below 1.3 persons/s per m. Exits 1_08, 1_10 and 1_12 are never used.
- Two rooms carry many others: losing B19 cuts off 30 rooms and losing A300 cuts off 22.

Headline: Mudge's layout is not the bottleneck; pre-movement and help for people who
cannot use stairs are.

Changes for the safety team (baseline over 3 runs: 303 of 305 out, 90% out by 3:27,
last out 8:20):
1. An assisted evacuation plan, with named helpers for the floor 2 stair C200D and
   floor 3 stair A300B refuges. Tested in 3 runs: 303 to 304 of 305 out, 90% out
   3:27 to 3:18, last out 8:20 to 8:20.
2. A fire warden sweeping floor 3 from room A300. Tested in 3 runs: no change
   (303 of 305, last out 8:20).
3. Signs toward exit 1_08, the 2.0 m floor 1 door from corridor A107 to outside, which
   nobody used. Tested in 3 runs: no change.
4. Also tested: a direct PA message at the alarm. No change, because most residents
   have not noticed the alarm yet when it plays.

Changes for the architect:
1. A new exit at the floor 2 door between room A211B and corridor A203B. It needs an
   outside stair. Tested in 3 runs: 303 to 303 out, 90% out 3:27 to 3:22, last out
   8:20 to 8:20.
2. A new exit at the floor 3 door from room A310 into A300. A310 is the room of one of
   the mobility-impaired residents. It needs an outside stair. Tested in 3 runs: no
   change.
3. Widen exit 1_02, the floor 1 door from stair C100C to outside, from 0.92 to 1.38 m.
   Tested in 3 runs: no change. The door never queues.
4. A new exit at the floor A (basement) door of stair C19F. It needs an outside stair.
   Tested in 3 runs: no change.

All 8 testable changes together, in 3 runs: 303 to 304 of 305 out, 90% out 3:27 to
3:03, last out 8:20 to 6:22.

Model: anthropic/claude-opus-5.5. Analysis cost $0.08 for this building.

## Tepper (business school), weekday fire, 1200 people

Fire in classroom 3500 (floor 3) at the alarm, smoke in stairwell 2902 (floor 2) from 45 s.
1200 occupants, about a third of the plans' design load. Rule-based occupants, seed 42.
Recommendations below come from the language model reasoning over the evidence pack only;
they were not re-run in the simulator (no time before the demo), so they are marked
"not tested".

What goes wrong:
- 1181 of 1200 get out; the last person leaves at 10:48. Smoke reaches head height in
  stairwell 2902 at about 1:44 and in classroom 3500 at about 3:36, so both margins are
  negative by more than 7 minutes.
- Of the 19 left inside, 16 are mobility-impaired: only 34 of 50 got out. 14 wait at stair
  refuges with no plan to help them (7 of them at stairwell 5902 on floor 5), and 2 were
  still queued for the recalled elevator on floor 5.
- A few narrow exits run at their limit: exit 2_12 (a 1.02 m floor 2 outside door) peaked
  at 1.37 persons/s per m, above the 1.3 cap; exit 1_01 (1.10 m, floor 1) had the longest
  queue (8 people); exit 2_02 hit the cap.
- Use is uneven: two floor 2 exits (2_01 and 2_13) carried 34% of everyone, while floor 1
  exits 1_05 and 1_06 were almost unused.
- The upper floors hang on single corridors: losing corridor 4-C001, 5-C002 or 3-C002 cuts
  off between 82 and 119 rooms each.
- Two people never left: one still looking for information in stairwell 2902, one in the
  floor 3 lobby 3401 unsure the alarm was real.

Headline: Tepper's exits are mostly adequate; the people it loses are the ones who cannot
use stairs, and its upper floors depend on single corridors.

Changes for the safety team (not tested):
1. An assisted evacuation plan with named helpers for the stair refuges, starting with
   stairwells 5902, 5901 (floor 5) and 4901 (floor 4).
2. Wardens on floor 5 to sweep corridor 5-C002 and redirect the people heading for the
   recalled elevator.
3. A direct PA instruction at the alarm, aimed at the people still unsure or looking for
   information.
4. Signs toward floor 1 exit 1_05 (2.0 m, almost unused) to take load off floor 2.

Changes for the architect (not tested):
1. Widen exit 2_12, the 1.02 m floor 2 outside door, to about 1.5 m (+47% capacity); it
   ran above the flow cap.
2. Widen exit 1_01, the 1.10 m floor 1 outside door, to about 1.65 m; it had the longest
   queue.
3. A second way off floor 5 that bypasses corridor 5-C002: make the 1.77 m door 5_003 by
   stairwell 5901 an exit to a new outside stair.
4. Make the 0.92 m floor 1 door 1_094 at stairwell 1903 an at-grade exit, so people
   reaching that stair refuge can leave directly.

Model: anthropic/claude-opus-5.5. Analysis cost $0.11 for this building.
