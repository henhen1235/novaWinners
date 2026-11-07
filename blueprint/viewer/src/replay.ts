// Sim replay playback (docs/SIM_INTERFACES.md replay.json): agents, clock, events feed,
// counters and the split view for ?replay=a.json&compare=b.json.
import * as THREE from "three";
import type { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import type { ViewerContext } from "./context";
import { openInterview, updateInterview, type DecisionRecord } from "./interview";
import { People, type AgentSample } from "./people";

export type ReplayEvent = { t: number; kind: string; agent?: string; ref?: string; text?: string };
export type ReplayDoc = {
  meta: { dt: number; duration_s: number; llm_calls?: number; cost_usd?: number; label?: string; run_id?: string; scenario?: string; seed?: number };
  agents: { id: string; persona: string; model?: string; color?: string }[];
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
const KIND_LABEL: Record<string, string> = {
  decision: "Decision", said: "Said", heard: "Heard", exited: "Left", blocked: "Blocked", fire_spread: "Fire", rumor: "Rumor",
};
// Agent colors follow design/DESIGN.md: calm text, moving route green, deciding amber, trapped hazard.
const STATE_TOKEN: Record<string, string> = {
  waiting: "text", hiding: "text", helping: "model", moving: "route", deciding: "warn", queued: "drafting",
  on_stairs: "drafting", waiting_at_refuge: "warn", carried: "model", at_elevator_lobby: "warn",
  trapped: "hazard-bright", hurt: "hazard-bright", blocked: "hazard-bright",
};
const STATE_LABEL: Record<string, string> = {
  waiting: "Waiting", hiding: "Hiding", helping: "Helping", moving: "Moving", deciding: "Deciding", queued: "Queued at a door",
  trapped: "Trapped", hurt: "Hurt", blocked: "Blocked", on_stairs: "On the stairs", waiting_at_refuge: "Waiting at refuge",
  carried: "Being carried", at_elevator_lobby: "At an elevator lobby", exited: "Evacuated",
};
const PERSONA_FALLBACK = ["drafting", "model", "walkable", "text-muted", "line-strong"];

async function fetchJson(url: string) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
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

export function buildRun(context: ViewerContext, url: string, source: ReplayDoc, decisions: DecisionRecord[]): Run {
  const doc = { ...source, events: source.events.map((event) => ({ ...event, text: readable(context, event.text) })) };
  const index = new Map(doc.agents.map((agent, i) => [agent.id, i]));
  const count = doc.agents.length;
  const xs: Float32Array[] = [];
  const zs: Float32Array[] = [];
  const states: string[][] = [];
  const levels: Uint8Array[] = [];
  const levelIndex = new Map(context.doc.levels.map((level, i) => [level.id, i]));
  const exitOf: (string | null)[] = new Array(count).fill(null);
  const exitTime: number[] = new Array(count).fill(Infinity);
  const exits = context.doc.exits;
  for (const frame of doc.frames) {
    const x = new Float32Array(count).fill(NaN);
    const z = new Float32Array(count).fill(NaN);
    const s: string[] = new Array(count).fill("");
    const l = new Uint8Array(count);
    for (const [id, ax, az, state, level] of frame.a) {
      const i = index.get(id);
      if (i === undefined) continue;
      x[i] = ax;
      z[i] = az;
      s[i] = state;
      l[i] = (level && levelIndex.get(level)) || 0;
      if (state === "exited" && exitOf[i] === null) {
        exitTime[i] = frame.t;
        let best = Infinity;
        for (const exit of exits.filter((item) => item.level === (level ?? item.level))) {
          const d = Math.hypot(exit.position[0] - ax, exit.position[1] - az);
          if (d < best) {
            best = d;
            exitOf[i] = exit.id;
          }
        }
      }
    }
    xs.push(x);
    zs.push(z);
    states.push(s);
    levels.push(l);
  }
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
  const name = doc.meta.label ?? url.split("/").slice(-2, -1)[0] ?? url;
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
    tag.center.set(0.5, -0.5);
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
        const [doc, decisions] = await Promise.all([fetchJson(url), fetchDecisions(url)]);
        return buildRun(context, url, doc as ReplayDoc, decisions);
      }),
    );
  } catch (error) {
    $("#readout").textContent = `Could not load the replay (${error instanceof Error ? error.message : error}). Check the path under public/.`;
    return;
  }
  // ?names=Classic agents,LLM agents overrides the run captions for the demo.
  const names = new URLSearchParams(location.search).get("names")?.split(",");
  runs.forEach((run, i) => (run.name = names?.[i]?.trim() || run.name));
  play(context, runs);
}

/** Plays loaded (or, with `live`, still growing) runs: agents, clock, feed, counters, interviews. */
export function play(context: ViewerContext, runs: Run[], live?: LiveHooks): PlayerHandle {
  runs.forEach((run, i) => {
    run.people ??= new People(context, run.doc.agents.map((agent) => agent.persona), i === 0);
    run.root.add(run.people.root);
    context.scene.add(run.root);
    addExitCounts(context, run);
  });
  runs[0].root.add(runs[0].labels);
  // In stacked and exploded views people draw over every floor so the ghosted floors never hide them.
  const onTop = (view: string) => {
    const xray = view === "stacked" || view === "exploded";
    for (const run of runs) {
      run.people.setOnTop(xray);
    }
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

  let duration = Math.max(...runs.map((run) => run.doc.meta.duration_s ?? run.doc.frames[run.doc.frames.length - 1]?.t ?? 0));
  const clock = { t: 0, playing: false, speed: 1, colorBy: "state" as "state" | "persona", last: 0 };

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
    level: "", x: 0, z: 0, world: p0, heading: 0, state: "", moving: false, fear: NaN, fill: new THREE.Color(), ring: new THREE.Color(), phase: 0,
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
    for (const tag of run.labels.children) {
      const exit = tag.userData.exit;
      if (!exit) continue;
      const used = run.exitCounts.get(exit.id)!.textContent !== "0";
      tag.visible = used && (view !== "single" || exit.level === focus);
    }
    if (run === runs[0] && levelIds.length > 1) for (const [id, n] of perFloor) context.setFloorCount(id, n);
  }


  // ---- rail and transport ----
  const scrub = $<HTMLInputElement>("#scrub");
  const clockOut = $("#clock");
  const play = $<HTMLButtonElement>("#play");
  scrub.max = String(duration);
  scrub.step = String(runs[0].doc.meta.dt || 0.5);
  $("#transport").hidden = false;
  $("#run").hidden = false;
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

  const totals: HTMLElement[] = [];
  const counters = runs.map((run) => {
    const column = document.createElement("div");
    column.className = "counter";
    if (runs.length > 1) {
      const heading = document.createElement("h3");
      heading.textContent = run.name;
      column.append(heading);
    }
    const big = document.createElement("p");
    big.className = "evacuated";
    const value = document.createElement("b");
    const total = document.createElement("span");
    total.textContent = `/ ${run.doc.agents.length}`;
    big.append(value, total);
    totals.push(total);
    const caption = document.createElement("p");
    caption.className = "counter-caption";
    caption.textContent = "evacuated";
    const cost = document.createElement("p");
    cost.className = "counter-caption";
    const calls = run.doc.meta.llm_calls ?? 0;
    cost.textContent = calls ? `${calls} model calls, $${(run.doc.meta.cost_usd ?? 0).toFixed(4)}` : "No model calls";
    column.append(big, caption, cost);
    $("#counters").append(column);
    return value;
  });

  // ---- events feed ----
  type FeedItem = { event: ReplayEvent; run: Run; element: HTMLLIElement };
  const feedItem = ({ event, run }: { event: ReplayEvent; run: Run }): FeedItem => {
      const element = document.createElement("li");
      element.className = `feed-${event.kind}`;
      element.hidden = true;
      const button = document.createElement("button");
      button.type = "button";
      const time = document.createElement("time");
      time.textContent = `${event.t.toFixed(1)} s`;
      const kind = document.createElement("b");
      kind.textContent = KIND_LABEL[event.kind] ?? event.kind;
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = [runs.length > 1 ? (run === runs[0] ? "left" : "right") : "", event.agent ?? ""].filter(Boolean).join(", ");
      const what = document.createElement("span");
      what.className = "what";
      what.textContent = event.text ?? event.ref ?? "";
      const head = document.createElement("span");
      head.className = "head";
      head.append(kind, who);
      button.append(time, head, what);
      button.addEventListener("click", () => {
        seek(event.t);
        const agent = run.doc.agents.findIndex((item) => item.id === event.agent);
        if (agent >= 0) select(run, agent);
      });
      element.append(button);
      return { event, run, element };
  };
  const feed: FeedItem[] = runs
    .flatMap((run) => run.doc.events.filter((event) => event.kind !== "heard").map((event) => ({ event, run })))
    .sort((a, b) => a.event.t - b.event.t)
    .map(feedItem);
  $("#feed").append(...feed.map((item) => item.element));
  let shown = 0;

  function refresh(force = false) {
    const t = clock.t;
    for (const run of runs) placeAgents(run, t);
    if (!force && t === clock.last) return;
    clock.last = t;
    scrub.value = String(t);
    clockOut.textContent = live ? `${t.toFixed(1)} s, live` : `${t.toFixed(1)} s / ${duration.toFixed(0)} s`;
    runs.forEach((run, r) => {
      const { i } = frameIndex(run, t);
      counters[r].textContent = String((run.states[i] ?? []).filter((state) => state === "exited").length);
      totals[r].textContent = `/ ${run.doc.agents.length}`;
    });
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
        if (event.kind === "heard") continue;
        const item = feedItem({ event, run });
        feed.push(item);
        $("#feed").append(item.element);
      }
      shown = -1; // re-evaluate visibility on the next refresh
    },
  };
}
