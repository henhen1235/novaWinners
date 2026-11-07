// Sim replay playback (docs/SIM_INTERFACES.md replay.json): agents, clock, events feed,
// counters and the split view for ?replay=a.json&compare=b.json.
import * as THREE from "three";
import type { ViewerContext } from "./context";
import { openInterview, updateInterview, type DecisionRecord } from "./interview";

export type ReplayEvent = { t: number; kind: string; agent?: string; ref?: string; text?: string };
type ReplayDoc = {
  meta: { dt: number; duration_s: number; llm_calls?: number; cost_usd?: number; label?: string; run_id?: string; scenario?: string; seed?: number };
  agents: { id: string; persona: string; model?: string; color?: string }[];
  frames: { t: number; a: [string, number, number, string][] }[];
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
  decisions: DecisionRecord[];
  live: THREE.InstancedMesh;
  ghosts: THREE.InstancedMesh;
  root: THREE.Group;
  labels: THREE.Group;
  exitOf: (string | null)[]; // exit id each agent left by, from the first "exited" frame
  exitTime: number[];
  exitCounts: Map<string, HTMLElement>;
  ring: THREE.Mesh;
}

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
  trapped: "hazard-bright", hurt: "hazard-bright", blocked: "hazard-bright",
};
const STATE_LABEL: Record<string, string> = {
  waiting: "Waiting", hiding: "Hiding", helping: "Helping", moving: "Moving", deciding: "Deciding", queued: "Queued at a door",
  trapped: "Trapped", hurt: "Hurt", blocked: "Blocked", exited: "Evacuated",
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

function buildRun(context: ViewerContext, url: string, doc: ReplayDoc, decisions: DecisionRecord[]): Run {
  const index = new Map(doc.agents.map((agent, i) => [agent.id, i]));
  const count = doc.agents.length;
  const xs: Float32Array[] = [];
  const zs: Float32Array[] = [];
  const states: string[][] = [];
  const exitOf: (string | null)[] = new Array(count).fill(null);
  const exitTime: number[] = new Array(count).fill(Infinity);
  const exits = context.doc.exits;
  for (const frame of doc.frames) {
    const x = new Float32Array(count).fill(NaN);
    const z = new Float32Array(count).fill(NaN);
    const s: string[] = new Array(count).fill("");
    for (const [id, ax, az, state] of frame.a) {
      const i = index.get(id);
      if (i === undefined) continue;
      x[i] = ax;
      z[i] = az;
      s[i] = state;
      if (state === "exited" && exitOf[i] === null) {
        exitTime[i] = frame.t;
        let best = Infinity;
        for (const exit of exits) {
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
  }
  const body = new THREE.CapsuleGeometry(0.2, 0.8, 4, 12).translate(0, 0.6, 0);
  const live = new THREE.InstancedMesh(body, new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.6 }), Math.max(count, 1));
  const ghosts = new THREE.InstancedMesh(
    body,
    new THREE.MeshBasicMaterial({ color: context.token("exit-bright"), transparent: true, opacity: 0.4, depthWrite: false }),
    Math.max(count, 1),
  );
  live.setColorAt(0, new THREE.Color());
  live.frustumCulled = false;
  ghosts.frustumCulled = false;
  ghosts.raycast = () => {};
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.32, 0.42, 40).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: context.token("text"), depthTest: false }),
  );
  ring.renderOrder = 11;
  ring.visible = false;
  const root = new THREE.Group();
  const labels = new THREE.Group();
  root.add(live, ghosts, ring);
  const name = doc.meta.label ?? doc.meta.scenario ?? url.split("/").slice(-2).join("/");
  const runId = doc.meta.run_id ?? url;
  return { url, doc, name, runId, xs, zs, states, decisions, live, ghosts, root, labels, exitOf, exitTime, exitCounts: new Map(), ring };
}

function addExitCounts(context: ViewerContext, run: Run) {
  const elevation = context.elevation();
  for (const exit of context.doc.exits.filter((item) => item.level === context.level())) {
    const tag = context.label("exit-count", (element) => {
      const value = document.createElement("b");
      value.textContent = "0";
      element.append(value, " out");
      run.exitCounts.set(exit.id, value);
    });
    tag.position.set(exit.position[0], elevation + 0.1, exit.position[1]);
    tag.center.set(0.5, -0.5);
    run.labels.add(tag);
  }
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
  for (const run of runs) {
    context.scene.add(run.root);
    addExitCounts(context, run);
  }
  runs[0].root.add(runs[0].labels);
  if (runs[1]) {
    context.split(runs[0].root, runs[1].root, runs[1].labels);
    $("#caption-a").textContent = runs[0].name;
    $("#caption-b").textContent = runs[1].name;
    $("#split-captions").hidden = false;
  }

  const duration = Math.max(...runs.map((run) => run.doc.meta.duration_s ?? run.doc.frames[run.doc.frames.length - 1]?.t ?? 0));
  const clock = { t: 0, playing: false, speed: 1, colorBy: "state" as "state" | "persona", last: 0 };
  const matrix = new THREE.Matrix4();
  const hidden = new THREE.Matrix4().makeScale(0, 0, 0);
  const color = new THREE.Color();
  const personaColors = new Map<string, THREE.Color>();
  for (const run of runs) {
    for (const agent of run.doc.agents) {
      if (personaColors.has(agent.persona)) continue;
      personaColors.set(agent.persona, agent.color ? new THREE.Color(agent.color) : context.token(PERSONA_FALLBACK[personaColors.size % PERSONA_FALLBACK.length]));
    }
  }
  const stateColor = (state: string) => context.token(STATE_TOKEN[state] ?? "text");

  function frameIndex(run: Run, t: number) {
    const dt = run.doc.meta.dt || 0.5;
    const f = (t - (run.doc.frames[0]?.t ?? 0)) / dt;
    const i = Math.max(0, Math.min(run.doc.frames.length - 1, Math.floor(f)));
    return { i, j: Math.min(i + 1, run.doc.frames.length - 1), frac: Math.max(0, Math.min(1, f - i)) };
  }

  const stateAt = (run: Run, agent: number, t: number) => run.states[frameIndex(run, t).i][agent] ?? "";

  function placeAgents(run: Run, t: number) {
    const { i, j, frac } = frameIndex(run, t);
    const y = context.elevation();
    run.doc.agents.forEach((agent, k) => {
      const x0 = run.xs[i][k];
      const z0 = run.zs[i][k];
      const state = run.states[i][k];
      if (Number.isNaN(x0)) {
        run.live.setMatrixAt(k, hidden);
        run.ghosts.setMatrixAt(k, hidden);
        return;
      }
      const x1 = Number.isNaN(run.xs[j][k]) ? x0 : run.xs[j][k];
      const z1 = Number.isNaN(run.zs[j][k]) ? z0 : run.zs[j][k];
      matrix.makeTranslation(x0 + (x1 - x0) * frac, y, z0 + (z1 - z0) * frac);
      const exited = state === "exited";
      run.live.setMatrixAt(k, exited ? hidden : matrix);
      run.ghosts.setMatrixAt(k, exited ? matrix : hidden);
      color.copy(clock.colorBy === "state" ? stateColor(state) : personaColors.get(agent.persona) ?? stateColor(""));
      run.live.setColorAt(k, color);
      if (selection && selection.run === run && selection.agent === k) {
        run.ring.visible = true;
        run.ring.position.set(x0 + (x1 - x0) * frac, y + 0.05, z0 + (z1 - z0) * frac);
      }
    });
    run.live.instanceMatrix.needsUpdate = true;
    run.ghosts.instanceMatrix.needsUpdate = true;
    if (run.live.instanceColor) run.live.instanceColor.needsUpdate = true;
    for (const [exitId, element] of run.exitCounts) {
      const value = String(run.exitOf.filter((id, k) => id === exitId && run.exitTime[k] <= t).length);
      if (element.textContent !== value) element.textContent = value;
    }
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
    if (playing && clock.t >= duration) clock.t = 0;
    play.setAttribute("aria-label", playing ? "Pause" : "Play");
    play.querySelector("span")!.textContent = playing ? "Pause" : "Play";
    play.classList.toggle("is-playing", playing);
  }
  play.addEventListener("click", () => setPlaying(!clock.playing));
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-speed]")) {
    button.addEventListener("click", () => {
      clock.speed = Number(button.dataset.speed);
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
      const seen = new Set(runs.flatMap((run) => run.states.flat()).filter(Boolean));
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
  const feed: FeedItem[] = runs
    .flatMap((run) => run.doc.events.filter((event) => event.kind !== "heard").map((event) => ({ event, run })))
    .sort((a, b) => a.event.t - b.event.t)
    .map(({ event, run }) => {
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
      who.textContent = [runs.length > 1 ? run.name : "", event.agent ?? ""].filter(Boolean).join(", ");
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
    });
  $("#feed").append(...feed.map((item) => item.element));
  let shown = 0;

  function refresh(force = false) {
    const t = clock.t;
    for (const run of runs) placeAgents(run, t);
    if (!force && t === clock.last) return;
    clock.last = t;
    scrub.value = String(t);
    clockOut.textContent = `${t.toFixed(1)} s / ${duration.toFixed(0)} s`;
    runs.forEach((run, r) => {
      const { i } = frameIndex(run, t);
      counters[r].textContent = String(run.states[i].filter((state) => state === "exited").length);
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
    run.live.computeBoundingSphere();
    const hit = raycaster.intersectObject(run.live, false)[0];
    if (hit?.instanceId === undefined) return false;
    select(run, hit.instanceId);
    return true;
  });

  let previous = performance.now();
  context.onFrame((now) => {
    const elapsed = Math.min((now - previous) / 1000, 0.1); // a hidden tab must not jump the clock
    previous = now;
    if (clock.playing) {
      clock.t = Math.min(duration, clock.t + elapsed * clock.speed);
      if (clock.t >= duration) setPlaying(false);
    }
    refresh();
  });

  drawLegend();
  refresh(true);
  if (new URLSearchParams(location.search).has("autoplay")) setPlaying(true);
}
