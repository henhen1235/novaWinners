"""baseline -> adversary attack -> attacked run -> optimizer fixes -> re-run, for N rounds over fixed seeds.

Layout of out/loop/<name>/:
  analysis.json, scoreboard.json, report.md
  r0_baseline/{scenario.json, s42/{replay.json, decisions.jsonl, metrics.json, summary.json}, ...}
  r1/{adversary.json, optimizer.json, interventions.json}
  r1_attack/..., r1_fixed/... (scene.json too when a door was widened), r2/..., ...
Rerunning the same command replays every occupant decision from decisions.jsonl and every
adversary/optimizer call from the disk cache, so results and replays are identical and free.
"""

from __future__ import annotations

import copy
import json
import logging
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable

from analysis.__main__ import analyze
from scenario.chat import call_llm

from .adversary import attack
from .agent import MODEL
from .optimizer import fix
from .patch import validate_scenario
from .simrun import run_one

log = logging.getLogger("loop")
SEEDS = (42, 43, 44)
METRICS = ("t95_s", "t90_s", "evacuated", "stragglers", "misled_by_rumor", "max_queue", "pre_move_median_s",
           "cost_usd", "llm_calls", "decisions")
LOWER_IS_BETTER = ("t95_s", "t90_s", "stragglers", "misled_by_rumor", "max_queue")


def _num(s: dict, k: str) -> float:
    """Metric value; an unreached 95% clear time counts as the run duration (reported as '>=')."""
    v = s.get(k)
    return float(s["duration_s"] if k in ("t95_s", "t90_s") and v is None else (v or 0))


def aggregate(summaries: list[dict]) -> dict:
    """Median over seeds of each metric (plus exit usage), and the seeds where 95% never cleared."""
    out = {k: round(statistics.median(_num(s, k) for s in summaries), 2) for k in METRICS}
    out["t95_unreached_seeds"] = sum(not s["t95_reached"] for s in summaries)
    out["total"] = summaries[0]["total"]
    out["exit_usage"] = {e: statistics.median(s["exit_usage"].get(e, 0) for s in summaries)
                         for e in summaries[0]["exit_usage"]}
    return out


def paired_delta(before: list[dict], after: list[dict], key: str) -> dict:
    """after - before per seed: median and [min, max]."""
    d = [_num(a, key) - _num(b, key) for b, a in zip(before, after)]
    return {"median": round(statistics.median(d), 2), "min": round(min(d), 2), "max": round(max(d), 2)}


def _dump(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=str))


def _keep_cost(path: Path, res: dict) -> None:
    """A cached rerun costs 0; keep the cost of the first, real call in the record."""
    if path.exists() and not res.get("cost_usd"):
        res["cost_usd"] = json.loads(path.read_text()).get("cost_usd", 0.0)


class Loop:
    def __init__(self, scene_path: str, scenario_path: str, out: Path, decide: str, no_llm: bool,
                 seeds: tuple[int, ...], llm: Callable, model: str) -> None:
        self.scene_path, self.out, self.decide, self.no_llm = scene_path, out, decide, no_llm
        self.seeds, self.llm, self.model = seeds, llm, model
        self.scene = json.loads(Path(scene_path).read_text())
        self.scenario = {**json.loads(Path(scenario_path).read_text()), "scene": scene_path}
        errs = validate_scenario(self.scenario, self.scene)
        if errs:
            raise ValueError("base scenario invalid: " + "; ".join(errs))
        self.n0 = sum(g["count"] for g in self.scenario["occupants"])
        self.stages: list[dict] = []

    def stage(self, name: str, label: str, scene: dict, sc: dict) -> list[dict]:
        d = self.out / name
        scene_file = self.scene_path
        if scene != self.scene:
            scene_file = str(d / "scene.json")
            _dump(Path(scene_file), scene)
        sc = {**sc, "scene": scene_file}
        _dump(d / "scenario.json", sc)
        jobs = [(scene, sc, seed, d / f"s{seed}", self.decide, self.no_llm, self.n0, scene_file, str(d / "scenario.json"))
                 for seed in self.seeds]
        if self.decide == "cognition" and len(jobs) > 1:  # LLM-bound: one process per seed (cognition keeps per-process minds)
            with ProcessPoolExecutor(len(jobs)) as ex:
                sums = list(ex.map(run_one, *zip(*jobs)))
        else:
            sums = [run_one(*j) for j in jobs]
        for seed, summ in zip(self.seeds, sums):
            log.info("%s seed %s: t95 %s, %s/%s out, %s misled, $%.4f", name, seed, summ["t95_s"],
                     summ["evacuated"], summ["total"], summ["misled_by_rumor"], summ["cost_usd"])
        self.stages.append({"stage": name, "label": label, "scene": scene_file, "per_seed": dict(zip(self.seeds, sums)),
                            "median": aggregate(sums)})
        return sums

    def run(self, rounds: int) -> dict:
        self.out.mkdir(parents=True, exist_ok=True)
        analysis = analyze(self.scene)
        _dump(self.out / "analysis.json", analysis)
        scene, sc = self.scene, copy.deepcopy(self.scenario)
        sums = self.stage("r0_baseline", "baseline", scene, sc)
        rounds_log = []
        for r in range(1, rounds + 1):
            adv = attack(scene, analysis, sc, aggregate(sums), r, self.no_llm, self.llm, self.model)
            _keep_cost(self.out / f"r{r}" / "adversary.json", adv)
            _dump(self.out / f"r{r}" / "adversary.json", {k: v for k, v in adv.items() if k != "scenario"})
            att = self.stage(f"r{r}_attack", f"round {r} attacked", scene, adv["scenario"])
            opt = fix(scene, adv["scenario"], analysis, att, aggregate(att), adv["injections"], r, self.no_llm,
                      self.llm, self.model)
            _keep_cost(self.out / f"r{r}" / "optimizer.json", opt)
            _dump(self.out / f"r{r}" / "optimizer.json", {k: v for k, v in opt.items() if k not in ("scene", "scenario")})
            _dump(self.out / f"r{r}" / "interventions.json", {"round": r, "interventions": opt["interventions"]})
            sums = self.stage(f"r{r}_fixed", f"round {r} fixed", opt["scene"], opt["scenario"])
            scene, sc = opt["scene"], opt["scenario"]
            rounds_log.append({"round": r, "adversary": {k: adv[k] for k in ("injections", "source", "cost_usd", "llm_calls")},
                               "optimizer": {k: opt[k] for k in ("interventions", "source", "cost_usd", "llm_calls")},
                               "improvement": {k: paired_delta(att, sums, k) for k in LOWER_IS_BETTER}})
        board = self.scoreboard(rounds_log)
        _dump(self.out / "scoreboard.json", board)
        (self.out / "report.md").write_text(report_md(board))
        return board

    def scoreboard(self, rounds_log: list[dict]) -> dict:
        occ_cost = sum(s["cost_usd"] for st in self.stages for s in st["per_seed"].values())
        occ_calls = sum(s["llm_calls"] for st in self.stages for s in st["per_seed"].values())
        agent_cost = sum(r[k]["cost_usd"] for r in rounds_log for k in ("adversary", "optimizer"))
        lat = [s["latency"]["p50_ms"] for st in self.stages for s in st["per_seed"].values() if s["latency"]["p50_ms"]]
        return {"name": self.out.name, "scene": self.scene_path, "decide": self.decide, "no_llm": self.no_llm,
                "seeds": list(self.seeds), "occupants": self.n0, "stages": self.stages, "rounds": rounds_log,
                "cost": {"occupant_decisions_usd": round(occ_cost, 4), "occupant_llm_calls": occ_calls,
                         "adversary_optimizer_usd": round(agent_cost, 4),
                         "adversary_optimizer_requests": sum(r[k]["llm_calls"] for r in rounds_log for k in ("adversary", "optimizer")),
                         "total_usd": round(occ_cost + agent_cost, 4)},
                "latency_per_tick_ms_p50_median": statistics.median(lat) if lat else None}


def fmt(m: dict, k: str) -> str:
    if k == "t95_s" and m.get("t95_unreached_seeds"):
        return f">={m[k]:g} ({m['t95_unreached_seeds']} seed(s) never reached 95%)"
    return f"{m[k]:g}"


def report_md(b: dict) -> str:
    rows = ["| stage | t95 s | t90 s | evacuated | stragglers | misled | max queue | LLM calls | cost $ |",
            "|---|---|---|---|---|---|---|---|---|"]
    for st in b["stages"]:
        m = st["median"]
        rows.append(f"| {st['label']} | {fmt(m, 't95_s')} | {m['t90_s']:g} | {m['evacuated']:g}/{m['total']} | {m['stragglers']:g} | "
                    f"{m['misled_by_rumor']:g} | {m['max_queue']:g} | {m['llm_calls']:g} | {m['cost_usd']:.4f} |")
    lines = [f"# Adversary vs optimizer: {b['name']}", "",
             f"Scene `{b['scene']}`, {b['occupants']} occupants, decide={b['decide']}, seeds {b['seeds']} "
             f"(numbers are medians over seeds; t95 = time to clear 95% of the original occupants, added wardens not counted).",
             "", *rows, ""]
    for r in b["rounds"]:
        lines += [f"## Round {r['round']}", "", f"Adversary ({r['adversary']['source']}):"]
        lines += [f"- `{i['event']['type']}` at {json.dumps(i['event']['at'])}, t={i['event']['t_start_s']:g} s: {i['why']}"
                  for i in r["adversary"]["injections"]]
        lines += ["", f"Optimizer ({r['optimizer']['source']}):"]
        lines += [f"- `{iv['kind']}` {json.dumps(iv['args'])}: {iv['why']}" for iv in r["optimizer"]["interventions"]]
        lines += ["", "Change from attacked to fixed (per-seed paired, median [min, max]; negative is better):"]
        lines += [f"- {k}: {d['median']:+g} [{d['min']:+g}, {d['max']:+g}]" for k, d in r["improvement"].items()]
        lines.append("")
    c = b["cost"]
    lines += ["## Cost", "", f"Occupant decisions: ${c['occupant_decisions_usd']} over {c['occupant_llm_calls']} LLM calls; "
              f"adversary + optimizer: ${c['adversary_optimizer_usd']} over {c['adversary_optimizer_requests']} requests; "
              f"total ${c['total_usd']}. Median per-tick LLM latency (p50): {b['latency_per_tick_ms_p50_median']} ms.", ""]
    return "\n".join(lines)


def run(scene: str, scenario: str, out: str, rounds: int = 2, decide: str = "policy", no_llm: bool = False,
        seeds: tuple[int, ...] = SEEDS, llm: Callable = call_llm, model: str = MODEL) -> dict:
    return Loop(scene, scenario, Path(out), decide, no_llm, seeds, llm, model).run(rounds)
