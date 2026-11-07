"""Build the fly response table offline with the real connectome simulation.

Runs the Shiu et al. 2024 leaky integrate-and-fire model on the MaleCNS v1.0 connectome
(169,984 neurons) for every cell of a small stimulus grid, once on the real wiring and once
on a degree-preserving rewire (the scrambled control), and stores descending-neuron rates.

Simulator, connectome cache and the rewire null come from the user's FlyScore project
(github.com/LordKarV/flyscore, MIT, `flyscore.lif.ShiuLIF`, `flyscore.connectome`). The
walking drive and the descending-neuron readout follow ../fly4 (drunkfly/loop.py, brain.py).
It needs numba, so run it in FlyScore's environment, with this repo on the path:

    cd ~/projects/nova && nice -n 10 ~/projects/flyscore/.venv/bin/python -m flyagent table

FLYSCORE_DIR (default ~/projects/flyscore) must hold cache/malecns.npz (`flyscore prepare`).
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from flyagent.core import LEVELS, TABLE_PATH, key

FLYSCORE_DIR = Path(os.environ.get("FLYSCORE_DIR", Path.home() / "projects" / "flyscore"))
WINDOW_MS = 300.0
TRIALS = 4          # trials run as parallel threads: this is also the CPU cap
SEED = 7
REWIRE_SEED = 0
SIDE_RE = r"_([LR])(?:\d|_|$)"

# stimulus channel -> MaleCNS cell types it drives (one Poisson rate per cell)
SENSORY = {
    # CO2-sensing ab1C ORNs (Gr21a/Gr63a, V glomerulus): innate avoidance (Suh et al. 2004)
    "co2": {"ORN_V"},
    # arista hot cells, VP2 glomerulus (Gallio et al. 2011)
    "heat": {"TRN_VP2"},
    # looming detectors upstream of the giant fiber (von Reyn 2014; Ache 2019; FlyScore B6)
    "loom": {"LC4", "LPLC2"},
    # inner photoreceptors, which drive phototaxis (Yamaguchi et al. 2010)
    "light": {"R7y", "R7p", "R7d", "R8y", "R8p", "R8d"},
    # Johnston's organ sound/vibration neurons (JO-A/B, Kamikouchi et al. 2009)
    "sound": "^JO-[AB]",
}
# walking motivation, both sides, always on: an alert, walking fly (fly4 drunkfly/loop.py DRIVE_HZ)
WALK_DRIVE_HZ = {"LC9": 50.0, "LC31a": 50.0, "PFL3": 30.0}
# Poisson rate (Hz) per channel and level; FlyScore's rates where it has one (loom 100, JO 140)
RATE_HZ = {"smoke": {1: 75.0, 2: 150.0}, "loom": {1: 100.0}, "light": {1: 100.0}, "sound": {1: 140.0}}
# readout name -> MaleCNS type; roles as in fly4 brain.py WALK_TYPES plus the giant fiber
READOUT = {"GF": "DNp01", "MDN": "MDN", "P9": "DNp09", "oDN1": "DNg97", "DNa01": "DNa01", "DNa02": "DNa02"}


def _groups(neurons) -> dict[str, list[int]]:
    t = neurons["type"].astype("string").fillna("")
    side = neurons["instance"].astype("string").fillna("").str.extract(SIDE_RE, expand=False).fillna("")

    def ids(types, s):
        m = t.str.match(types).fillna(False) if isinstance(types, str) else t.isin(types)
        return neurons.bodyId[m & (side == s)].astype(int).tolist()

    g = {}
    for s in "LR":
        g |= {f"{ch}_{s}": ids(types, s) for ch, types in SENSORY.items()}
        g |= {f"walk_{s}": ids(set(WALK_DRIVE_HZ), s)}
        g |= {f"{name}_{s}": ids({ty}, s) for name, ty in READOUT.items()}
    return g


def _stim(cell: dict, groups: dict, neurons) -> dict[int, float]:
    t = dict(zip(neurons.bodyId.astype(int), neurons["type"].astype("string").fillna("")))
    hz = {b: WALK_DRIVE_HZ[t[b]] for s in "LR" for b in groups[f"walk_{s}"]}
    for ch, (side, level) in cell.items():
        if level == 0:
            continue
        chans = ("co2", "heat") if ch == "smoke" else (ch,)
        for s in ("LR" if side == "B" else side):
            for c in chans:
                hz |= {b: RATE_HZ[ch][level] for b in groups[f"{c}_{s}"]}
    return hz


def grid() -> list[dict]:
    """Every stimulus cell: {channel: (side, level)}, side in L/R/B (both) or "-" when off."""
    def opts(ch):
        sides = ("L", "R") if ch == "light" else ("L", "R", "B")
        if ch == "sound":
            sides = ("B",)
        return [("-", 0)] + [(s, lv) for lv in LEVELS[ch] for s in sides]
    chans = list(LEVELS)
    return [dict(zip(chans, combo)) for combo in itertools.product(*(opts(c) for c in chans))]


def _rates(model, cell, groups, neurons, read_ids, read_names) -> dict[str, float]:
    r = model.run(_stim(cell, groups, neurons), read_ids, trials=TRIALS, duration_ms=WINDOW_MS, seed=SEED)
    per = r.mean(0)
    out = {}
    for name in dict.fromkeys(read_names):
        out[name] = round(float(np.mean([per[i] for i, n in enumerate(read_names) if n == name])), 2)
    return out


def build(out_path: Path = TABLE_PATH) -> dict:
    sys.path.insert(0, str(FLYSCORE_DIR))
    from flyscore import connectome
    from flyscore.lif import PARAMS, ShiuLIF

    conn, neurons = connectome.load(FLYSCORE_DIR / "cache")
    groups = _groups(neurons)
    read_names = [f"{n}_{s}" for n in READOUT for s in "LR" for _ in groups[f"{n}_{s}"]]
    read_ids = [b for n in READOUT for s in "LR" for b in groups[f"{n}_{s}"]]
    wirings = {"real": conn, "scrambled": connectome.rewire(conn, np.random.default_rng(REWIRE_SEED))}
    cells = grid()
    body = {"provenance": {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "connectome": f"{conn.name} ({conn.n} neurons, {len(conn.pre)} edges)",
        "model": "Shiu et al. 2024 LIF (flyscore.lif.ShiuLIF)", "lif_params": PARAMS,
        "flyscore_commit": _git_head(FLYSCORE_DIR),
        "scrambled": f"degree-preserving rewire, rng seed {REWIRE_SEED} (flyscore.connectome.rewire)",
        "window_ms": WINDOW_MS, "trials": TRIALS, "seed": SEED,
        "sensory_types": {k: sorted(v) if isinstance(v, set) else v for k, v in SENSORY.items()},
        "walk_drive_hz": WALK_DRIVE_HZ, "rate_hz": RATE_HZ, "readout_types": READOUT,
        "group_sizes": {k: len(v) for k, v in groups.items()},
    }, "baseline": {}, "entries": {}}
    for wname, w in wirings.items():
        model = ShiuLIF()
        model.load(w)
        t0 = time.perf_counter()
        for i, cell in enumerate(cells):
            k = key(cell)
            c0 = time.perf_counter()
            rates = _rates(model, cell, groups, neurons, read_ids, read_names)
            body["entries"].setdefault(k, {})[wname] = rates
            print(f"{wname} {i + 1}/{len(cells)} {k} {time.perf_counter() - c0:.1f}s", flush=True)
        body["provenance"][f"wall_s_{wname}"] = round(time.perf_counter() - t0, 1)
        body["baseline"][wname] = body["entries"][key({})][wname]
    # wall seconds per simulated second, TRIALS trials running in parallel threads
    body["provenance"]["wall_s_per_simulated_s"] = round(
        body["provenance"]["wall_s_real"] / (len(cells) * WINDOW_MS / 1000), 2)
    out_path.write_text(json.dumps(body, indent=None, separators=(",", ":")))
    return body


def _git_head(path: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
