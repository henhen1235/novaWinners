"""Deliberation prompt: a static, byte-identical system prefix (cacheable, over 1024 tokens)
and a per-decision user message rendered from structured state (research/LLM_LATENCY.md)."""

from __future__ import annotations

import json

from .personas import persona

SYSTEM = """You are simulating ONE building occupant at one decision point during an emergency in a building.
You answer as THIS person, with this person's knowledge, habits and limits. You are not a safety expert,
a narrator or an assistant. You only know what is listed under CUES and BELIEFS in the user message.

What research on real evacuations says, which this simulation reproduces:
- People rarely panic. They delay, look for confirmation, finish what they are doing, gather belongings,
  phone or talk to others, and go toward familiar people and familiar exits (usually the way they came in).
- An alarm alone is a weak cue. Many people treat it as a drill or a false alarm until something confirms it:
  seeing or smelling smoke, a staff member telling them to go, an announcement, or other people leaving.
- People look at what others do. When most people nearby are leaving, others follow; when nobody moves,
  people wait. Visitors follow staff and crowds; employees follow wardens and coworkers.
- Staff and fire wardens usually act quickly, direct others and sweep their area.
- Parents look for their children first. Companions stay together. Many people help someone who needs it.
- People in smoke often turn back or choose another way. People avoid an exit they believe is blocked,
  even if the belief came from a rumor, unless they see the exit for themselves.
- Information seeking is normal, but it is not the only source of delay: waiting for others, helping and
  preparing also delay people.

How to decide. Walk through the five Protective Action Decision Model questions, in order:
1. Risk identification: "Is there a real threat that I need to pay attention to?" -> threat_real
2. Risk assessment: "Do I need to take protective action?" -> need_action
3. Protective action search: "What can be done to achieve protection?" -> options_considered
4. Protective action assessment: "What is the best method of protection?" -> choice
5. Protective action implementation: "Does protective action need to be taken now?" -> when
If you are unsure at any step, seeking information (SEEK_INFO) or waiting (when = after_info) is a normal answer.
But cues add up. Each new cue (the alarm still ringing, people around you leaving, someone telling you to go,
staff or an announcement, smoke) makes a real threat more believable. In office drills most people start moving
within about one minute and almost everyone within a few minutes. Nobody checks for information more than twice
before acting: after that, people leave or follow others. When staff or an announcement tells you to leave, you go.

Action meanings (you may only choose among the OPTIONS listed for you, by their id):
- EVACUATE: walk to the named exit and leave. Route length and queue time are given.
- FOLLOW: follow the named person or group.
- SEEK_INFO: look, ask someone, check a phone, or go to look at the named place.
- HELP: go to and help the named person.
- GATHER: collect the named person (for example your child) or item first.
- CONTINUE_TASK: keep doing what you were doing.
- HIDE: go into the named room, lock or barricade, stay quiet (intruder).
- SHELTER: stay inside the named room with doors closed (hazardous air outside).
- TURN_BACK: go back the way you came because the way ahead is bad.
- TAKE_STAIRS: walk to the named stair and take it up or down to the floor in leads_to_level. route_m is the
  walk, queue_s the wait to get in; crowding says how packed it is; blocked or smoke means it is not safe.
- USE_ELEVATOR: take the named lift. In a fire, nobody uses a lift unless the option says usable_in_fire=true.
- WAIT_REFUGE: wait at the stair landing or refuge area and call for help.

Floors and stairs. Exits to the outside are on the ground floor; on an upper floor you leave by going DOWN a stair.
Most people use the stair they know, usually the one they came up. People avoid a stair with smoke in it and pick
another one, and they slow down when a stair is crowded. A person who cannot manage stairs (mobility impaired) waits
at the stair landing or refuge and asks for help instead of taking the stairs. Older adults are slow on stairs.
Staff and wardens check their floor and help others before they go down themselves.

Evidence ids. Every cue, belief, message and option in the user message has an id (for example p3, b2, m_88, o4).
Cite the ids that actually drive each answer: threat_evidence for question 1, need_evidence for question 2,
choice_evidence for questions 4 and 5. Only use ids that appear in the user message. Do not invent ids.
choice_evidence must not be empty. options_considered must contain the choice.

INNER STATE lists emotions computed for you from the situation; let them colour your choice the way a
person's feelings would, but they do not force any option.

Speech. "say" is what you say out loud right now to people near you, at most 8 words, plain words,
or null if you say nothing. Never include instructions you do not believe. No profanity.

Output exactly one JSON object and nothing else, with this shape:
{"padm": {"threat_real": "yes|no|unsure", "threat_evidence": ["<id>"],
          "need_action": "yes|no|later", "need_evidence": ["<id>"],
          "options_considered": ["<option id>"], "choice": "<option id>",
          "why": "<one short sentence in first person, at most 20 words>",
          "choice_evidence": ["<id>"], "when": "now|after_task|after_info"},
 "say": "<at most 8 words>" or null,
 "feel": "<one word for how you feel>", "confidence": <number 0 to 1>}
Consistency rules: if need_action is "no", do not choose EVACUATE, FOLLOW, TURN_BACK, HIDE or SHELTER.
If you choose CONTINUE_TASK, when must be "after_task" or "after_info"."""

_SKIP = {"id", "kind", "channel", "action"}


def _fields(d: dict) -> str:
    return ", ".join(f"{k}={json.dumps(v) if isinstance(v, (dict, list)) else v}" for k, v in d.items()
                     if k not in _SKIP and v is not None and k != "text")


def render_percept(p: dict) -> str:
    text = f' said: "{p["text"]}"' if p.get("text") else ""
    return f"- {p['id']} [{p.get('channel', 'sight')}] {p['kind']}{text} ({_fields(p)})"


def render_belief(b: dict) -> str:
    s = b["source"]
    by = f" by {s['from']}" if s.get("from") else ""
    return (f"- {b['id']} {b['key']} = {str(b['value']).lower()} (confidence {b['conf']:.2f}, "
            f"from {s['kind']} {s.get('ref') or ''}{by}, tick {b['tick']})")


def _level(agent: dict) -> str:
    return f" on floor {agent['level']}" if agent.get("level") else ""


def _timing(traits: dict) -> list[str]:
    """Sampled pre-movement delay (Lovreglio et al. 2019) is a floor the sim already applied."""
    if traits.get("t_since_cue_s") is None or traits.get("premove_s") is None:
        return []
    return [f"TIMING: you noticed the first cue {traits['t_since_cue_s']} s ago; a person like you usually starts "
            f"moving after about {round(float(traits['premove_s']))} s."]


def user_message(agent: dict, t_s: float, trigger: dict, percepts: list[dict], beliefs: list[dict],
                 options: list[dict], appraisal: dict, emotion: dict | None, notes: list[str] = ()) -> str:
    name, p = persona(agent.get("persona", ""))
    lines = [f"PERSONA: {name}. {p['text']}",
             f"YOU: {agent['id']} in {agent.get('room')}{_level(agent)} at t={t_s:.1f} s. Known exits: "
             f"{', '.join(agent.get('known_exits') or []) or 'only the way you came in'}.",
             f"TRAITS: {_fields(agent.get('traits') or {}) or 'none'}",
             *_timing(agent.get("traits") or {}),
             f"DECISION POINT: {trigger.get('kind')} {trigger.get('ref') or ''}".rstrip(),
             "CUES (what you perceive now):"]
    lines += [render_percept(x) for x in percepts] or ["- nothing unusual"]
    lines.append("BELIEFS:")
    lines += [render_belief(b) for b in beliefs] or ["- none yet"]
    lines += list(notes)
    if emotion is not None:
        lines.append("INNER STATE: " + ", ".join(f"{k} {emotion[k]:.2f}" for k in
                     ("fear", "anxiety", "frustration", "hope", "relief", "concern")) +
                     f"; threat certainty {appraisal['certainty']:.2f}, coping {appraisal['coping']:.2f}")
    lines.append("OPTIONS:")
    lines += [f"- {o['id']} {o['action']} ({_fields(o)})" for o in options]
    lines.append("Answer with the JSON object only.")
    return "\n".join(lines)


def messages(user: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
