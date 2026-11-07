// Sim replay playback (docs/SIM_INTERFACES.md replay.json): agents, clock, events feed,
// counters and the split view for ?replay=a.json&compare=b.json.
import * as THREE from "three";
import type { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import type { ViewerContext } from "./context";
import { openInterview, refreshInterview, updateInterview, type DecisionRecord } from "./interview";
import { People, type AgentSample } from "./people";
import { parseFrames, type ParsedFrames } from "./frames";
import { startSuggestions, type Suggestions } from "./recommendations";
import { agentNames, clockText, personaName, scenarioName } from "./names";

export type ReplayEvent = { t: number; kind: string; agent?: string; ref?: string; text?: string };
export type ReplayDoc = {
  meta: { dt: number; duration_s: number; llm_calls?: number; cost_usd?: number; label?: string; run_id?: string; scenario?: string; seed?: number };
  agents: { id: string; persona: string; model?: string; color?: string; speed?: number }[];
  // The 5th element is the level id; older replays have 4 elements (first level).
  frames: { t: number; a: [string, number, number, string, string?][] }[];
  events: ReplayEvent[];
};

export interface Run {
  url: string;
  doc: ReplayDoc;
  name: string;
  runId: string;
  xs: Float32Array[]; // per frame, per agent; NaN when the agent is absent
  zs: Float32Array[];
  states: string[][];
  levels: Uint8Array[]; // per frame, per agent: index into scene.levels
  decisions: DecisionRecord[];
  people: People;
  headings: Float32Array; // last walking direction per agent, kept while standing
  fear: { t: number[]; v: number[] }[]; // per agent, from decisions.jsonl emotion.fear
  root: THREE.Group;
  labels: THREE.Group;
  exitOf: (string | null)[]; // exit id each agent left by, from the first "exited" frame
  exitTime: number[];
  exitCounts: Map<string, HTMLElement>;
  ring: THREE.Mesh;
}

/** Hooks a live session gives the player: the transport drives the server, the clock trails the stream. */
export interface LiveHooks {
  onPlay: (playing: boolean) => void;
  onSpeed: (speed: number) => void;
  latest: () => number; // newest frame time received
}

export interface PlayerHandle {
  addEvents: (run: Run, events: ReplayEvent[]) => void;
  setCalls: (run: Run, calls: number, cost: number, errors: number) => void;
  noteDecisions: (run: Run, records: DecisionRecord[]) => void;
}

// Live frames are shown this far behind the newest one so positions can be interpolated.
const LIVE_BUFFER_S = 1;

export interface Player {
  runs: Run[];
  time: () => number;
  seek: (t: number) => void;
  stateAt: (run: Run, agent: number, t: number) => string;
}

const $ = <T extends HTMLElement>(selector: string) => document.querySelector<T>(selector)!;
const SPEEDS = [1, 4, 16];
// Agent colors follow design/DESIGN.md: calm text, moving route green, deciding amber, trapped hazard.
const STATE_TOKEN: Record<string, string> = {
  waiting: "text", hiding: "text", helping: "model", moving: "route", deciding: "warn", queued: "drafting",
  on_stairs: "drafting", waiting_at_refuge: "warn", carried: "model", at_elevator_lobby: "warn",
  trapped: "hazard-bright", hurt: "hazard-bright", blocked: "hazard-bright",
};
const STATE_LABEL: Record<string, string> = {
  waiting: "Not moving yet", hiding: "Hiding", helping: "Helping someone", moving: "Heading out", deciding: "Deciding", queued: "Queued at a door",
  trapped: "Trapped", hurt: "Hurt", blocked: "Blocked", on_stairs: "On the stairs", waiting_at_refuge: "Waiting at a refuge",
  carried: "Being carried", at_elevator_lobby: "At an elevator lobby", exited: "Out",
};
// What someone still inside is doing, for the "Still inside" list. Order = who needs a human first.
const SITUATION: [string, string, number][] = [
  ["trapped", "trapped", 0], ["blocked", "blocked by fire or smoke", 0], ["hurt", "hurt", 0],
  ["waiting_at_refuge", "waiting at a refuge", 1], ["carried", "being carried out", 2], ["never", "never started moving", 3],
  ["waiting", "stopped, not moving", 4], ["hiding", "hiding", 4], ["deciding", "deciding what to do", 5], ["at_elevator_lobby", "at an elevator lobby", 5],
  ["queued", "queued at a door", 6], ["helping", "helping someone out", 7], ["moving", "still walking out", 8], ["on_stairs", "on the stairs", 8],
];
const PERSONA_FALLBACK = ["drafting", "model", "walkable", "text-muted", "line-strong"];

async function fetchJson(url: string) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

// Fetch and parse in a Web Worker so a large replay never freezes the page; fall back to the main thread.
function loadReplay(context: ViewerContext, url: string): Promise<{ doc: ReplayDoc; parsed?: ParsedFrames }> {
  const request = { url: new URL(url, location.href).href, levelIds: context.doc.levels.map((level) => level.id), exits: context.doc.exits };
  let worker: Worker;
  try {
    worker = new Worker(new URL("./replay.worker.ts", import.meta.url), { type: "module" });
  } catch {
    return fetchJson(url).then((doc) => ({ doc: doc as ReplayDoc }));
  }
  return new Promise((resolve, reject) => {
    worker.onmessage = ({ data }) => {
      worker.terminate();
      if (data.error) reject(new Error(data.error));
      else resolve({ doc: data.doc as ReplayDoc, parsed: data.parsed as ParsedFrames });
    };
    worker.onerror = (event) => {
      worker.terminate();
      event.preventDefault();
      fetchJson(url).then((doc) => resolve({ doc: doc as ReplayDoc }), reject);
    };
    worker.postMessage(request);
  });
}

// decisions.jsonl sits next to replay.json; it is optional (emotion and reasons come from it).
async function fetchDecisions(url: string): Promise<DecisionRecord[]> {
  const base = url.slice(0, url.lastIndexOf("/") + 1);
  try {
    const response = await fetch(`${base}decisions.jsonl`);
    if (!response.ok) return [];
    const text = await response.text();
    if (text.trimStart().startsWith("<")) return []; // dev server fallback page, not a log
    return text.split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line));
  } catch {
    return [];
  }
}

// "EVACUATE exit_02" -> "Evacuate Main Entrance": sim event texts use action codes and exit ids.
function readable(context: ViewerContext, text: string | undefined) {
  if (!text) return text;
  let out = text.replace(/\b[A-Z][A-Z_]{2,}\b/g, (code) => code.charAt(0) + code.slice(1).toLowerCase().replace(/_/g, " "));
  for (const exit of context.doc.exits) out = out.replace(new RegExp(`\\b${exit.id}\\b`, "g"), exit.label);
  return out;
}

export function buildRun(context: ViewerContext, url: string, source: ReplayDoc, decisions: DecisionRecord[], given?: ParsedFrames): Run {
  const doc = { ...source, events: source.events.map((event) => ({ ...event, text: readable(context, event.text) })) };
  const index = new Map(doc.agents.map((agent, i) => [agent.id, i]));
  const count = doc.agents.length;
  const levelIds = context.doc.levels.map((level) => level.id);
  const { xs, zs, states, levels, exitOf, exitTime } = given ?? parseFrames(doc.frames, doc.agents.map((agent) => agent.id), levelIds, context.doc.exits);
  const fear = doc.agents.map(() => ({ t: [] as number[], v: [] as number[] }));
  for (const record of [...decisions].sort((a, b) => a.t_s - b.t_s)) {
    const k = index.get(record.agent_id);
    const value = record.emotion?.fear;
    if (k === undefined || typeof value !== "number") continue;
    fear[k].t.push(record.t_s);
    fear[k].v.push(value);
  }
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.32, 0.42, 40).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: context.token("text"), depthTest: false }),
  );
  ring.renderOrder = 11;
  ring.visible = false;
  const root = new THREE.Group();
  const labels = new THREE.Group();
  root.add(ring);
  const name = doc.meta.label ?? scenarioName(url.split("/").slice(-2, -1)[0] ?? url);
  // cognition.serve names a run by its directory under --runs (out/<run>/replay.json).
  const runId = url.split("/").slice(-2, -1)[0] || doc.meta.run_id || url;
  const people = null as unknown as People; // made in startReplay once we know whether this run paints heat
  return { url, doc, name, runId, xs, zs, states, levels, decisions, people, headings: new Float32Array(count), fear, root, labels, exitOf, exitTime, exitCounts: new Map(), ring };
}

// One "N out" tag per exit; hidden until someone uses that exit so 29 zeros do not crowd the plan.
function addExitCounts(context: ViewerContext, run: Run) {
  for (const exit of context.doc.exits) {
    const tag = context.label("exit-count", (element) => {
      const value = document.createElement("b");
      value.textContent = "0";
      element.append(value, " out");
      run.exitCounts.set(exit.id, value);
    });
    tag.center.set(-0.25, 1.2); // up and to the right of the exit sign, clear of the plan below it
    tag.visible = false;
    tag.userData.exit = exit;
    run.labels.add(tag);
  }
  const place = () => {
    for (const tag of run.labels.children) {
      const exit = tag.userData.exit;
      if (exit) context.place(exit.level, exit.position[0], exit.position[1], tag.position).setY(tag.position.y + 0.1);
    }
  };
  place();
  context.onLayout(place);
}

export async function startReplay(context: ViewerContext, replayUrl: string | null, compareUrl: string | null) {
  if (!replayUrl) return;
  const urls = compareUrl ? [replayUrl, compareUrl] : [replayUrl];
  let runs: Run[];
  try {
    runs = await Promise.all(
      urls.map(async (url) => {
        const [{ doc, parsed }, decisions] = await Promise.all([loadReplay(context, url), fetchDecisions(url)]);
        return buildRun(context, url, doc, decisions, parsed);
      }),
    );
  } catch (error) {
    $("#readout").textContent = `Could not load the replay (${error instanceof Error ? error.message : error}). Check the path under public/.`;
    return;
  }
  // ?names=Classic agents,LLM agents overrides the run captions for the demo.
  const names = new URLSearchParams(location.search).get("names")?.split(",");
  runs.forEach((run, i) => (run.name = names?.[i]?.trim() || run.name));
  // Suggested changes (recommendations.json next to the replay) are for a single run, not a comparison.
  suggestions = runs.length === 1 ? startSuggestions(context, runs[0].url) : null;
  play(context, runs);
}
let suggestions: Suggestions | null = null;

/** Plays loaded (or, with `live`, still growing) runs: agents, clock, feed, counters, interviews. */
export function play(context: ViewerContext, runs: Run[], live?: LiveHooks): PlayerHandle {
  runs.forEach((run, i) => {
    run.people ??= new People(context, run.doc.agents.map((agent) => agent.persona), i === 0);
    run.root.add(run.people.root);
    context.scene.add(run.root);
    addExitCounts(context, run);
  });
  runs[0].root.add(runs[0].labels);
  // People draw over the building in every view. A floor plate above them must not clip the figure.
  const onTop = (_view: string) => {
    for (const run of runs) run.people.setOnTop(true);
  };
  onTop(context.view());
  let started = false; // the split below re-lays out the stage before the clock exists
  context.onLayout((view) => {
    onTop(view);
    if (started) refresh(true);
  });
  if (runs[1]) {
    context.split(runs[0].root, runs[1].root, runs[1].labels);
    $("#caption-a").textContent = runs[0].name;
    $("#caption-b").textContent = runs[1].name;
    $("#split-captions").hidden = false;
  }

  // Fire or smoke anywhere in the run: elevators are recalled (not used in a fire).
  const fireKinds = new Set(["fire", "fire_spread", "smoke"]);
  if (runs.some((run) => run.doc.events.some((event) => fireKinds.has(event.kind)))) context.setElevatorsRecalled(true);
  let duration = Math.max(...runs.map((run) => run.doc.meta.duration_s ?? run.doc.frames[run.doc.frames.length - 1]?.t ?? 0));
  const clock = { t: 0, playing: false, speed: 1, colorBy: "persona" as "state" | "persona", last: 0 };

  const personaColors = new Map<string, THREE.Color>();
  for (const run of runs) {
    for (const agent of run.doc.agents) {
      if (personaColors.has(agent.persona)) continue;
      personaColors.set(agent.persona, agent.color ? new THREE.Color(agent.color) : context.token(PERSONA_FALLBACK[personaColors.size % PERSONA_FALLBACK.length]));
    }
  }
  const stateColor = (state: string) => context.token(STATE_TOKEN[state] ?? "text");

  // Binary search on frame times: live frames arrive at uneven steps when the server warps time.
  function frameIndex(run: Run, t: number) {
    const frames = run.doc.frames;
    let lo = 0;
    let hi = frames.length - 1;
    if (hi < 0) return { i: 0, j: 0, frac: 0 };
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (frames[mid].t <= t) lo = mid;
      else hi = mid - 1;
    }
    const j = Math.min(lo + 1, frames.length - 1);
    const span = frames[j].t - frames[lo].t;
    return { i: lo, j, frac: span > 0 ? Math.max(0, Math.min(1, (t - frames[lo].t) / span)) : 0 };
  }

  const stateAt = (run: Run, agent: number, t: number) => run.states[frameIndex(run, t).i][agent] ?? "";

  const levelIds = context.doc.levels.map((level) => level.id);
  const p0 = new THREE.Vector3();
  const p1 = new THREE.Vector3();
  const sample: AgentSample = {
    level: "", x: 0, z: 0, world: p0, heading: 0, state: "", moving: false, fear: NaN, speed: 0, fill: new THREE.Color(), ring: new THREE.Color(), phase: 0,
  };
  const fearAt = (run: Run, k: number, t: number) => {
    const series = run.fear[k];
    if (!series) return NaN;
    let value = NaN;
    for (let n = 0; n < series.t.length && series.t[n] <= t; n += 1) value = series.v[n];
    return value;
  };
  const perFloor = new Map<string, number>();
  // Speech bubbles: the LLM's `say` text over the speaker for 3 s after a "said" event.
  const BUBBLE_S = 3;
  const MAX_BUBBLES = 8;
  const isSaid = (event: ReplayEvent) => event.kind === "said" && event.agent && event.text;
  const said = new Map(runs.map((run) => [run, run.doc.events.map((event, n) => ({ event, n })).filter(({ event }) => isSaid(event))]));
  const bubbles = new Map(runs.map((run) => [run, new Map<number, CSS2DObject>()]));
  function drawBubbles(run: Run, t: number) {
    const shown = bubbles.get(run)!;
    const active = run.people.lod === "far" ? [] : said.get(run)!.filter(({ event }) => event.t <= t && t - event.t < BUBBLE_S).slice(-MAX_BUBBLES);
    const keep = new Set<number>();
    for (const { event, n } of active) {
      const k = run.doc.agents.findIndex((agent) => agent.id === event.agent);
      if (k < 0 || Number.isNaN(run.people.worldPositions[k * 3])) continue;
      keep.add(n);
      let bubble = shown.get(n);
      if (!bubble) {
        bubble = context.label("speech", (element) => (element.textContent = event.text!));
        bubble.center.set(0.5, 1.15);
        run.labels.add(bubble);
        shown.set(n, bubble);
      }
      bubble.position.fromArray(run.people.worldPositions, k * 3);
      bubble.position.y += run.people.lod === "near" ? 1.9 : 0.3;
    }
    for (const [n, bubble] of shown) {
      if (keep.has(n)) continue;
      bubble.removeFromParent();
      shown.delete(n);
    }
  }
  function placeAgents(run: Run, t: number) {
    if (!run.xs.length) return; // a live run before its first frame
    const { i, j, frac } = frameIndex(run, t);
    const view = context.view();
    const focus = context.level();
    const now = performance.now();
    run.people.begin(context.pxPerMeter());
    if (run === runs[0]) for (const id of levelIds) perFloor.set(id, 0);
    run.doc.agents.forEach((agent, k) => {
      const x0 = run.xs[i][k] ?? NaN; // live agents spawned after this frame have no slot yet
      const z0 = run.zs[i][k] ?? NaN;
      const state = run.states[i][k];
      const level0 = levelIds[run.levels[i][k]];
      const level1 = levelIds[run.levels[j][k]];
      if (!Number.isNaN(x0) && state !== "exited" && run === runs[0]) perFloor.set(level0, (perFloor.get(level0) ?? 0) + 1);
      if (Number.isNaN(x0) || (view === "single" && level0 !== focus)) {
        run.people.hide(k);
        return;
      }
      const x1 = Number.isNaN(run.xs[j][k] ?? NaN) ? x0 : run.xs[j][k];
      const z1 = Number.isNaN(run.zs[j][k] ?? NaN) ? z0 : run.zs[j][k];
      context.place(level0, x0, z0, p0);
      context.place(level1, x1, z1, p1);
      // Plans jump between panels at a floor change; in 3D the agent slides down the stair link.
      if (level0 !== level1 && view === "side") p1.copy(frac < 0.5 ? p0 : p1), p0.copy(p1);
      const dx = x1 - x0;
      const dz = z1 - z0;
      const moving = level0 === level1 && dx * dx + dz * dz > 1e-4;
      if (moving) run.headings[k] = Math.atan2(-dz, dx);
      p0.lerp(p1, frac);
      sample.level = level0;
      sample.x = x0 + dx * frac;
      sample.z = z0 + dz * frac;
      sample.heading = run.headings[k];
      sample.state = state;
      sample.moving = moving;
      sample.fear = fearAt(run, k, t);
      sample.speed = agent.speed ?? 0;
      sample.phase = k * 1.7;
      const persona = personaColors.get(agent.persona) ?? stateColor("");
      sample.fill.copy(clock.colorBy === "state" ? stateColor(state) : persona);
      // Chevrons are filled by persona and ringed by state, so both read at once.
      if (run.people.lod === "mid") sample.fill.copy(persona);
      sample.ring.copy(stateColor(state));
      run.people.set(k, sample, now);
      if (selection && selection.run === run && selection.agent === k) {
        run.ring.visible = true;
        run.ring.position.copy(p0).setY(p0.y + 0.05);
        const size = Math.max(1, 16 / context.pxPerMeter());
        run.ring.scale.set(size, 1, size);
      }
    });
    run.people.end(now);
    drawBubbles(run, t);
    for (const [exitId, element] of run.exitCounts) {
      const value = String(run.exitOf.filter((id, k) => id === exitId && run.exitTime[k] <= t).length);
      if (element.textContent !== value) element.textContent = value;
    }
    // Exits used by fewer than 3 people stay quiet, and where two counts would overlap on screen
    // only the busier exit keeps its label (greedy, busiest first).
    const tags = run.labels.children
      .filter((tag) => tag.userData.exit)
      .map((tag) => ({ tag, n: Number(run.exitCounts.get(tag.userData.exit.id)!.textContent) }))
      .sort((a, b) => b.n - a.n);
    const taken: number[][] = [];
    for (const { tag, n } of tags) {
      const exit = tag.userData.exit;
      tag.visible = n >= 3 && (view !== "single" || exit.level === focus);
      if (!tag.visible) continue;
      const [x, y] = context.toScreen(tag.position);
      const box = [x - 4, y - 40, x + 64, y - 12]; // the tag sits up and to the right of its anchor
      if (taken.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) tag.visible = false;
      else taken.push(box);
    }
    // Per-floor counts are one run's; with two runs side by side they would mislabel the right half.
    if (run === runs[0] && levelIds.length > 1 && runs.length === 1) for (const [id, n] of perFloor) context.setFloorCount(id, n);
  }


  // ---- rail and transport ----
  const scrub = $<HTMLInputElement>("#scrub");
  const clockOut = $("#clock");
  const play = $<HTMLButtonElement>("#play");
  scrub.max = String(duration);
  scrub.step = String(runs[0].doc.meta.dt || 0.5);
  $("#transport").hidden = false;
  $("#run").hidden = false;
  $("#inside").hidden = false;
  $("#agents").hidden = false;
  $("#feed-section").hidden = false;
  document.body.classList.add("replaying");

  function setPlaying(playing: boolean) {
    clock.playing = playing;
    live?.onPlay(playing);
    if (playing && clock.t >= duration && !live) clock.t = 0;
    play.setAttribute("aria-label", playing ? "Pause" : "Play");
    play.querySelector("span")!.textContent = playing ? "Pause" : "Play";
    play.classList.toggle("is-playing", playing);
  }
  play.addEventListener("click", () => setPlaying(!clock.playing));
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-speed]")) {
    button.addEventListener("click", () => {
      clock.speed = Number(button.dataset.speed);
      live?.onSpeed(clock.speed);
      for (const other of document.querySelectorAll("[data-speed]")) other.setAttribute("aria-pressed", String(other === button));
    });
  }
  scrub.addEventListener("input", () => seek(Number(scrub.value)));
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-color]")) {
    button.addEventListener("click", () => {
      clock.colorBy = button.dataset.color as "state" | "persona";
      for (const other of document.querySelectorAll("[data-color]")) other.setAttribute("aria-pressed", String(other === button));
      drawLegend();
      refresh(true);
    });
  }
  window.addEventListener("keydown", (event) => {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
    if (event.code === "Space") {
      event.preventDefault();
      setPlaying(!clock.playing);
    }
  });

  function drawLegend() {
    const legend = $("#legend");
    legend.replaceChildren();
    const entries: [string, THREE.Color][] = [];
    if (clock.colorBy === "persona") for (const [persona, value] of personaColors) entries.push([persona.replace(/_/g, " "), value]);
    else {
      const seen = live ? new Set(Object.keys(STATE_LABEL)) : new Set(runs.flatMap((run) => run.states.flat()).filter(Boolean));
      for (const state of Object.keys(STATE_LABEL)) if (seen.has(state)) entries.push([STATE_LABEL[state], state === "exited" ? context.token("exit-bright") : stateColor(state)]);
    }
    // Shape cues are the same in plans and 3D, so they get one fixed key under the colors.
    const cues: [string, string][] = [
      ["cue-vest", "Staff vest"],
      ["cue-fear", "Fear ring"],
      ["cue-refuge", "Dashed: at a refuge"],
      ["cue-heat", "Crowding (zoomed out)"],
    ];
    for (const [className, label] of cues) {
      const item = document.createElement("li");
      const swatch = document.createElement("i");
      swatch.className = `cue ${className}`;
      item.className = "legend-cue";
      item.append(swatch, label);
      legend.append(item);
    }
    for (const [name, value] of entries) {
      const item = document.createElement("li");
      const swatch = document.createElement("i");
      swatch.className = "dot";
      swatch.style.background = `#${value.getHexString()}`;
      if (name === STATE_LABEL.exited) swatch.style.opacity = "0.4";
      item.append(swatch, name.charAt(0).toUpperCase() + name.slice(1));
      legend.append(item);
    }
  }

  // ---- result: the outcome first, in a sentence ----
  const names = new Map(runs.map((run) => [run, agentNames(run.doc.agents)]));
  const nameOf = (run: Run, id: string | undefined) => (id && names.get(run)!.get(id)) || id || "";
  const totals: HTMLElement[] = [];
  const outcomes: HTMLElement[] = [];
  const callouts: HTMLElement[] = [];
  const counters = runs.map((run) => {
    const column = document.createElement("div");
    column.className = "counter";
    if (runs.length > 1) {
      const heading = document.createElement("h3");
      heading.textContent = run.name;
      column.append(heading);
    }
    const outcome = document.createElement("p");
    outcome.className = "result-line";
    outcomes.push(outcome);
    const last = document.createElement("p");
    last.className = "counter-caption";
    totals.push(last);
    const now = document.createElement("p");
    now.className = "counter-caption result-now";
    const value = document.createElement("b");
    now.append("Now: ", value);
    column.append(outcome, last, now);
    const calls = run.doc.meta.llm_calls ?? 0;
    const cost = document.createElement("p");
    cost.className = "counter-caption";
    cost.textContent = calls ? `${calls} model calls, $${(run.doc.meta.cost_usd ?? 0).toFixed(4)}` : "No model calls";
    callouts.push(cost);
    column.append(cost);
    $("#counters").append(column);
    return value;
  });
  function drawOutcome(run: Run, r: number) {
    const out = run.exitTime.filter((t) => Number.isFinite(t));
    const total = run.doc.agents.length;
    const got = document.createElement("b");
    got.textContent = String(out.length);
    outcomes[r].replaceChildren(got, live ? ` of ${total} out so far` : ` of ${total} got out`);
    totals[r].textContent = out.length ? `Last person out at ${clockText(Math.max(...out))}` : "Nobody got out";
  }
  runs.forEach(drawOutcome);

  // ---- still inside: who is left, grouped by who they are, what they are doing and where ----
  const firstSeen = new Map<Run, Float32Array>();
  const firstXZ = (run: Run) => {
    let xz = firstSeen.get(run);
    if (xz && !live) return xz;
    xz = new Float32Array(run.doc.agents.length * 2).fill(NaN);
    run.xs.forEach((xs, f) => {
      for (let k = 0; k < xz!.length / 2; k += 1) {
        if (!Number.isNaN(xz![2 * k]) || Number.isNaN(xs[k] ?? NaN)) continue;
        xz![2 * k] = xs[k];
        xz![2 * k + 1] = run.zs[f][k];
      }
    });
    firstSeen.set(run, xz);
    return xz;
  };
  type Group = { key: string; text: string; rank: number; members: number[]; level: string; x: number; z: number };
  const levelName = (id: string) => context.doc.levels.find((level) => level.id === id)?.name ?? id;
  function stillInside(run: Run, t: number): Group[] {
    const { i } = frameIndex(run, t);
    const first = firstXZ(run);
    const groups = new Map<string, Group>();
    run.doc.agents.forEach((agent, k) => {
      const state = run.states[i]?.[k] ?? "";
      const x = run.xs[i]?.[k] ?? NaN;
      const z = run.zs[i]?.[k] ?? NaN;
      if (!state || state === "exited" || Number.isNaN(x)) return;
      const level = levelIds[run.levels[i][k]];
      const still = Math.hypot(x - first[2 * k], z - first[2 * k + 1]) < 0.5;
      const situation = (state === "waiting" || state === "hiding") && still ? "never" : state;
      const [, phrase, rank] = SITUATION.find(([id]) => id === situation) ?? [situation, STATE_LABEL[situation]?.toLowerCase() ?? situation, 8];
      const stair = situation === "waiting_at_refuge" ? context.stairNear(level, x, z) : null;
      const what = stair ? `waiting at the stair ${stair} refuge` : phrase;
      const key = `${agent.persona}|${what}|${level}`;
      const group = groups.get(key) ?? { key, text: "", rank, members: [], level, x, z };
      group.members.push(k);
      groups.set(key, group);
      const where = levelIds.length > 1 ? `, ${levelName(level).toLowerCase()}` : "";
      group.text = `${group.members.length} ${personaName(agent.persona, group.members.length).toLowerCase()} ${what}${where}`;
    });
    return [...groups.values()].sort((a, b) => a.rank - b.rank || b.members.length - a.members.length);
  }
  const MAX_INSIDE_ROWS = 7;
  let insideKey = "";
  const pins = new Map<Run, CSS2DObject[]>();
  function drawInside(t: number) {
    const atEnd = !live && t >= duration - 0.01;
    if (atEnd) suggestions?.reveal();
    const all = runs.map((run) => stillInside(run, t));
    const key = `${atEnd}|${all.map((groups) => groups.map((g) => `${g.key}:${g.members.length}`).join(",")).join(";")}`;
    if (key === insideKey) return;
    insideKey = key;
    const list = $("#inside-list");
    list.replaceChildren();
    runs.forEach((run, r) => {
      const groups = all[r];
      if (runs.length > 1) {
        const heading = document.createElement("li");
        heading.className = "inside-run";
        heading.textContent = run.name;
        list.append(heading);
      }
      if (!groups.length) {
        const done = document.createElement("li");
        done.className = "inside-none";
        done.textContent = "Everyone is out.";
        list.append(done);
      }
      for (const group of groups.slice(0, MAX_INSIDE_ROWS)) {
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = group.rank <= 3 ? "needs-help" : "";
        button.textContent = group.text.charAt(0).toUpperCase() + group.text.slice(1);
        button.addEventListener("click", () => {
          const k = group.members[0];
          context.focusOn(group.level, run.xs[frameIndex(run, clock.t).i][k], run.zs[frameIndex(run, clock.t).i][k]);
          select(run, k);
        });
        item.append(button);
        list.append(item);
      }
      const rest = groups.slice(MAX_INSIDE_ROWS).reduce((n, g) => n + g.members.length, 0);
      if (rest) {
        const more = document.createElement("li");
        more.className = "inside-none";
        more.textContent = `and ${rest} more ${rest === 1 ? "person" : "people"}`;
        list.append(more);
      }
      // At the end of a replay the people left behind get a pin on the plan.
      for (const pin of pins.get(run) ?? []) pin.removeFromParent();
      pins.set(run, []);
      if (!atEnd) return;
      for (const group of groups.slice(0, MAX_INSIDE_ROWS)) {
        const pin = context.label("inside-pin", (element) => (element.textContent = `${group.members.length} ${group.text.split(",")[0].replace(/^\d+ /, "")}`));
        context.place(group.level, group.x, group.z, pin.position);
        pin.center.set(0.5, 1.4);
        run.labels.add(pin);
        pins.get(run)!.push(pin);
      }
    });
    const count = all[0].reduce((n, g) => n + g.members.length, 0);
    $("#inside-title").textContent = runs.length > 1 ? "Still inside" : count ? `Still inside: ${count}` : "Still inside";
  }

  // ---- events feed ----
  type FeedItem = { event: ReplayEvent; run: Run; element: HTMLLIElement };
  // ---- what happened: plain language, routine noise dropped ----
  const placeText = (text: string) =>
    text.replace(/\br_(\w+)/g, "room $1").replace(/\bdoor door_(\d+)_\w+/g, "a door on floor $1").replace(/\bdoor_(\d+)_\w+/g, "a door on floor $1").split(";")[0];
  const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
  const told = new Set<string>();
  function story(event: ReplayEvent, run: Run): string | null {
    const who = nameOf(run, event.agent);
    const text = event.text ?? "";
    switch (event.kind) {
      case "alarm": return "The fire alarm sounded";
      case "fire_spread": return capital(placeText(text));
      case "blocked": return who ? `${who} found the way blocked` : capital(placeText(text));
      case "waiting_at_refuge": return `${who} is waiting at a stair refuge for help`;
      case "help_assigned": case "helping": return who ? `${who} went to help someone` : capital(placeText(text));
      case "reached_person": return `${who} reached someone who needs help`;
      case "elevator_recalled": return told.has("elevators") ? null : (told.add("elevators"), "Elevators recalled; not usable in the fire");
      case "milestone": return text;
      case "rumor": return `Rumor: ${text}`;
      case "said": {
        // Staff instructions are the moments that move people; everyone else's chatter is noise.
        const persona = run.doc.agents.find((agent) => agent.id === event.agent)?.persona ?? "";
        if (!["staff", "warden"].includes(persona) || told.has(`said:${event.agent}`)) return null;
        told.add(`said:${event.agent}`);
        return `${who} told people: "${text}"`;
      }
      case "decision": {
        if (/go back|return/i.test(text)) return `${who} went back`;
        // Ignoring the alarm a minute in is a finding; the first seconds of finishing a task are not.
        if (/continue task/i.test(text) && event.t >= 60 && !told.has(`stay:${event.agent}`)) {
          told.add(`stay:${event.agent}`);
          return `${who} kept working`;
        }
        if (/refuge/i.test(text) && !told.has(`refuge:${event.agent}`)) {
          told.add(`refuge:${event.agent}`);
          return `${who} chose to wait at a refuge`;
        }
        return null;
      }
      default: return null;
    }
  }
  // Every 25 % out is a line of its own.
  const milestones = (run: Run): ReplayEvent[] => {
    const times = run.exitTime.filter((t) => Number.isFinite(t)).sort((a, b) => a - b);
    const total = run.doc.agents.length;
    return [0.25, 0.5, 0.75, 1].flatMap((share) => {
      const n = Math.ceil(total * share);
      return times.length >= n && n > 0 ? [{ t: times[n - 1], kind: "milestone", text: share === 1 ? `Everyone out (${total})` : `${share * 100}% out (${n} of ${total})` }] : [];
    });
  };
  // story() remembers what it already told (first "kept working" per person), so call it once per event.
  const feedItem = ({ event, run, line }: { event: ReplayEvent; run: Run; line: string }): FeedItem => {
      const element = document.createElement("li");
      element.className = `feed-${event.kind}`;
      element.hidden = true;
      const button = document.createElement("button");
      button.type = "button";
      const time = document.createElement("time");
      time.textContent = clockText(event.t);
      const what = document.createElement("span");
      what.className = "what";
      what.textContent = `${runs.length > 1 ? `${run.name}: ` : ""}${line}`;
      button.append(time, what);
      button.addEventListener("click", () => {
        seek(event.t);
        const agent = run.doc.agents.findIndex((item) => item.id === event.agent);
        if (agent >= 0) select(run, agent);
      });
      element.append(button);
      return { event, run, element };
  };
  const feed: FeedItem[] = runs
    .flatMap((run) => [...run.doc.events, ...milestones(run)].map((event) => ({ event, run })))
    .sort((a, b) => a.event.t - b.event.t)
    .map(({ event, run }) => ({ event, run, line: story(event, run) }))
    .filter((item): item is { event: ReplayEvent; run: Run; line: string } => item.line !== null)
    .map(feedItem);
  $("#feed").append(...feed.map((item) => item.element));
  let shown = 0;

  function refresh(force = false) {
    const t = clock.t;
    for (const run of runs) placeAgents(run, t);
    if (!force && t === clock.last) return;
    clock.last = t;
    scrub.value = String(t);
    clockOut.textContent = live ? `${clockText(t)}, live` : `${clockText(t)} of ${clockText(duration)}`;
    runs.forEach((run, r) => {
      const { i } = frameIndex(run, t);
      counters[r].textContent = `${(run.states[i] ?? []).filter((state) => state === "exited").length} out at ${clockText(t)}`;
      if (live) drawOutcome(run, r);
    });
    drawInside(t);
    const visible = feed.filter((item) => item.event.t <= t + 1e-6).length;
    if (visible !== shown) {
      feed.forEach((item, k) => (item.element.hidden = k >= visible));
      shown = visible;
      const list = $("#feed");
      list.scrollTop = list.scrollHeight;
    }
    if (selection) updateInterview(t);
  }

  function seek(t: number) {
    clock.t = Math.max(0, Math.min(duration, t));
    refresh(true);
  }

  // ---- selection and interview ----
  let selection: { run: Run; agent: number } | null = null;
  const player: Player = { runs, time: () => clock.t, seek, stateAt };
  function select(run: Run, agent: number) {
    for (const item of runs) item.ring.visible = false;
    selection = { run, agent };
    openInterview(player, run, agent, () => {
      selection = null;
      for (const item of runs) item.ring.visible = false;
    });
    $("#iv-title").textContent = nameOf(run, run.doc.agents[agent]?.id);
    refresh(true);
  }
  context.onPick((raycaster, side) => {
    const run = runs[Math.min(side, runs.length - 1)];
    const agent = run.people.pick(raycaster);
    if (agent < 0) return false;
    select(run, agent);
    return true;
  });

  let previous = performance.now();
  context.onFrame((now) => {
    const elapsed = Math.min((now - previous) / 1000, 0.1); // a hidden tab must not jump the clock
    previous = now;
    if (live) {
      // Trail the newest frame by the buffer; jump ahead if the stream got far in front (tab hidden, warp).
      const target = live.latest() - LIVE_BUFFER_S;
      if (target > duration) {
        duration = target;
        scrub.max = String(duration);
      }
      if (clock.playing) clock.t = Math.max(0, Math.min(target, clock.t + elapsed * clock.speed));
      if (target - clock.t > 3) clock.t = target - LIVE_BUFFER_S;
    } else if (clock.playing) {
      clock.t = Math.min(duration, clock.t + elapsed * clock.speed);
      if (clock.t >= duration) setPlaying(false);
    }
    refresh();
  });

  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-color]")) {
    button.setAttribute("aria-pressed", String(button.dataset.color === "persona"));
  }
  drawLegend();
  started = true;
  refresh(true);
  if (new URLSearchParams(location.search).has("autoplay")) setPlaying(true);

  return {
    addEvents(run, events) {
      for (const source of events) {
        const event = { ...source, text: readable(context, source.text) };
        run.doc.events.push(event);
        if (isSaid(event)) said.get(run)!.push({ event, n: run.doc.events.length - 1 });
        const line = story(event, run);
        if (line === null) continue;
        const item = feedItem({ event, run, line });
        feed.push(item);
        $("#feed").append(item.element);
      }
      shown = -1; // re-evaluate visibility on the next refresh
    },
    setCalls(run, calls, cost, errors) {
      const i = runs.indexOf(run);
      if (i < 0) return;
      run.doc.meta.llm_calls = calls;
      run.doc.meta.cost_usd = cost;
      const line = callouts[i];
      if (!line) return;
      if (calls > 0) line.textContent = `${calls} model calls, $${cost.toFixed(4)}`;
      else if (errors > 0) line.textContent = `Model calls failing (${errors})`;
      else line.textContent = "No model calls";
    },
    noteDecisions(run, records) {
      const index = new Map(run.doc.agents.map((agent, i) => [agent.id, i]));
      for (const record of records) {
        run.decisions.push(record);
        const who = run.doc.agents.find((agent) => agent.id === record.agent_id);
        if (who && record.model?.includes("/")) who.model = record.model;
        const k = index.get(record.agent_id);
        const fear = record.emotion?.fear;
        if (k === undefined || typeof fear !== "number") continue;
        run.fear[k] ??= { t: [], v: [] };
        run.fear[k].t.push(record.t_s);
        run.fear[k].v.push(fear);
      }
      refreshInterview();
    },
  };
}
