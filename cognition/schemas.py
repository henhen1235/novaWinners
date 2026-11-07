"""Pydantic models for one occupant decision and its interview (docs/AGENT_COGNITION.md).

Sim-supplied dicts (percepts, options) keep extra fields: the sim owns their shape,
we only require an id and a kind/action.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Unit = Field(ge=0.0, le=1.0)


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Percept(_Open):
    id: str
    kind: str  # fire, smoke, alarm, pa, exit_sign, exit, exit_blocked, person, utterance, intruder, ...
    channel: str = "sight"  # sight | hearing | smell
    ref: str | None = None  # exit / agent / room id the cue is about
    where: str | None = None
    dist_m: float | None = None


class BeliefSource(_Open):
    kind: str  # sight, smell, alarm, pa, warden, known, stranger, prior
    ref: str | None = None  # percept id that produced it
    from_: str | None = Field(default=None, alias="from")


class Belief(_Open):
    id: str
    key: str  # hazard:<room>, exit_open:<exit>, alarm, told_to_leave
    value: bool
    conf: float = Unit
    source: BeliefSource
    tick: int


class Option(_Open):
    id: str
    action: str  # EVACUATE, FOLLOW, SEEK_INFO, HELP, GATHER, CONTINUE_TASK, HIDE, SHELTER, TURN_BACK


class Appraisal(BaseModel):
    relevance: float = Unit
    certainty: float = Unit
    urgency: float = Unit
    coping: float = Unit
    goal_blocked: float = Unit
    social_proof: float = Unit  # PADM social cue; feeds certainty, not an appraisal dimension
    protection_responsibility: float = Unit  # dependants / role, not blame
    harm: float = Unit  # H = R*S*(1-C)


class Emotion(BaseModel):
    fear: float = Unit
    anxiety: float = Unit
    frustration: float = Unit
    hope: float = Unit
    relief: float = Unit
    concern: float = Unit
    valence: float = Field(ge=-1.0, le=1.0)
    arousal: float = Unit


class Padm(BaseModel):
    threat_real: Literal["yes", "no", "unsure"]
    threat_evidence: list[str] = []
    need_action: Literal["yes", "no", "later"]
    need_evidence: list[str] = []
    options_considered: list[str]
    choice: str
    why: str = Field(max_length=300)
    choice_evidence: list[str]
    when: Literal["now", "after_task", "after_info"]


class Decision(BaseModel):
    padm: Padm
    say: str | None = None
    feel: str = "calm"
    confidence: float = Unit


class Validation(BaseModel):
    status: Literal["ok", "repaired", "fallback", "replayed"]
    errors: list[str] = []


class DecisionRecord(_Open):
    record_id: str
    run_id: str
    tick: int
    t_s: float
    agent_id: str
    persona: str
    model: str
    trigger: dict
    percepts: list[Percept]
    attention_dropped: list[str] = []
    beliefs_before: list[Belief]
    beliefs_after: list[Belief]
    appraisal: Appraisal
    emotion: Emotion
    emotion_sources: list[dict]
    options: list[Option]
    inputs: dict  # everything needed to re-run this decision purely (mind_before, neighbours, traits)
    prompt_sha256: str
    raw_output: str
    decision: Decision
    validation: Validation
    source: Literal["llm", "fallback", "replay"]
    feel_agrees: bool | None = None
    latency_ms: int
    cost_usd: float


class Reason(BaseModel):
    id: str
    text: str
    label: Literal["necessary", "jointly sufficient", "stated, not decisive", "untested"]
    keep_rate_removed: float | None = None
    n: int = 0


class InterviewAnswer(BaseModel):
    answer: str
    citations: list[dict]
    reasons: list[Reason]
    verified: bool
    intent: str
    record_id: str | None
    dropped_sentences: list[str] = []
    baseline: dict | None = None
    counterfactual: dict | None = None
    llm_calls: int = 0
    cost_usd: float = 0.0
