"""Fruit-fly occupant: a connectome-driven decide(view) (see research/FLY_AGENT.md).

Use with `python -m sim run ... --decide flyagent` and occupants whose model (or persona) is
"fruit_fly" or "fruit_fly_scrambled"; everyone else gets the sim's rule-based policy.
"""

from flyagent.decide import decide, fly_decide

__all__ = ["decide", "fly_decide"]
