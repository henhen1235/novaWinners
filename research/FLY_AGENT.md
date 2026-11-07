# A fruit fly as an evacuation occupant

Status: 2026-11-07. Code: `flyagent/`. Tests: `tests/test_flyagent.py`.

The point is a contrast for the demo: next to occupants that reason (LLM or rules), one
occupant whose choices come out of a wired brain, the adult fruit-fly connectome, with no
goals, beliefs or language. A second fly gets the same brain with scrambled wiring, so the
demo can show whether any of its behavior is due to the wiring at all.

This is a toy embodiment. A fly is 3 mm long, does not know what an exit is, and cannot read
a sign. What is real: the connectome, the simulator, which sensory neurons are driven, and
which descending neurons are read. What is ours: the mapping from building percepts to
sensory drive and from descending-neuron rates to the sim's options.

## What the sister projects contain

All five are the user's own work and share one stack.

| project | what it is | what we use |
|---|---|---|
| `~/projects/flyscore` (github.com/LordKarV/flyscore, MIT) | Benchmark: does a fly-brain simulator behave like a fly because of its wiring? 7 literature tests, each also run on scrambled wirings | the simulator (`flyscore.lif.ShiuLIF`), the connectome cache, the rewire null, its results |
| `~/projects/fly4` (drunkfly) | MaleCNS LIF brain driving the NeuroMechFly body (flygym/MuJoCo) closed loop every 10 ms; ethanol acts on synapse classes | walking drive inputs, DN readout set and the DN-to-steering formula (`loop.py transduce`) |
| `~/projects/fly3` (flyduet) | MaleCNS and FlyWire v783 in one schema, flyvis optic-lobe bridge, resumable LIF kernel | background only |
| `~/projects/fly2` (flydream) | Differentiable rate model on a connectome subgraph; "dreams" the stimulus that maximizes LPLC2 | background: confirms LPLC2 is wiring-specific in a rate model too |
| `~/projects/codefly` | MaleCNS fly that approves Claude Code edits via MN9 (feeding) and wanders via P9/DNa | same DN recipe as fly4 |

### Connectome

MaleCNS v1.0 (Janelia/Google, Sept 2026, CC-BY 4.0): 169,984 neurons after FlyScore drops
glia and fragments, 25.6M synapses. Edge weight = synapse count, signed by the presynaptic
transmitter (GABA and glutamate inhibitory, everything else excitatory; Shiu et al. 2024's
rule), scaled so the mean input per neuron matches Shiu's FlyWire calibration (one global
`w_scale`). Not hemibrain, not FlyWire: FlyScore and fly4 both run MaleCNS.

### Simulator

Shiu et al. 2024 (Nature 634:210) leaky integrate-and-fire network, reimplemented in Numba by
FlyScore (`flyscore/lif.py`): v rest/reset -52 mV, threshold -45 mV, tau_m 20 ms, tau_syn
5 ms, refractory 2.2 ms, synaptic delay 1.8 ms, 0.275 mV per synapse, dt 0.1 ms. Stimulated
neurons get Poisson kicks at a set rate. It reproduces Shiu's Brian2 output on FlyWire
(per-neuron rate r = 0.954 over the 458 active neurons). There is no adaptation, no
neuromodulation, no spontaneous activity.

### What FlyScore says about wiring vs behavior

FlyScore 4/7 for this model (raw passes 6/7; 40 trials of 1 s, 5 weight-shuffle and 5
degree-preserving rewire nulls):

- Sugar to MN9 (feeding), bitter suppressing it, and JO-C/E to aDN1 (grooming) pass and are
  wiring-specific: every scrambled wiring fails.
- Looming (LC4, LPLC2) to DNp01 (giant-fiber escape) passes, but 5 of 10 nulls also pass,
  all of them weight shuffles. The escape is carried by who connects to whom; the synapse
  weights add nothing measurable. Only the rewire null kills it.
- Water to MN9 fails on the male connectome.

So for the fly agent: the scrambled control is the degree-preserving **rewire**, because a
weight shuffle would leave the escape reflex intact and hide the effect.

## Sensorimotor mapping

### Percepts to sensory neurons

The sim's percepts have no direction yet, so `flyagent` computes egocentric bearings itself:
heading = toward the fly's target exit, else the exit it came in by; bearing to a hazard
(its room centroid) or an exit sign (its exit) from scene.json positions, straight line,
walls ignored. Positive bearing = left. Within 15 degrees of dead ahead or behind counts as
both sides. If a percept or option carries `bearing_deg`, that wins.

| percept | channel | neurons driven (MaleCNS types, per side) | rate | source |
|---|---|---|---|---|
| smoke (dist >= 5 m) | smoke level 1 | CO2 ORNs `ORN_V` (ab1C, Gr21a/Gr63a) + arista hot cells `TRN_VP2` | 75 Hz | CO2 avoidance: Suh et al. 2004; hot cells: Gallio et al. 2011 |
| smoke < 5 m, or fire | smoke level 2 | same | 150 Hz | |
| >= 3 people moving in view | loom | `LC4`, `LPLC2` | 100 Hz | von Reyn et al. 2014; Ache et al. 2019; FlyScore B6 rate |
| nearest exit sign | light, its side | inner photoreceptors `R7*`, `R8*` | 100 Hz | phototaxis via R7/R8: Yamaguchi et al. 2010 |
| alarm, urgent shout | sound, both sides | Johnston's organ `JO-A*`, `JO-B*` | 140 Hz | Kamikouchi et al. 2009; FlyScore B5 rate |

Always on: walking drive on `LC9`, `LC31a` (50 Hz) and `PFL3` (30 Hz), both sides, as in
fly4's sober walking fly. Without it the forward and steering neurons are silent at rest.
The people percept carries no position, so a crowd looms on both sides.

### Descending neurons to motor primitives

Readout per side: DNp01 (giant fiber, escape takeoff), MDN (moonwalker, backward), DNp09 =
P9 and DNg97 = oDN1 (forward), DNa01 and DNa02 (steering; Rayshubskiy et al. 2020). Rates
are compared with the same wiring's no-stimulus rates (fly4 `transduce`):

- turn = d(DNa01 L-R) + 0.5 d(DNa02 L-R), positive = turn left;
- forward ratio = (P9 + 0.5 oDN1) / the same at baseline;
- primitive, first match: escape if mean GF >= 50 Hz; backward if mean MDN >= 10 Hz;
  freeze if forward ratio < 0.4 and |turn| < 5 Hz; turn left/right if |turn| >= 5 Hz;
  else forward.

### Primitives to sim options

Each EVACUATE or FOLLOW option gets the bearing of its exit. Forward wants 0 degrees; a turn
wants 30 to 90 degrees to that side (|turn| / 30 Hz, fly4's full-turn scale); escape and
backward want 135 degrees away from a one-sided threat, else 180. The option with the
nearest bearing wins (ties: shorter route). Freeze picks CONTINUE_TASK (or WAIT, HIDE).
TURN_BACK, when offered, takes backward. With no scene geometry any non-freeze primitive
takes the shortest route. The fly never speaks. Thresholds are constants in
`flyagent/decide.py`; they were set before looking at the table and are not fit to it.

## Speed: offline table, lookup at runtime

The full simulation is far too slow to call per decision. Measured on this Mac (M-series,
8 cores, 16 GB) while other agents were running benchmarks (load average 45 to 255), at
`nice -n 10` with 4 trials as 4 threads:

- a 300 ms window (4 trials) took 3.4 to 21 s of wall time; 28.7 s of wall time per
  simulated second on average over the real-wiring pass (1,446 s for 168 cells). An earlier
  probe on a quieter machine needed 1.5 to 5.6 s for 3 trials of 300 ms. The cost grows
  with how much of the network the stimulus wakes up (looming plus smoke is slowest).
- So one live decision would cost seconds. Instead `python -m flyagent table` runs the
  real LIF once per cell of a stimulus grid and stores the DN rates in
  `flyagent/response_table.json` (64 KB, 168 cells x 2 wirings, 12 DN rates each, with
  provenance: connectome, LIF constants, FlyScore commit, seeds, window, trials, rates).
  Real pass 24 min, scrambled pass 18 min.
- Grid: smoke {off, L/R/both x 2 levels} x loom {off, L, R, both} x light {off, L, R} x
  sound {off, on} = 168 cells. Window 300 ms, 4 trials, seed 7.
- Runtime decide(): table lookup plus a little geometry. 382 decisions in three sim runs:
  median 0.52 ms, p99 24 ms, max 51 ms (the slow ones are the first call, which reads
  the table and scene, and scheduler stalls on the loaded machine). Every DecisionRecord
  carries the table key, the stimulus cell, the raw DN rates, the baseline, the motor
  command, the primitive and the table provenance under `record.fly`.

Not done: a reduced rate model restricted to the relevant pathways. The table was enough.

## Results from the table

No-stimulus baseline, real wiring (Hz): P9 18 L / 55 R, oDN1 10 / 13, DNa01 16 / 8,
DNa02 1 / 3, GF 3 / 0, MDN 0. The baseline is lopsided (fly4 saw the same), which is why
everything is read relative to it.

| stimulus (alone) | real wiring | scrambled (rewire) |
|---|---|---|
| none | forward | freeze (all DNs silent) |
| alarm (JO-A/B) | forward; GF 32 Hz, below the 50 Hz escape line | freeze |
| loom left | escape: GF 234 Hz, turn -25 Hz (to the right, away) | freeze |
| loom right | escape: GF 250 Hz, turn +12 Hz (to the left, away) | freeze |
| loom both | escape: GF 346 Hz | freeze |
| smoke weak, left or right | freeze: forward drive down to 28 to 37% | freeze |
| smoke strong, left / right / both | weak right turn (-5 to -6 Hz) with forward drive at 23 to 27% | freeze |
| exit sign (R7/R8), left or right | forward, |turn| < 2.1 Hz: no phototaxis | freeze |

Over all 168 cells the real wiring gives escape 126 (every cell with looming), turn right
27, freeze 9, forward 6. The scrambled wiring gives freeze in all 168: under the
degree-preserving rewire, none of these inputs (nor the walking drive) reaches the
walking, steering or escape DNs within 300 ms. Primitives differ in 159 of 168 cells.

What this says, honestly:

- The looming escape is real and lateralized: the giant fiber fires hard and the steering
  DNs turn the fly away from the side the loom comes from. It is wired: the rewire kills it
  (FlyScore's rewire nulls also fail B6; its weight shuffles do not).
- Smoke (CO2 ORNs plus hot cells) suppresses walking but is not steered: left and right
  smoke both give the same small right turn. There is no lateralized smoke avoidance in
  this model within 300 ms.
- The exit sign does nothing. Photoreceptors are many synapses from the DNs, and
  histamine (their transmitter) counts as excitatory under Shiu's sign rule though it is
  inhibitory in the fly. A fly that "reads" exit signs would have to be imposed; we did not.
- The scrambled fly is not a subtly different fly; it is a fly whose inputs never reach
  its motor neurons. That is the strongest possible "wiring matters", and also the least
  subtle. A weight-shuffle fly would keep the escape (FlyScore B6) and is the obvious
  next control.

## Behavior in a run

`flyagent/examples/tiny_office_fire_flies.json` is `sim/examples/tiny_office_fire.json`
(fire in Office N2 at t=0, door impassable after 60 s, false rumor at 40 s that the Main
Entrance is blocked) with each occupant's model drawn as 50% rule-based, 25% fruit_fly,
25% fruit_fly_scrambled. The non-fly occupants used the sim's rule-based policy (no LLM
calls, no OpenRouter key used).

    python -m sim run --scenario flyagent/examples/tiny_office_fire_flies.json \
        -o out/sim/flies --seed 42 --decide flyagent --no-llm

| seed | occupant | n | out | median exit (s) | last exit (s) | decisions per agent |
|---|---|---|---|---|---|---|
| 42 | fruit_fly | 6 | 6 | 21.0 | 52.5 | 2.3 |
| 42 | fruit_fly_scrambled | 9 | 0 | - | - | 15.6 |
| 42 | rule-based | 15 | 15 | 60.5 | 232.0 | 6.4 |
| 7 | fruit_fly | 10 | 10 | 26.0 | 45.5 | 2.3 |
| 7 | fruit_fly_scrambled | 6 | 0 | - | - | 15.7 |
| 7 | rule-based | 14 | 13 | 92.0 | 127.5 | 8.8 |
| 1 | fruit_fly | 9 | 8 | 22.5 | 31.5 | 3.8 |
| 1 | fruit_fly_scrambled | 5 | 0 | - | - | 15.4 |
| 1 | rule-based | 16 | 16 | 96.5 | 141.0 | 7.2 |

How the flies behave:

- Real flies leave first (median exit 21 to 26 s vs 60 to 97 s for the rule-based
  occupants) because they have no pre-movement delay: the alarm reaches them, the walking
  drive keeps P9 up, and "forward" carries them along their heading. Caveat: heading is
  defined as toward the exit they came in by, so "forward" means "back the way I came".
  That is our embodiment choice, not the connectome's; it also happens to be the familiar
  exit bias people show.
- The rumor does nothing to a fly: it cannot hear words, only the alarm sound.
- Flies that see a crowd moving (3 or more people) escape: the giant fiber fires, and
  they take the exit most behind them.
- Flies near the fire freeze or crawl. In seed 1 the one fly that did not get out saw the
  fire 2.7 m away at t=0: all 16 of its decisions (t=0 to 300 s) read strong smoke on its
  right, forward drive stayed below 40% and it froze for the whole run.
- Scrambled flies never move: every decision is freeze (CONTINUE_TASK), 0 of 20 got out
  over the three seeds.

The mapping is ours and coarse; the claim we can make is narrow: with the real wiring the
same sensory drive produces walking, escape away from looms and smoke-induced slowing; with
the wiring scrambled it produces nothing.

## What the sim could add

- Keep unknown persona names (`fruit_fly`) instead of resolving them to `regular`; today the
  fly is selected by the occupant `models` field, which the sim passes through unchanged.
- Give percepts and options a bearing (`bearing_deg`, positive = left of the agent's heading)
  or positions, and the agent's heading. Then flyagent needs no scene.json and no argv sniff.
- A position on `people_moving` (or per-person percepts), so a crowd looms from one side.

## Sources

Shiu et al. 2024, Nature 634:210 (LIF whole-brain model). MaleCNS v1.0, male-cns.janelia.org.
von Reyn et al. 2014, Nat Neurosci (giant fiber); Ache et al. 2019, Curr Biol (LPLC2);
Bidaye et al. 2014, Science (MDN); Bidaye et al. 2020, Neuron (P9); Sapkal et al. 2024 (oDN1);
Rayshubskiy et al. 2020, bioRxiv (DNa01/DNa02 steering); Suh et al. 2004, Nature (CO2
avoidance); Gallio et al. 2011, Cell (hot cells); Kamikouchi et al. 2009, Nature (JO);
Yamaguchi et al. 2010, PNAS (R7/R8 phototaxis); Hampel et al. 2015, eLife (JO-C/E
grooming). Code reused: `~/projects/flyscore` (simulator, cache, rewire), `~/projects/fly4`
(walking drive, DN readout and transduce formula).
