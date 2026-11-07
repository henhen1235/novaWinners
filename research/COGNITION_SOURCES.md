# Sources for docs/AGENT_COGNITION.md (verification pass)

Checked 2026-11-07 in about 30 minutes, for the [verify] items and every other
work cited in docs/AGENT_COGNITION.md. Tags, as in research/EVAC_PSYCHOLOGY.md:

- [V] checked today against the paper's own text or abstract.
- [V sec] checked only through a citing paper or index record.
- [U] from memory or a snippet; check before putting it on a slide.

Already verified elsewhere and not repeated: Lovreglio et al. 2019 (EVAC_PSYCHOLOGY.md),
Xie et al. 2012 sign noticing 38% (MAPPING_IDEAS.md), Kuligowski and Mileti 2009,
Kuligowski and Hoskins TN 1664, Sime 1985 (EVAC_PSYCHOLOGY.md).

## 1. Explanation faithfulness (Turpin, Lanham, and our "remove evidence, re-run" test)

| Work | Finding | Fair use? | Usable numbers |
|---|---|---|---|
| Turpin, Michael, Perez, Bowman. "Language Models Don't Always Say What They Think: Unfaithful Explanations in Chain-of-Thought Prompting". NeurIPS 2023. arXiv 2305.04388 [V] | Biasing features (e.g. reordering options so the answer is always "(A)") change answers, and the CoT never mentions them; models write CoT that rationalizes the biased answer. GPT-3.5 and Claude 1.0 on 13 BIG-Bench Hard tasks | Yes, with one fix: these CoTs were written at decision time and were still unfaithful. Turpin supports rule 3 (test by intervention). It does not support rule 1 as a cure, since recording at decision time does not make reasons faithful | Accuracy drops by up to 36% under bias |
| Lanham et al. (30 authors, Anthropic). "Measuring Faithfulness in Chain-of-Thought Reasoning". arXiv 2307.13702, 2023 [V abstract] | Intervene on the CoT (adding mistakes, paraphrasing; also early answering and filler tokens [U, not in the abstract]) and watch the answer. Reliance on CoT varies widely by task. Larger, more capable models give less faithful reasoning on most tasks | Yes. Adds a caution: faithfulness is model- and task-specific, so measure it per model, as the doc already plans | Inverse scaling: bigger model, less faithful (qualitative) |
| Atanasova, Camburu, Lioma, Lukasiewicz, Simonsen, Augenstein. "Faithfulness Tests for Natural Language Explanations". ACL 2023 (short), pp. 283-294 [V] | Counterfactual test: edit the input so the prediction flips and check whether the explanation mentions the edit. Input reconstruction test: rebuild input from stated reasons and check the prediction holds | Yes, this is the "counterfactual test" our method belongs to | Code: github.com/copenlu/nle_faithfulness |
| Madsen, Chandar, Reddy. "Are self-explanations from Large Language Models faithful?". Findings of ACL 2024. arXiv 2401.07927 [V abstract] | Self-consistency checks for three explanation types: counterfactual, feature attribution, and redaction (remove the words the model says matter, re-run, see if the prediction changes). Faithfulness depends on explanation type, model and task | Yes. Our test is the redaction check applied to evidence ids | No pooled rate; "self-explanations should not be trusted in general" |
| Matton, Ness, Guttag, Kiciman. "Walk the Talk? Measuring the Faithfulness of Large Language Model Explanations". ICLR 2025 (spotlight). arXiv 2504.14150 [V sec, abstract via search] | "Causal concept faithfulness": compare the concepts an explanation says mattered with those that change the answer when an auxiliary LLM edits them; Bayesian hierarchical model at dataset and question level. On medical QA, explanations made misleading claims about which evidence drove the answer | Yes, closest match to our method. Their hierarchical estimate is a better statistic than our 3-run majority | Code: github.com/kmatton/walk-the-talk |
| DeYoung et al. "ERASER: A Benchmark to Evaluate Rationalized NLP Models". ACL 2020. arXiv 1911.03429 [V sec] | Defines comprehensiveness (prediction change when rationale is removed) and sufficiency (prediction kept with only the rationale) | Yes. Our "flip when removed" is a binary comprehensiveness test; we lack the sufficiency half | Metric definitions |
| Hooker, Erhan, Kindermans, Kim. "A Benchmark for Interpretability Methods in Deep Neural Networks" (ROAR). NeurIPS 2019 [U] | Removing features creates out-of-distribution inputs, so a changed output may reflect the odd input, not the feature's importance | Caveat for us: deleting a percept can leave the prompt incoherent (e.g. an option that refers to a removed sign) | None |

Answer to the question "is it established, and what is it called": yes. The
family is called erasure, occlusion or leave-one-out attribution; in NLP the
removal metric is "comprehensiveness" (ERASER), for LLM self-explanations it is
the "redaction" or "counterfactual" faithfulness test (Madsen 2024; Atanasova
2023), and the concept-level version is "causal concept faithfulness" (Matton
2025). What may be new is applying it to agent decisions in a simulation with
structured evidence ids; I found no prior paper doing that, but did not search
exhaustively [U].

## 2. Introspection (Nisbett and Wilson)

| Work | Finding | Fair use? | Notes |
|---|---|---|---|
| Nisbett, Wilson. "Telling more than we can know: Verbal reports on mental processes". Psychological Review 84(3):231-259, 1977. doi 10.1037/0033-295X.84.3.231 [V sec, index records] | People often cannot report the causes of their own choices; their explanations match what observers would guess from plausible theories rather than what actually influenced them | Yes | Counterpoint worth one line: Ericsson and Simon, "Verbal reports as data", Psychological Review 87(3):215-251, 1980 [V sec] argue concurrent reports of what is being attended to are valid data, retrospective reports of causes less so. This supports rule 1 (record at decision time) for humans, while Turpin shows the LLM analogue still needs rule 3 |

## 3. PADM (Lindell and Perry 2012)

| Work | Finding | Fair use? | Parameters |
|---|---|---|---|
| Lindell, Perry. "The Protective Action Decision Model: Theoretical Modifications and Additional Evidence". Risk Analysis 32(4):616-632, 2012. doi 10.1111/j.1539-6924.2011.01647.x [V, full text read] | Environmental cues, social cues and warnings feed three predecisional processes (exposure/reception, attention, comprehension/interpretation), then three core perceptions (threat, protective action, stakeholder), then decision stages, then a behavioral response: information search, protective response (problem-focused coping) or emotion-focused coping. Uncertainty at any stage triggers information seeking; there is a feedback loop as new cues arrive | Yes, "decisions are cue-driven" and the five-question JSON are fair. Bonus: the paper says risk identification "is equivalent to what Lazarus and Folkman call primary appraisal", a direct bridge to our appraisal stage | None numeric. The paper says "there appears to be no research on the duration of psychological preparation" (milling), so milling time must come from pre-movement data, not PADM |

Exact stage questions (quoted from the paper):

1. Risk identification: "Is there a real threat that I need to pay attention to?"
2. Risk assessment: "Do I need to take protective action?"
3. Protective action search: "What can be done to achieve protection?"
4. Protective action assessment: "What is the best method of protection?"
5. Protective action implementation: "Does protective action need to be taken now?"

Information-seeking stages: information needs assessment ("What information do
I need to answer my question?"), communication action assessment ("Where and
how can I obtain this information?"), communication action implementation ("Do
I need the information now?").

Stakeholder perceptions: rated on expertise, trustworthiness and protection
responsibility; "stakeholders differ significantly in these perceived
characteristics". Stakeholder groups named: authorities, evaluators, watchdogs
(news media), industry, households; in practice people "rely on the mass media
and peers" when authorities are unreachable [V]. No fixed credibility ranking
is given.

Our JSON maps cleanly: threat_real = stage 1, need_action = 2,
options_considered = 3, choice = 4, when = 5; SEEK_INFO and when=after_info
are the information-seeking branch. EVAC_PSYCHOLOGY.md marks the question
wording [U]; it can now be [V].

## 4. Arousal and attention (Easterbrook)

| Work | Finding | Fair use? | Parameters |
|---|---|---|---|
| Easterbrook. "The effect of emotion on cue utilization and the organization of behavior". Psychological Review 66(3):183-201, 1959. doi 10.1037/h0047707, PMID 13658305 [V sec] | Rising emotional arousal reduces the range of cues used: peripheral cues drop first, central task-relevant cues are kept; helpful on simple tasks, harmful once central cues are lost too | Partly. The doc's "attention budget shrinks" implies all cues drop evenly. Easterbrook predicts reallocation: threat and task-relevant cues kept, peripheral ones lost | None numeric (a theoretical review) |
| Sorensen, Barratt. "Is threat the only modulator of attentional selectivity? Redefining the Easterbrook hypothesis". Frontiers in Psychology 5:1020, 2014. doi 10.3389/fpsyg.2014.01020 [V] | Arousal raises selectivity (higher weight on targets, better target-distractor separation) but lowers visual short-term memory capacity; threat adds its own weighting on top. Cites van Steenbergen et al. 2011: arousal necessary but not sufficient, negative content needed for narrowing | Supports a priority-weighted mapping, not a uniform cut | Qualitative |
| Mather, Sutherland. "Arousal-biased competition in perception and memory". Perspectives on Psychological Science 6(2):114-133, 2011. PMC3110019 [V sec; volume and pages U] | Arousal strengthens whatever already wins the competition for attention ("winner-take-more, loser-take-less") | Yes, as the mechanism for the mapping below. Note a 2019 replication (Asgeirsson and Nieuwenhuis, Attention, Perception and Psychophysics) challenged it [V sec] | Qualitative |
| Tucker, Marsh, Gifford, Lu, Luh, Astur. "The effects of information and hazard on evacuee behavior in virtual reality". Fire Safety Journal 99:1-11, 2018. doi 10.1016/j.firesaf.2018.04.011 [V sec, abstract] | VR fire pilot: obstacle information shortened evacuation time but did not change exit choice; effect on self-reported anxiety only marginal. Argues that anxiety narrows attention so useful information is missed | Evacuation-specific, but the narrowing claim is the authors' framing, not a measured effect | None usable |
| Digital escape-route signage study (Springer 2017, doi 10.1007/978-3-319-58472-0_11) [U, details not read] | Under evacuation conditions participants focused on searching for signs and attended less to other environmental cues ("tunneling") | Shows narrowing onto the task, which can raise sign use when searching for exits. Supports priority weighting | None |

Verdict: the hypothesis is still cited and qualitatively supported, with two
qualifications that change our design: narrowing needs negative or threat
content, not arousal alone, and it reallocates attention toward high-priority
cues rather than shrinking it evenly. No paper gives a numeric arousal-to-cue
mapping for evacuees.

## 5. Appraisal theory

| Work | Finding | Fair use? | Usable content |
|---|---|---|---|
| Lazarus. Emotion and Adaptation. Oxford University Press, 1991 [U, book not read]; core themes checked in Lazarus, "From psychological stress to the emotions: a history of changing outlooks", Annual Review of Psychology 44:1-22, 1993 [V] | Primary appraisal (is my goal at stake: goal relevance, goal congruence, type of ego-involvement) and secondary appraisal (what can I do: blame or credit, coping potential, future expectancy) [primary list U; secondary list V sec]. Core relational themes [V]: anxiety "facing uncertain, existential threat"; fright "an immediate, concrete, and overwhelming physical danger"; hope "fearing the worst but wanting better"; relief "a distressing goal-incongruent condition that has changed for the better or gone away". Frustration alone is not anger; anger needs blame of an agent who could have acted otherwise [V] | Yes | Anxiety vs fear split = uncertain vs immediate threat. Do not label "anger" without a blameworthy agent |
| Scherer. "The dynamic architecture of emotion: Evidence for the component process model". Cognition and Emotion 23(7):1307-1351, 2009. doi 10.1080/02699930902928969 [V abstract] | Sequential stimulus evaluation checks in four groups: relevance (novelty, intrinsic pleasantness, goal relevance), implications (causal attribution, outcome probability, discrepancy from expectation, goal conduciveness, urgency), coping potential (control, power, adjustment), normative significance [groups V sec; sub-checks U] | Yes | Predicted profiles [U, from memory of Scherer 2001]: fear = sudden, obstructive, high urgency, low power; anxiety/worry = lower suddenness, medium urgency, low power; despair = obstructive, very low control and power; irritation = obstructive with some control |
| Ortony, Clore, Collins. The Cognitive Structure of Emotions. Cambridge University Press, 1988 [V sec, via Durupinar 2016 Algorithm 1] | Prospect-based emotions: hope (pleased about prospect of desirable event), fear (displeased about prospect of undesirable event), satisfaction, fears-confirmed, relief (undesirable prospect disconfirmed), disappointment. Compounds, e.g. anger = reproach + distress | Yes, standard for computational agents | Intensity driven by desirability and likelihood |
| Gratch, Marsella. "A domain-independent framework for modeling emotion" (EMA). Cognitive Systems Research 5(4):269-306, 2004. doi 10.1016/j.cogsys.2004.02.002 [V, full text] | Appraisal variables: relevance, desirability, causal attribution (agency, blame/credit), likelihood, unexpectedness, urgency, ego involvement, coping potential (controllability, changeability, power, adaptability). Rules: hope if desirability > 0 and likelihood < 1; fear if desirability < 0 and likelihood < 1; joy/distress when likelihood = 1; intensity = abs(desirability x likelihood) | Yes. Best citation for a computed, product-form appraisal-to-emotion mapping | intensity = abs(D x P) |

Two of our appraisal variables are not appraisal dimensions in these theories:
"social proof" is a PADM social cue (and the Kinateder/Comunale herding
evidence in EVAC_PSYCHOLOGY.md) that should feed threat certainty, and
"responsibility" in appraisal theory means agency or blame. Our meaning (role,
dependants) matches PADM "protection responsibility" or OCC "fortunes of
others" better.

## 6. Emotion contagion in crowd and evacuation models

| Work | Finding | Fair use? | Parameters |
|---|---|---|---|
| Bosse, Hoogendoorn, Klein, Treur, van der Wal, van Wissen. "Modelling collective decision making in groups and crowds: Integrating social contagion and interacting emotions, beliefs and intentions". Autonomous Agents and Multi-Agent Systems 27(1):52-84, 2013. doi 10.1007/s10458-012-9201-1 (ASCRIBE; open access) [V, full text] | One contagion equation for emotions, beliefs and intentions; fear modulates openness to beliefs. Fitted to the 4 May 2010 Dam Square (Amsterdam) memorial panic: 35 people traced from TV footage over seconds 11 to 17, error = mean Euclidean distance to real positions; full ASCRIBE beat a no-move baseline, Helbing social force, and ASCRIBE without contagion (repeated-measures model effect F(1.4, 48.2) = 5.7) | Yes for the equation. "Validated" overstates it: one incident, 35 people, 6 s, parameters tuned to that clip | Equations below; most personal parameters fixed at 0.5 defaults (Table 4), global ones tuned; channel alpha = 1 - 1/(1 + exp(-sigma (d - tau_d))) |
| Bosse, Duell, Memon, Treur, van der Wal. "Agent-Based Modeling of Emotion Contagion in Groups". Cognitive Computation 7:111-136, 2015. doi 10.1007/s12559-014-9277-9 [V sec] | The group-level absorption and amplification model ASCRIBE builds on | Cite with ASCRIBE | Same equation |
| Durupinar, Gudukbay, Aman, Badler. "Psychological Parameters for Crowd Simulation: From Audiences to Mobs". IEEE TVCG 22(9):2145-2159, 2016. doi 10.1109/TVCG.2015.2501801 [V, full text] | OCEAN personality, OCC appraisal (22 categories mapped to PAD), and a threshold dose contagion model (Dodds and Watts): an agent sees an expressive neighbour within 4 m and a 120 degree cone, accumulates doses over the last k steps, and is infected past its susceptibility threshold. Empathy from OCEAN; expressiveness threshold falls with extraversion; decay rate tied to neuroticism | Yes for the OCEAN mapping. Its contagion mechanism did worse than ASCRIBE against footage (Tsai 2013, on the 2010 thesis version) | empathy = 0.354 O + 0.177 C + 0.135 E + 0.312 A + 0.021 N (from Jolliffe and Farrington's BES correlations), traits in [-1, 1]; dose ~ N(0.1, 0.01); k = 10; 4 m; 120 degrees. Authors say values were "adjusted empirically" |
| Tsai, Fridman, Bowring, Brown, Epstein, Kaminka, Marsella, Ogden, Rika, Sheel, Taylor, Wang, Zilka, Tambe. "ESCAPES: Evacuation Simulation with Children, Authorities, Parents, Emotions, and Social comparison". AAMAS 2011, pp. 457-464 [V sec] | LAX terminal evacuation; families search for members before leaving; contact with security staff calms agents; fear and knowledge spread | Yes, as precedent for warden calming and family search | Rule-based; no fitted parameters |
| Tsai, Bowring, Marsella, Tambe. "Empirical evaluation of computational fear contagion models in crowd dispersions". Autonomous Agents and Multi-Agent Systems 27(2):200-217, 2013. doi 10.1007/s10458-013-9220-6 (conference version IVA 2011, LNCS 6895:384-397) [V abstract] | Put the thermodynamic (Bosse) and epidemiological (Durupinar thesis) models in ESCAPES and compared with traced paths from the Amsterdam video and a second video (Greek protest, tear gas). The thermodynamic model reproduced the scene better; graded fear and proximity-based contagion were key | Yes. This is the "validation against real footage" answer | Graded (continuous) fear plus proximity channel beats threshold infection |
| van Haeringen, Gerritsen, Hindriks. "Emotion contagion in agent-based simulations of crowds: a systematic review". Autonomous Agents and Multi-Agent Systems 37(1), 2022. doi 10.1007/s10458-022-09589-z [V sec] | Review of crowd contagion models; via the 2023 paper: empirical validation is lagging | Cite as the state of the field | None |
| van Haeringen, Gerritsen. "Simulating Panic Amplification in Crowds via Density-Emotion Interactions". AAMAS 2023, pp. 1895-1903 [V, full text] | ASCRIBE's amplification has no stated psychological basis; psychology describes contagion as convergence. Replaces it with personal-space violation and pushing; re-runs Dam Square with 17,750 agents; escalation only above about 1.2 p/m2 | Yes, and it argues against using ASCRIBE amplification | delta (susceptibility) = 0.35 O + 0.18 C + 0.14 E + 0.31 A + 0.02 N; expressivity = 0.14 O - 0.02 C + 0.32 E + 0.11 A + 0.29 N (Gross and John); regulation = 0.17 O + 0.22 C + 0.19 E + 0.45 A - 0.23 N (Baranczuk). Scream radius 10 m and flee threshold are self-described guesses |

## 7. Milling, information seeking and warning source credibility

| Work | Finding | Fair use? | Parameters |
|---|---|---|---|
| Wood, Mileti, Bean, Liu, Sutton, Madden. "Milling and Public Warnings". Environment and Behavior 50(5):535-566, 2018. doi 10.1177/0013916517709561 [V sec, abstract] | Milling = searching for and confirming information before acting (Emergent Norm Theory). Longer, fuller mobile warning messages reduced milling and shortened the delay | Yes for the concept. Community mobile alerts, not building fires | Direction only: more complete warnings, less milling |
| Mileti, Peek. "The social psychology of public response to warnings of a nuclear power plant accident". Journal of Hazardous Materials 75(2-3):181-194, 2000 [V sec; PDF is scanned, stages not read] | Warning response process: hear, understand, believe, personalize, respond, with confirmation seeking between stages; content (source, consistency, certainty, guidance, location) and repetition raise response [stage list U] | Yes as background | None |
| Lindell and Perry 2012 (above) [V] | Milling explicitly described: people "seek confirmation that a danger exists, obtain further information ... and relay warnings to peers"; no research on its duration | Yes | None |
| Kuligowski and Mileti 2009; Kuligowski and Hoskins NIST TN 1664 (see EVAC_PSYCHOLOGY.md) [V] | In buildings, more information seeking goes with longer delay (2009); in a high-rise office fire, information seeking alone did not drive delay overall, waiting, helping and preparation did (TN 1664) | Use these for building fires, not Wood 2018 | Do not make SEEK_INFO the main source of delay |
| Source credibility | PADM rates sources on expertise, trustworthiness and protection responsibility and finds they differ, but gives no ranking [V]. Building data: retail staff switching to evacuation mode got 500 customers out in 2 min 45 s (SR143, in EVAC_PSYCHOLOGY.md) [V]; industry summaries say in-person staff direction and voice messages beat bells [U] | The doc's fixed order "own observation > official PA > warden/staff > known person > stranger" is not from Lindell and Perry, and putting PA above an in-person warden conflicts with our own persona table (employees trust warden first) | Make weights per persona from the three PADM attributes |

## 8. Affiliation (Sime 1985)

| Work | Finding | Fair use? | Parameters |
|---|---|---|---|
| Sime. "Movement toward the familiar: Person and place affiliation in a fire entrapment setting". Environment and Behavior 17(6):697-724, 1985 [V abstract, see EVAC_PSYCHOLOGY.md] | Summerland fire (1973): exit choice mediated by person affiliation (go to companions) and place affiliation (go the way you came in), not just proximity | Yes for "familiar-exit bias and affiliation" | Use Station fire about 2/3 entry exit share (NCSTAR 2) as the number |

## 9. Recent LLM plus emotion or appraisal work (2023 to 2026)

| Work | Finding | What to take |
|---|---|---|
| Durupinar. "How Affect Propagates among LLM Agents: Emergent Emotional Contagion in Crowd Simulation". arXiv 2607.25140, July 2026 [V abstract] | LLM does appraisal (Big Five, Russell circumplex), a crowd simulator steers; no hand-written contagion, it emerges from perceiving expressions. Alarm spreads as a front to a nonzero plateau; dynamics depend on the model backend, prompt and temperature; sparse small crowds only; no human-data validation in the abstract | Closest prior work; cite it. Its backend dependence is our argument for computing contagion in code |
| Huang et al. "Apathetic or Empathetic? Evaluating LLMs' Emotional Alignments with Humans" (EmotionBench). NeurIPS 2024. arXiv 2308.03656 [V sec] | 400+ appraisal-based situations, 8 emotions, 1,266 human subjects; LLMs respond sensibly in places but fall short of human emotional alignment and do not link similar situations | Supports not asking the LLM for emotion intensity |
| Tak, Gratch. "Is GPT a Computational Model of Emotion? Detailed Analysis". arXiv 2307.13779, 2023 (ACII 2023 venue U) [V sec] | GPT appraisals (Smith and Lazarus items) align with human appraisals and labels, but it struggles with emotion intensity and coping | Let the LLM name the feeling (our self-report check), compute intensity in code |
| Croissant, Frister, Schofield, McCall. "An appraisal-based chain-of-emotion architecture for affective language model game agents". PLOS ONE 19:e0301033, 2024. doi 10.1371/journal.pone.0301033 [V sec] | Appraisal step before response improved believability and user-experience metrics over control LLM agents | Precedent for appraisal-then-act prompting |
| Mozikov et al. "EAI: Emotional Decision-Making of LLMs in Strategic Games and Ethical Dilemmas". NeurIPS 2024. arXiv 2406.03299 [V sec] | Injected emotions shift LLM decisions in ways that often diverge from human patterns (e.g. cooperation drops even under positive emotion); depends on model size and alignment | Risk for us: passing computed emotion into the deliberation prompt may bias choices non-humanly. Ablate emotion-in-prompt vs not and compare to the calibration targets |

## Formulas we can defend

All variables in [0, 1] unless stated. The theories give the dimensions and
directions; the product forms follow EMA (intensity = abs(desirability x
likelihood)); the exact combinations are ours and need calibration.

Appraisal inputs (code): R relevance (threat could reach me or my dependants),
S threat certainty (PADM risk identification; raised by environmental cues,
credible warnings and social cues), U urgency (1 - time to impact / time needed,
clipped), C coping potential (EMA controllability: estimated success of the
best known option, from route length, queue and blockage), B goal blocked
(current plan obstructed), H_prev expected harm at the last appraisal.

Expected harm (EMA undesirable outcome with likelihood S (1 - C)):

    H = R * S * (1 - C)

Emotions:

    fear        = H * U                          # Lazarus fright: immediate, concrete; Scherer: high urgency, low power
    anxiety     = R * 4 * S * (1 - S) * (1 - C)  # Lazarus: uncertain threat; peaks at S = 0.5
    hope        = R * S * C                      # OCC/EMA: desirable prospect (reaching safety), likelihood C < 1
    relief      = max(0, H_prev - H)             # OCC: undesirable prospect disconfirmed; event pulse
    frustration = R * B                          # Scherer goal obstruction; never label it anger without a blamed agent (Lazarus)
    concern     = dependant_responsibility * harm_to_dependant   # OCC fortunes of others / Lazarus compassion

Each emotion relaxes toward a persona baseline b between appraisals:

    e(t + dt) = b + (e(t) - b) * exp(-dt / tau)

No published evacuation value for tau exists; Durupinar ties decay rate to
neuroticism with hand-tuned values. Start with tau 10 to 30 s and treat it as a
calibration knob [ours].

Contagion (ASCRIBE, Bosse et al. 2013, quoted form; S sender, R receiver):

    gamma_SR = eps_S * alpha_SR * delta_R          # expressiveness x channel x openness
    gamma_R  = sum over S of gamma_SR
    q*_R     = sum over S of gamma_SR * q_S / gamma_R
    q_R(t + dt) = q_R + gamma_R * [ eta_R * ( beta_R * (1 - (1 - q*_R)(1 - q_R))
                                            + (1 - beta_R) * q*_R * q_R )
                                    + (1 - eta_R) * q*_R - q_R ] * dt

eta is amplify (1) vs absorb (0); beta is upward vs downward bias. Use eta = 0
(pure absorption): psychology describes convergence, ASCRIBE's amplification
has no stated basis (van Haeringen and Gerritsen 2023), and the absorption-only
form reduces to q_R += gamma_R * dt * (q*_R - q_R), which stays in [0, 1] if
gamma_R * dt <= 1 (clamp it). Channel: alpha_SR = line_of_sight(S, R) * (1 -
1/(1 + exp(-sigma (d - tau_d)))), with tau_d about 4 m for sight (Durupinar's 4 m,
120 degree cone) and larger for heard screams (van Haeringen used 10 m, a
guess). Personality: delta_R = empathy from 0.354 O + 0.177 C + 0.135 E + 0.312 A
+ 0.021 N and eps_S from 0.14 O - 0.02 C + 0.32 E + 0.11 A + 0.29 N, traits in
[-1, 1], then rescale to [0, 1] (the coefficients are correlations, not
weights that sum to 1). ASCRIBE's own fit used 0.5 for most personal
parameters. Staff calming (ESCAPES; Ntika et al. WIMS 2014): q_R += delta_R *
(q_staff - q_R) on contact, q_staff low. Apply contagion to fear and anxiety
only, and log each delta in emotion_sources as the doc already plans.

Arousal to attention (Easterbrook; Mather and Sutherland; Sorensen and
Barratt): reallocate, do not shrink evenly. With arousal a and cue priority
p_i (1 for fire, smoke, blocked route, exit or crowd movement on my route, 0
for peripheral or off-task cues):

    weight_i = salience_i * (1 + k * a * (2 * p_i - 1))
    perceive cue i if weight_i >= theta, or with probability min(1, weight_i)

k about 0.5 [ours, no published value]; only apply narrowing when the
situation is negative or threatening (van Steenbergen via Sorensen and
Barratt). Calibrate theta so a calm agent notices a static sign about 38% of
the time (Xie 2012) and a dynamic sign about 77% (Galea 2014). Optional
inverted-U: above a = 0.9, also cut high-priority detection (Easterbrook's
disorganization at extreme arousal) [ours].

Faithfulness test statistics (fix for the interview pipeline): estimate the
baseline flip rate p0 by re-running the unchanged prompt n times; call an item
decisive only if its removal flip rate beats p0 significantly. With n = 3 and
p0 = 0.3, a 2-of-3 "majority flip" happens 22% of the time by chance and 3-of-3
2.7%; use n >= 10, or temperature 0 where the provider is deterministic. Also
run the sufficiency check (keep only cited items) and a joint removal of all
cited items, so redundant reasons are not all marked "not decisive".

## Corrections to docs/AGENT_COGNITION.md

1. Line 8 to 11: Turpin's unfaithful CoTs were produced at decision time, not
   afterwards. Rewrite as "stated reasons, even at decision time, can omit what
   drove the output (Turpin et al. 2023)", and say rule 3, not rule 1, is the
   answer to it. Rule 1 is supported for humans by Ericsson and Simon 1980.
2. Line 18 and lines 130 to 134: "verified only if removing it changes the
   choice" treats necessity as the whole test. Overdetermined choices (two
   sufficient reasons) mark both reasons "not decisive". Add sufficiency and
   joint-removal checks and say removal can create odd prompts (ROAR caveat).
3. Lines 130 to 144: 3 re-runs with majority flip cannot separate signal from
   sampling noise (22% false "majority flip" at a 30% baseline flip rate; our
   own calibration found 0.34 to 0.54 bits of action entropy left within a
   model x persona cell). Report flip rates against a baseline with n >= 10.
   The example "3 of 3 vs 1 of 3" should show the baseline too.
4. Line 151 to 152: "Novel" is overstated. The method is established
   (ERASER comprehensiveness; Madsen 2024 redaction; Atanasova 2023
   counterfactual test; Matton 2025 causal concept faithfulness). Say "applies
   established faithfulness tests to simulated occupants' decisions" and cite
   them.
5. Line 41: replace "attention budget shrinks as arousal rises" with "attention
   reallocates toward threat and route-relevant cues as arousal rises, and
   peripheral cues drop" (Easterbrook; arousal-biased competition). Mark the
   evacuation evidence as indirect (Tucker et al. 2018 argue it, do not measure
   it).
6. Lines 57 to 59: "Each link is a published effect" is overstated. Arousal
   narrowing is published with qualifications; "fewer cues lower coping
   potential" is our model assumption; emotion contagion is published in
   models whose empirical validation is thin (one 35-person, 6 s video fit).
   Say "each link rests on published theory; the loop itself is our
   hypothesis and is testable".
7. Line 42: the source order "own observation > official PA > warden/staff >
   known person > stranger" is not in Lindell and Perry 2012, which gives
   attributes (expertise, trustworthiness, protection responsibility) and no
   ranking. Putting PA above an in-person warden conflicts with
   EVAC_PSYCHOLOGY.md personas and the retail staff evidence. Make weights
   persona-dependent and cite the three attributes.
8. Line 43: "social proof" is a PADM social cue (and Kinateder/Comunale
   herding), not an appraisal dimension; feed it into threat certainty and
   option choice. "Responsibility (role, dependants)" is PADM protection
   responsibility or OCC fortunes-of-others, not appraisal-theory
   responsibility (blame). Rename to avoid the clash. Also "frustration" is
   not an OCC or Lazarus category; cite it as Scherer goal obstruction.
9. Line 43: ASCRIBE should be described as "fitted to one incident (Dam Square
   2010, 35 traced people)", and the doc should say we use its absorption
   term only; its amplification term lacks a psychological basis (van
   Haeringen and Gerritsen 2023). Durupinar 2016 is cited for OCEAN mapping;
   note its threshold contagion did worse than ASCRIBE against footage (Tsai
   et al. 2013).
10. Line 44: "milling and information seeking (Wood, Mileti)": Wood et al. 2018
    and Mileti and Peek 2000 are community and mobile-alert studies; for
    building fires cite Kuligowski and Mileti 2009 and NIST TN 1664, which
    found information seeking alone did not drive delay in a high-rise office
    fire.
11. Line 51 to 52: the 34% freeze figure is earthquake footage (Christchurch
    and Tohoku, Lambie et al.), not an intruder or cornered scenario. Say so,
    or drop the comparison; we have no real "cornered" freeze rate.
12. Line 50 to 55: worth adding the support we now have for computing emotion
    in code: EmotionBench (LLMs fall short of human emotional alignment) and
    Tak and Gratch 2023 (labels fine, intensity poor). Also add Mozikov et al.
    2024 as a risk: emotion in the prompt can bias choices non-humanly, so
    ablate it.
13. Line 45: PADM wording is now verified; the doc and EVAC_PSYCHOLOGY.md can
    quote the five questions exactly (see section 3) and note the paper's own
    line that risk identification equals primary appraisal.
14. Sources section: add Durupinar 2026 (arXiv 2607.25140) as the closest prior
    work on LLM crowd emotion; it is not cited anywhere yet.
