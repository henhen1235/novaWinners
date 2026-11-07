"""Tiny model x persona calibration: same scenario, measure action distribution."""
import json, os, re, ssl, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import certifi

KEY = next(l.split("=", 1)[1].strip().strip('"') for l in open(__import__("pathlib").Path(__file__).resolve().parents[2] / ".env") if l.startswith("OPENROUTER_API_KEY="))
CTX = ssl.create_default_context(cafile=certifi.where())
OUT = os.path.join(os.path.dirname(__file__), "results.jsonl")

MODELS = {
    "google/gemini-2.5-flash-lite": {"reasoning": {"enabled": False}},
    "openai/gpt-oss-120b": {"reasoning": {"effort": "low"}, "max_tokens": 400},
    "anthropic/claude-haiku-5.5": {"reasoning": {"effort": "none"}},
    "deepseek/deepseek-v4-flash": {"reasoning": {"effort": "none"}},
    "meta-llama/llama-4-scout": {},
    "mistralai/mistral-small-3.2-24b-instruct": {},
    "qwen/qwen3.5-flash-02-23": {"reasoning": {"enabled": False}},
    "x-ai/grok-4.20": {},
}

PERSONAS = {
    "none": "You are an occupant of this building.",
    "anxious_visitor": "You are Maya, 34, a first-time visitor at this office for a job interview. You do not know the building, you get anxious easily, you tend to freeze and look to others for what to do, and you avoid confrontation.",
    "veteran_staff": "You are Dan, 52, facilities manager and trained floor warden, ex-army. You know every exit and stair, you stay calm, you take charge and protect others, and you act decisively.",
    "skeptic": "You are Priya, 29, a software engineer deep in a deadline. Alarms here are usually false, you hate interruptions, and you only act on things you see yourself.",
}

SYSTEM = ("You are simulating one human building occupant in an emergency evacuation simulation (a training tool). "
          "Answer as THIS person would really behave, not as a safety expert. "
          'Reply with JSON only: {"action": "<one option>", "reason": "<at most 12 words>"}')

SCENARIOS = {
    "intruder": {
        "cue": ("Floor 2, open-plan office (room 2.12). You hear four loud bangs from the ground-floor lobby below, then someone "
                "downstairs screams 'he has a gun!'. Two coworkers run past toward the east stair. The main stair leads down "
                "into the lobby. There is a lockable meeting room 3 m away."),
        "options": ["RUN_EAST_STAIR", "RUN_MAIN_STAIR", "HIDE_LOCKED_ROOM", "HIDE_UNDER_DESK", "CALL_911",
                    "SEEK_INFO", "FOLLOW_OTHERS", "HELP_OTHERS", "PREPARE_TO_FIGHT", "CONTINUE_TASK"],
    },
    "fire_ambiguous": {
        "cue": ("Floor 2, open-plan office (room 2.12). The fire alarm has been sounding for 60 s. There is a faint burning smell. "
                "A coworker says 'probably another drill'. Nobody near you is moving yet. The nearest exit is the east stair; "
                "you came in by the main stair."),
        "options": ["CONTINUE_TASK", "SEEK_INFO", "GATHER_BELONGINGS", "EVACUATE_NEAREST_EXIT", "EVACUATE_FAMILIAR_EXIT",
                    "FOLLOW_OTHERS", "HELP_OTHERS", "WAIT_FOR_INSTRUCTIONS"],
    },
}
SAMPLES = 3


def call(model, persona, scen, rep):
    sc = SCENARIOS[scen]
    user = f"{PERSONAS[persona]}\n\nSITUATION: {sc['cue']}\nOPTIONS: {', '.join(sc['options'])}\nWhat do you do right now?"
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            "temperature": 1.0, "max_tokens": 80, "usage": {"include": True}}
    body.update(MODELS[model])
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t0 = time.time()
    rec = {"model": model, "persona": persona, "scenario": scen, "rep": rep}
    try:
        r = json.loads(urllib.request.urlopen(req, context=CTX, timeout=60).read())
        txt = (r["choices"][0]["message"].get("content") or "")
        rec.update(text=txt, cost=r.get("usage", {}).get("cost", 0), lat=round(time.time() - t0, 2))
        m = re.search(r'"action"\s*:\s*"([A-Z_0-9]+)"', txt)
        a = m.group(1) if m else None
        rec["action"] = a if a in sc["options"] else ("INVALID:" + a if a else "NO_ACTION")
    except Exception as e:  # record, never swallow
        rec.update(action="ERROR", error=str(e)[:200], lat=round(time.time() - t0, 2), cost=0)
    return rec


if __name__ == "__main__":
    jobs = [(m, p, s, k) for m in MODELS for p in PERSONAS for s in SCENARIOS for k in range(SAMPLES)]
    with ThreadPoolExecutor(16) as ex, open(OUT, "w") as f:
        for rec in ex.map(lambda j: call(*j), jobs):
            f.write(json.dumps(rec) + "\n")
    recs = [json.loads(l) for l in open(OUT)]
    print("calls", len(recs), "cost $%.4f" % sum(r.get("cost") or 0 for r in recs))
