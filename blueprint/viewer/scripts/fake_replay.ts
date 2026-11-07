// Fake sim replay for building the viewer before `python -m sim run` exists.
// Usage: node scripts/fake_replay.ts <scene.json> -o public/replays/tiny_llm [--mode classic|llm] [--agents 30] [--seed 42]
// Writes <dir>/replay.json (docs/SIM_INTERFACES.md) and <dir>/decisions.jsonl, the layout the sim uses.
// ponytail: no queuing at doors and no collision avoidance; real replays come from sim/.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { buildNav, locate, nearestExit, onMesh, pathLength, type Vec2 } from "../src/nav.ts";

const argv = process.argv.slice(2);
const flag = (name: string, fallback: string) => {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : fallback;
};
const scenePath = argv[0];
if (!scenePath || scenePath.startsWith("-")) {
  console.error("usage: node scripts/fake_replay.ts <scene.json> -o out_dir [--mode classic|llm] [--agents 30] [--seed 42]");
  process.exit(2);
}
const outDir = flag("-o", "public/replays/fake");
const mode = flag("--mode", "llm");
const agentCount = Number(flag("--agents", "30"));
let seed = Number(flag("--seed", "42"));

// mulberry32: deterministic so screenshots and demo runs repeat.
function random() {
  seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
const between = (lo: number, hi: number) => lo + random() * (hi - lo);

const scene = JSON.parse(readFileSync(scenePath, "utf8"));
const level: string = scene.levels[0].id;
const nav = buildNav(scene.navmesh);
const DT = 0.5;
const RUMOR_T = 14;
const MODEL = mode === "llm" ? "google/gemini-2.5-flash-lite" : "rule-based";
const PERSONAS = [
  { name: "regular", share: 0.6, speed: 1.3, color: "#8DB4DC" },
  { name: "visitor", share: 0.3, speed: 1.1, color: "#D5DEE8" },
  { name: "staff", share: 0.1, speed: 1.4, color: "#6F93B8" },
];

function insidePolygon(p: Vec2, polygon: number[][]) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, zi] = polygon[i];
    const [xj, zj] = polygon[j];
    if (zi > p[1] !== zj > p[1] && p[0] < ((xj - xi) * (p[1] - zi)) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}

function spawn(room: { polygon: number[][]; centroid: number[] }): Vec2 {
  const xs = room.polygon.map((point) => point[0]);
  const zs = room.polygon.map((point) => point[1]);
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const p: Vec2 = [between(Math.min(...xs), Math.max(...xs)), between(Math.min(...zs), Math.max(...zs))];
    if (insidePolygon(p, room.polygon) && onMesh(nav, p, level, 0)) return p;
  }
  return nav.centroids[locate(nav, [room.centroid[0], room.centroid[1]], level)];
}

function along(points: Vec2[], meters: number): Vec2 {
  let left = meters;
  for (let i = 1; i < points.length; i += 1) {
    const [a, b] = [points[i - 1], points[i]];
    const step = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (left <= step) return [a[0] + ((b[0] - a[0]) * left) / (step || 1), a[1] + ((b[1] - a[1]) * left) / (step || 1)];
    left -= step;
  }
  return points[points.length - 1];
}

type Phase = { t0: number; t1: number; state: string; at?: Vec2; points?: Vec2[]; speed?: number };
type Exit = { id: string; level: string; position: number[]; label: string; width_m: number };
const exits: Exit[] = scene.exits.filter((exit: Exit) => exit.level === level);
const rooms = scene.rooms.filter((room: { level: string; type?: string }) => room.level === level && room.type !== "corridor");
const spawnRooms = rooms.length ? rooms : scene.rooms;
// Visitors only know the door they came in by: the widest exit.
const mainExit = [...exits].sort((a, b) => b.width_m - a.width_m)[0];
const events: Record<string, unknown>[] = [];
const decisions: Record<string, unknown>[] = [];

function decide(agent: Agent, t: number, from: Vec2, known: Exit[], trigger: string, heard?: { from: string; ref: string; exit: Exit }) {
  const best = nearestExit(nav, from, level, known) ?? nearestExit(nav, from, level, exits)!;
  const ref = `d_${String(decisions.length + 1).padStart(6, "0")}`;
  const fear = Math.min(1, 0.3 + random() * 0.3 + (heard ? 0.25 : 0));
  const reasons = [
    { text: trigger === "alarm" ? "The alarm is sounding" : `Heard that ${heard!.exit.label} is blocked`, cites: [heard ? heard.ref : "p1"], label: "necessary" },
    { text: `${best.exit.label} is the closest exit I know (${best.meters.toFixed(0)} m)`, cites: ["o1"], label: heard ? "necessary" : "stated, not decisive" },
  ];
  if (agent.persona === "visitor") reasons.push({ text: "It is the way I came in", cites: ["b1"], label: "stated, not decisive" });
  decisions.push({
    record_id: ref, run_id: runId, tick: Math.round(t / DT), t_s: t, agent_id: agent.id, persona: agent.persona, model: MODEL,
    trigger: { kind: trigger === "alarm" ? "alarm_heard" : "message_heard", ref: heard?.ref ?? "ev1" },
    percepts: [{ id: "p1", kind: "alarm", channel: "sound" }, { id: "p2", kind: "exit_sign", ref: best.exit.id, lit: true }],
    beliefs_before: heard ? [{ id: "b2", key: `exit_open:${heard.exit.id}`, value: false, conf: 0.7, source: { kind: "utterance", ref: heard.ref, from: heard.from } }] : [],
    emotion: { fear, anxiety: fear * 0.6, frustration: heard ? 0.4 : 0.1, hope: 1 - fear, concern: agent.persona === "staff" ? 0.6 : 0.1, valence: -fear, arousal: 0.4 + fear / 2 },
    options: known.map((exit, index) => ({ id: `o${index + 1}`, action: "EVACUATE", exit: exit.id })),
    decision: { choice: "o1", action: "EVACUATE", exit: best.exit.id, reasons },
    validation: { status: "ok", errors: [] }, latency_ms: mode === "llm" ? 600 : 0, cost_usd: mode === "llm" ? 0.00011 : 0,
  });
  events.push({ t, kind: "decision", agent: agent.id, ref, text: `Evacuate via ${best.exit.label}, ${best.meters.toFixed(1)} m` });
  return best;
}

type Agent = { id: string; persona: string; speed: number; start: Vec2; phases: Phase[]; known: Exit[]; exitId?: string };
const runId = `fake_${mode}_${seed}`;
const agents: Agent[] = [];
for (let index = 0; index < agentCount; index += 1) {
  const roll = random();
  const persona = roll < 0.6 ? PERSONAS[0] : roll < 0.9 ? PERSONAS[1] : PERSONAS[2];
  const room = spawnRooms[index % spawnRooms.length];
  const id = `a_${String(index + 1).padStart(2, "0")}`;
  const known = persona.name === "visitor" ? [mainExit] : exits;
  agents.push({ id, persona: persona.name, speed: persona.speed * between(0.9, 1.1), start: spawn(room), phases: [], known });
}

function plan(agent: Agent) {
  // LLM agents take longer to start (they look around, gather things); classic agents follow the pre-movement table.
  const pre = mode === "llm" ? between(3, 14) : between(2, 8);
  const think = mode === "llm" ? 1.5 : 0.5;
  const route = decide(agent, pre, agent.start, agent.known, "alarm");
  agent.phases = [
    { t0: 0, t1: pre, state: "waiting", at: agent.start },
    { t0: pre, t1: pre + think, state: "deciding", at: agent.start },
    { t0: pre + think, t1: pre + think + route.meters / agent.speed, state: "moving", points: route.points, speed: agent.speed },
  ];
  agent.exitId = route.exit.id;
}
agents.forEach(plan);

function positionAt(agent: Agent, t: number): [Vec2, string] {
  for (const phase of agent.phases) {
    if (t >= phase.t1) continue;
    if (phase.points) return [along(phase.points, (t - phase.t0) * phase.speed!), phase.state];
    return [phase.at!, phase.state];
  }
  const last = agent.phases[agent.phases.length - 1];
  return [last.points ? last.points[last.points.length - 1] : last.at!, "exited"];
}

// LLM run only: a staff member says the main exit is blocked; listeners heading there stall and reroute.
if (mode === "llm" && exits.length > 1) {
  const speaker = agents.find((agent) => agent.persona === "staff") ?? agents[0];
  const [where] = positionAt(speaker, RUMOR_T);
  events.push({ t: RUMOR_T, kind: "rumor", agent: speaker.id, ref: "m_001", text: `Says ${mainExit.label} is blocked` });
  for (const agent of agents) {
    if (agent === speaker) continue;
    const [p, state] = positionAt(agent, RUMOR_T);
    if (state === "exited" || Math.hypot(p[0] - where[0], p[1] - where[1]) > 8 || agent.exitId !== mainExit.id) continue;
    events.push({ t: RUMOR_T + 0.5, kind: "heard", agent: agent.id, ref: "m_001", text: `Heard from ${speaker.id}: ${mainExit.label} is blocked` });
    const stall = between(2, 6);
    const others = exits.filter((exit) => exit !== mainExit);
    const route = decide(agent, RUMOR_T + stall, p, others, "rumor", { from: speaker.id, ref: "m_001", exit: mainExit });
    agent.phases = agent.phases.filter((phase) => phase.t0 < RUMOR_T).map((phase) => ({ ...phase, t1: Math.min(phase.t1, RUMOR_T) }));
    agent.phases.push(
      { t0: RUMOR_T, t1: RUMOR_T + stall, state: "deciding", at: p },
      { t0: RUMOR_T + stall, t1: RUMOR_T + stall + route.meters / agent.speed, state: "moving", points: route.points, speed: agent.speed },
    );
    agent.exitId = route.exit.id;
  }
}

const exitTimes = agents.map((agent) => agent.phases[agent.phases.length - 1].t1);
const duration = Math.ceil(Math.max(...exitTimes) + 5);
agents.forEach((agent, index) => {
  const exit = exits.find((item) => item.id === agent.exitId)!;
  events.push({ t: Math.ceil(exitTimes[index] / DT) * DT, kind: "exited", agent: agent.id, ref: exit.id, text: `Left by ${exit.label}` });
});
events.sort((a, b) => (a.t as number) - (b.t as number));

const frames = [];
for (let t = 0; t <= duration + 1e-9; t += DT) {
  frames.push({
    t: Math.round(t * 10) / 10,
    a: agents.map((agent) => {
      const [p, state] = positionAt(agent, t);
      return [agent.id, Math.round(p[0] * 100) / 100, Math.round(p[1] * 100) / 100, state];
    }),
  });
}

const usage = Object.fromEntries(exits.map((exit) => [exit.id, agents.filter((agent) => agent.exitId === exit.id).length]));
const pre = agents.map((agent) => agent.phases[0].t1).sort((a, b) => a - b);
const replay = {
  meta: {
    scene: scenePath, scenario: `fake ${mode} run`, label: mode === "llm" ? "LLM agents" : "Classic agents", run_id: runId, seed: Number(flag("--seed", "42")),
    dt: DT, duration_s: duration, llm_calls: mode === "llm" ? decisions.length : 0, cost_usd: mode === "llm" ? Math.round(decisions.length * 0.00011 * 1e5) / 1e5 : 0,
  },
  agents: agents.map((agent) => ({ id: agent.id, persona: agent.persona, model: MODEL, color: PERSONAS.find((p) => p.name === agent.persona)!.color })),
  frames,
  events,
  metrics: {
    evacuated: agents.length, total: agents.length, t_last_exit_s: Math.max(...exitTimes), exit_usage: usage,
    pre_movement_s: { median: pre[Math.floor(pre.length / 2)], p90: pre[Math.floor(pre.length * 0.9)] }, per_persona: {},
  },
};
mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, "replay.json"), JSON.stringify(replay));
writeFileSync(join(outDir, "decisions.jsonl"), decisions.map((d) => JSON.stringify(d)).join("\n") + "\n");
console.log(`${outDir}/replay.json: ${agents.length} agents, ${frames.length} frames, ${events.length} events, last exit ${Math.max(...exitTimes).toFixed(1)} s, route total ${pathLength(agents[0].phases[2]?.points ?? []).toFixed(1)} m (a_01)`);
