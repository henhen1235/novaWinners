"""Deterministic egress analysis on any scene.json (blueprint or scan).

Modules: occupancy (IBC loads), spof (single points of failure), pinch
(narrowest clear width per room route), smoke (Tanaka-Yamana smoke clock).
CLI: python -m analysis out/hall/scene.json -o out/hall/analysis.json
"""
