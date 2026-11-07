// Live mode (docs/LIVE_MODE.md section 5, wire format in docs/SIM_INTERFACES.md "Live mode"):
// ?live=ws://127.0.0.1:8790/ws talks to `python -m sim.serve`; ?live=mock runs an in-browser stand-in
// that streams an existing replay through the same binary format, so the UI works without the server.
// Frames are appended to an ordinary Run and played ~1 s behind the stream with interpolation.
import type { ViewerContext } from "./context";
import { buildRun, play, type PlayerHandle, type ReplayDoc, type ReplayEvent, type Run } from "./replay";
import { People } from "./people";
import { clearPlacedPeople, getDraft, type Occupant } from "./edit";

type Meta = {
  type: "meta";
  session: string;
  branch: number;
  dt: number;
  duration_s: number;
  levels: string[];
  states: string[];
  agents: { index: number; id: string; persona: string; model?: string; color?: string; speed?: number }[];
};
type Message =
  | Meta
  | { type: "agents"; agents: Meta["agents"] }
  | { type: "events"; events: ReplayEvent[] }
  | { type: "status"; t: number; speed?: number; warp?: number; paused?: boolean; pending?: number; evacuated?: number; total?: number; decisions?: number; llm_calls?: number; cost_usd?: number; model_errors?: number }
  | { type: "log"; records: import("./interview").DecisionRecord[] }
  | { type: "edit_ok" }
  | { type: "error"; for?: string; errors?: unknown }
  | { type: "finished" }
  | { type: "ended"; run_dir: string };

interface Socket {
  send: (data: string) => void;
  close: () => void;
  onmessage: ((event: { data: unknown }) => void) | null;
  onopen: (() => void) | null;
  onclose: (() => void) | null;
  readyState: number;
}

declare const __REPO_ROOT__: string; // vite.config.ts define

const $ = <T extends HTMLElement>(selector: string) => document.querySelector<T>(selector)!;

/** Binary frame: float32 t, uint32 n, float32[2n] x,z, uint8[n] state code, uint8[n] level index (little-endian). */
export function decodeFrame(buffer: ArrayBuffer) {
  const view = new DataView(buffer);
  const t = view.getFloat32(0, true);
  const n = view.getUint32(4, true);
  const xz = new Float32Array(buffer.slice(8, 8 + 8 * n));
  const states = new Uint8Array(buffer, 8 + 8 * n, n);
  const levels = new Uint8Array(buffer, 8 + 9 * n, n);
  return { t, n, xz, states, levels };
}

export function encodeFrame(t: number, xz: Float32Array, states: Uint8Array, levels: Uint8Array) {
  const n = states.length;
  const buffer = new ArrayBuffer(8 + 10 * n);
  const view = new DataView(buffer);
  view.setFloat32(0, t, true);
  view.setUint32(4, n, true);
  for (let i = 0; i < 2 * n; i += 1) view.setFloat32(8 + 4 * i, xz[i], true);
  new Uint8Array(buffer, 8 + 8 * n, n).set(states);
  new Uint8Array(buffer, 8 + 9 * n, n).set(levels);
  return buffer;
}

// ---- in-browser stand-in for sim.serve: streams a recorded replay through the wire format ----
const MOCK_STATES = ["waiting", "moving", "deciding", "queued", "exited", "helping", "hiding", "waiting_at_refuge", "carried", "at_elevator_lobby"];

class MockServer implements Socket {
  readyState = 0;
  onmessage: Socket["onmessage"] = null;
  onopen: Socket["onopen"] = null;
  onclose: Socket["onclose"] = null;
  private timer = 0;
  private t = 0;
  private speed = 1;
  private paused = false;
  private eventAt = 0;
  private source: ReplayDoc | null = null;
  private extra: ReplayEvent[] = [];

  constructor(private readonly replayUrl: string, private readonly levels: string[]) {
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.();
    }, 50);
  }

  private emit(message: object | ArrayBuffer) {
    this.onmessage?.({ data: message instanceof ArrayBuffer ? message : JSON.stringify(message) });
  }

  send(data: string) {
    const message = JSON.parse(data);
    if (message.type === "start") void this.start(message.speed ?? 1);
    else if (message.type === "pause") this.paused = true;
    else if (message.type === "resume") this.paused = false;
    else if (message.type === "speed") this.speed = message.value;
    else if (message.type === "edit") {
      // The stand-in cannot re-simulate; it acknowledges and logs the edit so the flow is visible.
      this.emit({ type: "edit_ok" });
      const what = [...(message.events ?? []).map((event: { type: string }) => event.type.replace(/_/g, " ")), ...(message.occupants ?? []).map((group: { count: number }) => `${group.count} people`)];
      this.extra.push({ t: this.t, kind: "decision", text: `Edit applied (mock): ${what.join(", ")}` });
    } else if (message.type === "end") this.end();
  }

  private async start(speed: number) {
    const response = await fetch(this.replayUrl);
    if (!response.ok) {
      this.emit({ type: "error", for: "start", errors: [`mock replay ${this.replayUrl}: HTTP ${response.status}`] });
      return;
    }
    const source = (await response.json()) as ReplayDoc;
    this.source = source;
    this.speed = speed;
    this.emit({
      type: "meta", session: "mock", branch: 0, dt: source.meta.dt, duration_s: source.meta.duration_s,
      levels: this.levels, states: MOCK_STATES,
      agents: source.agents.map((agent, index) => ({ index, ...agent })),
    } satisfies Meta);
    this.timer = window.setInterval(() => this.tick(), 200); // 5 Hz, like the server
  }

  private tick() {
    const source = this.source!;
    if (!this.paused) this.t += 0.2 * this.speed;
    const frames = source.frames;
    const frame = frames[Math.min(frames.length - 1, Math.floor(this.t / (source.meta.dt || 0.5)))];
    const index = new Map(source.agents.map((agent, k) => [agent.id, k]));
    const n = source.agents.length;
    const xz = new Float32Array(2 * n).fill(NaN);
    const states = new Uint8Array(n);
    const levels = new Uint8Array(n);
    for (const [id, x, z, state, level] of frame.a) {
      const k = index.get(id)!;
      xz[2 * k] = x;
      xz[2 * k + 1] = z;
      states[k] = Math.max(0, MOCK_STATES.indexOf(state));
      levels[k] = Math.max(0, this.levels.indexOf(level ?? this.levels[0]));
    }
    this.emit(encodeFrame(frame.t, xz, states, levels));
    const events = [...source.events.filter((event) => event.t > this.eventAt && event.t <= frame.t), ...this.extra.splice(0)];
    this.eventAt = frame.t;
    if (events.length) this.emit({ type: "events", events });
    const exited = Array.from(states).filter((code) => MOCK_STATES[code] === "exited").length;
    const pending = Array.from(states).filter((code) => MOCK_STATES[code] === "deciding").length;
    this.emit({ type: "status", t: frame.t, speed: this.speed, warp: 1, paused: this.paused, pending, evacuated: exited, total: n });
    if (frame === frames[frames.length - 1]) {
      this.emit({ type: "finished" });
      this.paused = true;
    }
  }

  private end() {
    window.clearInterval(this.timer);
    // The recorded replay is the "branch" a real session would write.
    this.emit({ type: "ended", run_dir: `blueprint/viewer/public${this.replayUrl.replace(/\/replay\.json$/, "")}` });
  }

  close() {
    window.clearInterval(this.timer);
    this.readyState = 3;
    this.onclose?.();
  }
}

// ---- the live session ----
export function startLive(context: ViewerContext, target: string | null) {
  if (target === null) return;
  const params = new URLSearchParams(location.search);
  const sceneUrl = params.get("scene") ?? "/office/scene.json";
  const levelIds = context.doc.levels.map((level) => level.id);
  const section = $("#live");
  const status = $("#live-status");
  const startButton = $<HTMLButtonElement>("#live-start");
  const endButton = $<HTMLButtonElement>("#live-end");
  const result = $("#live-result");
  section.hidden = false;

  const url = target === "" ? "ws://127.0.0.1:8790/ws" : target;
  const mock = url === "mock";
  let socket: Socket = { send() {}, close() {}, onmessage: null, onopen: null, onclose: null, readyState: 0 };
  const send = (message: object) => socket.readyState === 1 && socket.send(JSON.stringify(message));
  status.textContent = "Connecting";

  let meta: Meta | null = null;
  let run: Run | null = null;
  let player: PlayerHandle | null = null;
  let latest = 0;
  let states: string[] = [];
  let metaLevels: number[] = []; // server level index -> scene level index
  const personas: string[] = [];
  const CAPACITY_HEADROOM = 200; // people added during the run

  let pendingStart = false;

  // Used only when the sidebar has not placed anyone. Same rooms as sim/examples/tepper_fire.json.
  const TEPPER_OCCUPANTS = [
    { where: { room: "r_5254" }, count: 20, personas: { regular: 0.5, visitor: 0.3, staff: 0.1, older_adult: 0.1 } },
    { where: { room: "r_OTB5-1" }, count: 20, personas: { regular: 0.6, visitor: 0.3, skeptic: 0.1 } },
    { where: { room: "c_4_001" }, count: 25, personas: { regular: 0.6, visitor: 0.2, staff: 0.1, mobility_impaired: 0.1 } },
    { where: { room: "r_4242" }, count: 15, personas: { regular: 0.7, staff: 0.1, parent: 0.2 } },
    { where: { room: "c_3_002" }, count: 30, personas: { regular: 0.5, visitor: 0.4, staff: 0.1 } },
    { where: { room: "r_2602" }, count: 30, personas: { regular: 0.4, visitor: 0.5, staff: 0.1 } },
    { where: { room: "r_1042" }, count: 30, personas: { regular: 0.4, visitor: 0.5, staff: 0.1 } },
  ];

  function defaultOccupants() {
    if (context.doc.rooms.some((room) => room.id === "r_3500")) return TEPPER_OCCUPANTS;
    const rooms = [...context.doc.rooms].filter((room) => (room.area_m2 ?? 0) >= 12)
      .sort((a, b) => (b.area_m2 ?? 0) - (a.area_m2 ?? 0)).slice(0, 16);
    const chosen = rooms.length ? rooms : context.doc.rooms.slice(0, 8);
    return chosen.filter((room) => room.id).map((room) => ({
      where: { room: room.id },
      count: Math.max(1, Math.min(6, Math.round((room.area_m2 ?? 12) / 25))),
      personas: { regular: 0.6, visitor: 0.25, staff: 0.15 },
    }));
  }

  function scenario() {
    const draft = getDraft();
    const occupants = draft.occupants.length ? draft.occupants : defaultOccupants();
    const hasAlarm = draft.events.some((event) => event.type === "alarm_only" || event.type === "fire");
    const events = hasAlarm ? draft.events : [
      { id: "ev_alarm", type: "alarm_only", t_start_s: 0, at: { room: context.doc.rooms[0]?.id }, params: {} },
      ...draft.events,
    ];
    return {
      schema_version: "0.1",
      scene: `blueprint/viewer/public${sceneUrl}`,
      seed: Math.floor(Math.random() * 1_000_000_000),
      duration_s: 600,
      events,
      occupants: personalize(occupants),
      assumptions: ["Started from the viewer"],
    };
  }

  // A shared slider speed is the center of the group. Each person draws their own pace around it.
  function personalize(groups: Occupant[]) {
    return groups.flatMap((group) => {
      const count = group.count ?? 1;
      const mean = Number(group.traits?.speed_mps);
      if (!Number.isFinite(mean) || count <= 1) return [group];
      return Array.from({ length: count }, () => {
        const speed = Math.min(2, Math.max(0.3, mean * (0.82 + Math.random() * 0.36)));
        return { ...group, count: 1, traits: { ...group.traits, speed_mps: Math.round(speed * 100) / 100 } };
      });
    });
  }

  function sendStart() {
    const draft = getDraft();
    const people = (draft.occupants.length ? draft.occupants : defaultOccupants())
      .reduce((sum, group) => sum + (group.count ?? 1), 0);
    startButton.disabled = true;
    endButton.disabled = false;
    const built = scenario();
    send({
      type: "start", scene: params.get("livescene") ?? `blueprint/viewer/public${sceneUrl}`, scenario: built, seed: built.seed,
      decide: params.get("decide") ?? "cognition", no_llm: params.get("decide") === "policy", speed: 1,
    });
    const notes = [];
    if (draft.events.length) notes.push(`${draft.events.length} sidebar event${draft.events.length === 1 ? "" : "s"}`);
    if (draft.occupants.length) notes.push("the people you placed");
    clearPlacedPeople();
    const rulesOnly = params.get("decide") === "policy";
    $("#live-mode").textContent = rulesOnly
      ? "This run uses the built-in rules."
      : "Each person asks openai/gpt-oss-120b.";
    status.textContent = notes.length ? `Starting ${people} people with ${notes.join(" and ")}` : `Starting ${people} people`;
    pendingStart = false;
  }

  function connect() {
    socket = mock
      ? new MockServer(params.get("mock") ?? "/replays/tepper_alarm/replay.json", levelIds)
      : (new WebSocket(url) as unknown as Socket);
    if (!mock) (socket as unknown as WebSocket).binaryType = "arraybuffer";
    socket.onopen = () => {
      status.textContent = mock ? "Ready. Press Start simulation." : "Ready. Press Start simulation.";
      startButton.disabled = false;
      if (pendingStart) sendStart();
    };
    socket.onclose = () => {
      if (run) {
        status.textContent = "Disconnected";
        startButton.disabled = true;
        return;
      }
      status.textContent = "Waiting for the simulation server";
      startButton.disabled = true;
      if (!mock) window.setTimeout(connect, 1500);
    };
    socket.onmessage = onSocketMessage;
  }

  startButton.addEventListener("click", () => {
    if (socket.readyState !== 1) {
      pendingStart = true;
      status.textContent = "Connecting, then starting";
      return;
    }
    sendStart();
  });
  endButton.addEventListener("click", () => {
    send({ type: "end" });
    endButton.disabled = true;
  });

  // Edits from the editing panel go to the running sim; before the start they stay in the scenario draft.
  window.addEventListener("nova:edit", (event) => {
    if (!run || socket.readyState !== 1) return;
    const detail = (event as CustomEvent).detail ?? {};
    const events = detail.events ?? (detail.event ? [detail.event] : []);
    const occupants = Array.isArray(detail.occupants) ? detail.occupants : detail.occupants ? [detail.occupants] : [];
    send({ type: "edit", events, occupants });
    event.preventDefault();
  });

  function addAgents(agents: Meta["agents"]) {
    if (!run) return;
    for (const agent of agents) {
      run.doc.agents[agent.index] = { id: agent.id, persona: agent.persona, model: agent.model, color: agent.color, speed: agent.speed };
      personas[agent.index] = agent.persona;
      run.fear[agent.index] = { t: [], v: [] };
    }
  }

  function begin(message: Meta) {
    meta = message;
    states = message.states;
    metaLevels = message.levels.map((id) => Math.max(0, levelIds.indexOf(id)));
    const doc: ReplayDoc = {
      meta: { dt: message.dt, duration_s: message.duration_s, run_id: `live_${message.session}`, label: "Live run" },
      agents: [], frames: [], events: [],
    };
    run = buildRun(context, `/live/${message.session}/replay.json`, doc, []);
    const capacity = message.agents.length + CAPACITY_HEADROOM;
    personas.length = capacity;
    personas.fill("");
    run.headings = new Float32Array(capacity);
    run.exitOf = new Array(capacity).fill(null);
    run.exitTime = new Array(capacity).fill(Infinity);
    addAgents(message.agents);
    run.people = new People(context, personas, true);
    player = play(context, [run], {
      onPlay: (playing) => send({ type: playing ? "resume" : "pause" }),
      onSpeed: (speed) => send({ type: "speed", value: speed }),
      latest: () => latest,
    });
    document.querySelector<HTMLButtonElement>("#play")?.click(); // a live run plays from the start
    document.querySelector<HTMLButtonElement>('[data-view="side"]')?.click(); // spread the floors so nobody is buried in the stack
  }

  function addFrame(buffer: ArrayBuffer) {
    if (!run) return;
    const frame = decodeFrame(buffer);
    const x = new Float32Array(frame.n);
    const z = new Float32Array(frame.n);
    const s: string[] = new Array(frame.n);
    const l = new Uint8Array(frame.n);
    for (let k = 0; k < frame.n; k += 1) {
      x[k] = frame.xz[2 * k];
      z[k] = frame.xz[2 * k + 1];
      s[k] = states[frame.states[k]] ?? "";
      l[k] = metaLevels[frame.levels[k]] ?? 0;
      if (s[k] === "exited" && run.exitOf[k] === null) {
        run.exitTime[k] = frame.t;
        let best = Infinity;
        for (const exit of context.doc.exits.filter((item) => item.level === levelIds[l[k]])) {
          const d = Math.hypot(exit.position[0] - x[k], exit.position[1] - z[k]);
          if (d < best) [best, run.exitOf[k]] = [d, exit.id];
        }
      }
    }
    if (run.doc.frames.length && frame.t <= run.doc.frames[run.doc.frames.length - 1].t) return; // out of order
    run.doc.frames.push({ t: frame.t, a: [] });
    run.xs.push(x);
    run.zs.push(z);
    run.states.push(s);
    run.levels.push(l);
    latest = frame.t;
  }

  function onSocketMessage({ data }: { data: unknown }) {
    if (data instanceof ArrayBuffer) return addFrame(data);
    if (data instanceof Blob) return void data.arrayBuffer().then(addFrame);
    const message = JSON.parse(String(data)) as Message;
    if (message.type === "meta") {
      if (!run) begin(message);
      status.textContent = `Running, branch ${message.branch}`;
    } else if (message.type === "agents") addAgents(message.agents);
    else if (message.type === "log" && run && player) player.noteDecisions(run, message.records);
    else if (message.type === "events" && run && player) player.addEvents(run, message.events);
    else if (message.type === "status") {
      const parts = [`${message.t.toFixed(1)} s`, `${message.evacuated ?? 0} of ${message.total ?? 0} out`];
      if (message.pending) parts.push(`${message.pending} thinking`);
      if (message.warp && message.warp < 0.99) parts.push(`slowed to ${message.warp.toFixed(2)}x while the model answers`);
      if (message.paused) parts.push("paused");
      status.textContent = parts.join(", ");
      if (player && run) player.setCalls(run, message.llm_calls ?? 0, message.cost_usd ?? 0, message.model_errors ?? 0);
    } else if (message.type === "edit_ok") status.textContent = "Edit applied at the next tick";
    else if (message.type === "error") {
      status.textContent = `Server error${message.for ? ` (${message.for})` : ""}: ${JSON.stringify(message.errors ?? "")}`;
      if (message.for === "start" && !run) {
        startButton.disabled = false;
        endButton.disabled = true;
      }
    }
    else if (message.type === "finished") status.textContent = "Run finished; press End to write the replay";
    else if (message.type === "ended") showResult(message.run_dir);
  }

  connect();

  // The branch's replay is exact (recorded decisions), so the end of a live run is a normal replay link.
  function showResult(runDir: string) {
    result.replaceChildren();
    const marker = "blueprint/viewer/public";
    const at = runDir.indexOf(marker);
    // Under public/ it is served as is; anywhere else in the repo (out/live) it goes through vite's /@fs/.
    const absolute = runDir.startsWith("/") ? runDir : `${__REPO_ROOT__}/${runDir}`;
    const base = (at >= 0 ? runDir.slice(at + marker.length) : `/@fs${absolute}`).replace(/\/$/, "");
    const link = document.createElement("a");
    const query = new URLSearchParams({ scene: sceneUrl, glb: params.get("glb") ?? "", replay: `${base}/replay.json` });
    link.href = `?${query}`;
    link.textContent = "Open this run as a replay";
    result.append(link);
    status.textContent = `Ended${meta ? `, session ${meta.session}` : ""}`;
  }
}
