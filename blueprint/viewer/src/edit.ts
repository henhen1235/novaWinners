// Editing UI (docs/LIVE_MODE.md section 6): event palette, click and type, chat and people placement.
// Every edit goes out as a cancelable "nova:edit" window event; the live module calls preventDefault
// when it forwarded the edit to a running sim, otherwise the edit joins the scenario draft.
import * as THREE from "three";
import type { SceneDoc, ViewerContext } from "./context";
import "./edit.css";

type At = { room?: string; exit?: string; door?: string; stair?: string; point?: number[]; level?: string };
export type ScenarioEvent = { id: string; type: string; t_start_s: number; at: At; params: Record<string, unknown> };
export type Occupant = {
  where: { point: number[]; level: string } | { room: string };
  count: number;
  personas: Record<string, number>;
  traits?: Record<string, unknown>;
};
type Hit = { level: string; x: number; z: number };
type Person = Hit & { persona: string };
type Item = { label: string; sent: boolean; event?: ScenarioEvent; occupants?: Occupant[]; people: Person[]; marker?: THREE.Object3D };
type Tool = "fire" | "smoke" | "block" | "rumor" | "pa" | "open" | "type" | "people";

const TOOLS: [Tool, string, string][] = [
  ["fire", "Fire", "Start a fire in the room you click"],
  ["smoke", "Smoke", "Fill the room you click with smoke"],
  ["block", "Block", "Block the nearest exit, door or stair"],
  ["open", "Open exit", "Open the nearest exit"],
  ["rumor", "Rumor", "Start a rumor where you click"],
  ["pa", "PA", "Play a PA announcement"],
  ["type", "Click and type", "Describe an event in words, then click where it happens"],
  ["people", "People", "Click to place one person, drag a box to spread several"],
];
const HINTS: Record<Tool, string> = {
  fire: "Click a room to start a fire.",
  smoke: "Click a room to fill it with smoke.",
  block: "Click near an exit, door or stair to block it.",
  open: "Click near an exit to open it.",
  rumor: "Edit the rumor, then click where it starts.",
  pa: "Edit the announcement, then click anywhere on the plan.",
  type: "Describe what happens, then click the spot.",
  people: "Click to place one person, or drag a box to spread the count.",
};
const MESSAGE_DEFAULT: Partial<Record<Tool, string>> = {
  rumor: "The east exit is locked",
  pa: "Please leave by the nearest exit",
};
// Event types from scenario/schema.py EVENT_TYPES (pa_announcement and open_exit are schema types too).
const EVENT_TYPE: Partial<Record<Tool, string>> = {
  fire: "fire", smoke: "smoke", block: "blocked_exit", open: "open_exit", rumor: "rumor", pa: "pa_announcement",
};
const TYPE_LABEL: Record<string, string> = {
  fire: "Fire", smoke: "Smoke", blocked_exit: "Blocked", open_exit: "Open exit", rumor: "Rumor",
  pa_announcement: "PA", alarm_only: "Alarm", active_intruder: "Intruder",
};
// Persona colors stay in the blue-gray family: green, red and amber are reserved for exit, fire and warning.
const PERSONAS: [string, string, string, number][] = [
  ["staff", "Staff", "text", 0],
  ["regular", "Regular", "drafting", 0],
  ["visitor", "Visitor", "model", 0],
  ["older_adult", "Older adult", "walkable", 0],
  ["mobility_impaired", "Mobility impaired", "line-strong", 0],
  ["parent_with_child", "Parent with child", "text-muted", 0],
  ["skeptic", "Skeptic", "drafting", -0.2],
  ["committed_task", "Committed to a task", "walkable", 0.2],
  ["fruit_fly", "Fruit fly", "model", -0.3],
];
// [key, label, min, max, step, default]
const TRAITS: [string, string, number, number, number, number][] = [
  ["speed_mps", "Walking speed (m/s)", 0.3, 2, 0.05, 1.25],
  ["exit_familiarity", "Exit familiarity", 0, 1, 0.05, 0.5],
  ["sign_follow_p", "Sign following", 0, 1, 0.05, 0.6],
  ["risk_tolerance", "Risk tolerance", 0, 1, 0.05, 0.5],
];
const OCEAN = [["O", "Openness"], ["C", "Conscientiousness"], ["E", "Extraversion"], ["A", "Agreeableness"], ["N", "Neuroticism"]];
const MARKER_PX = 9;
const DOT_PX = 8;
const DRAG_PX = 5;
const MAX_DOTS_PER_GROUP = 300;
const SCENE = new URLSearchParams(location.search).get("scene") ?? "";
// scenario/serve.py wants a repo-relative scene path; viewer scenes are served from public/.
const SCENE_PATH = SCENE.startsWith("/") ? `blueprint/viewer/public${SCENE}` : SCENE;

let draft: { events: ScenarioEvent[]; occupants: Occupant[] } = { events: [], occupants: [] };

/** Copy of the edits that were not sent to a live run (the scenario draft). */
export function getDraft() {
  return { events: [...draft.events], occupants: [...draft.occupants] };
}

// ---- geometry helpers ----
function inside(poly: number[][], x: number, z: number) {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i], [xj, zj] = poly[j];
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) hit = !hit;
  }
  return hit;
}
const centroid = (poly: number[][]) => poly.reduce((s, p) => [s[0] + p[0] / poly.length, s[1] + p[1] / poly.length], [0, 0]);

function roomAt(doc: SceneDoc, hit: Hit) {
  return doc.rooms.find((room) => room.level === hit.level && room.type !== "outside" && inside(room.polygon, hit.x, hit.z));
}

/** Nearest exit (and, unless exitsOnly, door or stair) to the hit on its level. */
function nearestTarget(doc: SceneDoc, hit: Hit, exitsOnly: boolean): { at: At; name: string; dist: number } | null {
  const exitDoors = new Set(doc.exits.map((exit) => exit.door_id));
  // Upper floors often have no exits of their own; then any exit in plan distance (the way out downstairs).
  const sameLevel = doc.exits.filter((exit) => exit.level === hit.level);
  const options: { at: At; name: string; p: number[] }[] = (sameLevel.length ? sameLevel : doc.exits)
    .map((exit) => ({ at: { exit: exit.id }, name: exit.label, p: exit.position }));
  if (!exitsOnly) {
    for (const door of doc.doors) {
      if (door.level !== hit.level || exitDoors.has(door.id)) continue;
      options.push({ at: { door: door.id }, name: `door ${door.id}`, p: centroid(door.segment) });
    }
    for (const stair of doc.stairs ?? []) {
      if (stair.from_level !== hit.level && stair.to_level !== hit.level) continue;
      options.push({ at: { stair: stair.id }, name: `stair ${stair.id}`, p: centroid(stair.polygon) });
    }
  }
  let best: { at: At; name: string; dist: number } | null = null;
  for (const option of options) {
    const dist = Math.hypot(option.p[0] - hit.x, option.p[1] - hit.z);
    if (!best || dist < best.dist) best = { at: option.at, name: option.name, dist };
  }
  return best;
}

/** Floor position of an event's `at` (null when the id is not in the scene). */
function atPosition(doc: SceneDoc, at: At): Hit | null {
  const find = <T extends { id: string }>(list: T[] | undefined, id?: string) => (id ? list?.find((item) => item.id === id) : undefined);
  const exit = find(doc.exits, at.exit);
  if (exit) return { level: exit.level, x: exit.position[0], z: exit.position[1] };
  const door = find(doc.doors, at.door);
  if (door) { const [x, z] = centroid(door.segment); return { level: door.level, x, z }; }
  const stair = find(doc.stairs, at.stair);
  if (stair) { const [x, z] = centroid(stair.polygon); return { level: stair.from_level, x, z }; }
  const room = find(doc.rooms, at.room);
  if (room) return { level: room.level, x: room.centroid[0], z: room.centroid[1] };
  if (at.point && at.level) return { level: at.level, x: at.point[0], z: at.point[1] };
  return null;
}

/** Up to n random points in [x0,x1]x[z0,z1] that fall inside a room of the level (uniform if it has none). */
function samplePoints(doc: SceneDoc, level: string, x0: number, x1: number, z0: number, z1: number, n: number) {
  const rooms = doc.rooms.filter((room) => room.level === level && room.type !== "outside");
  const out: Hit[] = [];
  for (let tries = 0; out.length < n && tries < n * 60; tries++) {
    const x = x0 + Math.random() * (x1 - x0), z = z0 + Math.random() * (z1 - z0);
    if (!rooms.length || rooms.some((room) => inside(room.polygon, x, z))) out.push({ level, x, z });
  }
  return out;
}

const round = (v: number) => Math.round(v * 100) / 100;

export function startEdit(context: ViewerContext) {
  const doc = context.doc;
  const levelName = (id: string) => doc.levels.find((level) => level.id === id)?.name ?? id;
  let tool: Tool | null = null;
  let lastHit: Hit | null = null;
  let eventCount = 0;
  let chat: { role: "user" | "assistant"; content: string }[] = [];
  const items: Item[] = [];
  const canvas = document.querySelector<HTMLCanvasElement>("#stage canvas");
  const stage = document.querySelector<HTMLElement>("#stage");
  // The camera that last rendered is the active one (perspective, or orthographic in plan views).
  let activeCamera: THREE.Camera = context.camera;

  // ---- rail section ----
  const section = document.createElement("section");
  section.className = "edit";
  section.setAttribute("aria-labelledby", "edit-title");
  section.innerHTML = `
    <h2 id="edit-title">Edit scenario</h2>
    <div class="views edit-tools" role="group" aria-label="Edit tools">
      ${TOOLS.map(([id, name, title]) => `<button type="button" data-tool="${id}" aria-pressed="false" title="${title}">${name}</button>`).join("")}
    </div>
    <label class="edit-field" data-for="rumor pa" hidden><span>Message</span><input id="edit-message" type="text" maxlength="200" /></label>
    <label class="edit-field" data-for="type" hidden><span>Describe what happens here</span>
      <input id="edit-describe" type="text" maxlength="300" placeholder="Fire spreading from here" /></label>
    <div class="edit-people" data-for="people" hidden>
      <label class="edit-field"><span>Persona <i class="edit-swatch"></i></span><select id="edit-persona">
        ${PERSONAS.map(([id, name]) => `<option value="${id}">${name}</option>`).join("")}
      </select></label>
      <label class="edit-field edit-count"><span>People per box</span><input id="edit-count" type="number" min="1" max="200" value="10" /></label>
      <details open><summary>Traits</summary>
        ${TRAITS.map(([key, name, min, max, step, value]) => slider(key, name, min, max, step, value)).join("")}
        ${OCEAN.map(([key, name]) => slider(`ocean-${key}`, `${name} (${key})`, 0, 1, 0.05, 0.5)).join("")}
      </details>
    </div>
    <p id="edit-hint" class="edit-hint" aria-live="polite">Pick a tool, then click the plan. Escape puts it down.</p>
    <h3>Chat</h3>
    <ol id="edit-chat" class="edit-chat" aria-live="polite"></ol>
    <form id="edit-chat-form" class="edit-chat-form">
      <input id="edit-chat-input" type="text" maxlength="500" aria-label="Message to the scenario assistant" placeholder="Smoke in the lobby at 2 minutes" />
      <button type="submit">Send</button>
    </form>
    <h3>Changes</h3>
    <p class="edit-empty" id="edit-empty">No changes yet.</p>
    <ol id="edit-list" class="edit-list"></ol>
    <button id="edit-download" type="button" class="edit-download">Download scenario.json</button>`;
  const routeSection = document.querySelector("aside section.route");
  if (routeSection) routeSection.before(section);
  else document.querySelector("aside")?.append(section);

  const $ = <T extends Element>(selector: string) => section.querySelector<T>(selector)!;
  const hint = $<HTMLParagraphElement>("#edit-hint");
  const message = $<HTMLInputElement>("#edit-message");
  const describe = $<HTMLInputElement>("#edit-describe");
  const persona = $<HTMLSelectElement>("#edit-persona");
  const count = $<HTMLInputElement>("#edit-count");
  const swatch = $<HTMLElement>(".edit-swatch");
  const list = $<HTMLOListElement>("#edit-list");
  const chatLog = $<HTMLOListElement>("#edit-chat");

  for (const input of section.querySelectorAll<HTMLInputElement>("input[type=range]")) {
    input.addEventListener("input", () => (input.nextElementSibling!.textContent = input.value));
  }
  const personaColor = (id: string) => {
    const [, , token, shift] = PERSONAS.find((p) => p[0] === id) ?? PERSONAS[1];
    return context.token(token).offsetHSL(0, 0, shift);
  };
  const paintSwatch = () => (swatch.style.background = `#${personaColor(persona.value).getHexString()}`);
  persona.addEventListener("change", paintSwatch);
  paintSwatch();

  // ---- tools ----
  function arm(next: Tool | null) {
    const was = tool;
    tool = next === tool ? null : next;
    // Left-drag draws the people box, so the camera only moves while the people tool is down.
    if (was === "people" && tool !== "people") context.setNavigation(true);
    if (tool === "people" && was !== "people") context.setNavigation(false);
    for (const button of section.querySelectorAll<HTMLButtonElement>("[data-tool]")) {
      button.setAttribute("aria-pressed", String(button.dataset.tool === tool));
    }
    for (const panel of section.querySelectorAll<HTMLElement>("[data-for]")) {
      panel.hidden = !tool || !panel.dataset.for!.split(" ").includes(tool);
    }
    if (tool && MESSAGE_DEFAULT[tool]) message.value = MESSAGE_DEFAULT[tool]!;
    canvas?.classList.toggle("edit-armed", !!tool);
    hint.textContent = tool ? HINTS[tool] : "Pick a tool, then click the plan. Escape puts it down.";
  }
  section.querySelector(".edit-tools")!.addEventListener("click", (event) => {
    const button = (event.target as Element).closest<HTMLButtonElement>("[data-tool]");
    if (button) arm(button.dataset.tool as Tool);
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && tool) arm(null);
  });

  /** Event of `type` at the clicked floor point, snapped to what the type needs. */
  function buildEvent(type: string, hit: Hit, text?: string): { event: ScenarioEvent; note: string } | { error: string } {
    let at: At;
    let note = "";
    if (type === "blocked_exit" || type === "open_exit") {
      const target = nearestTarget(doc, hit, type === "open_exit");
      if (!target) return { error: `No ${type === "open_exit" ? "exit" : "exit, door or stair"} on ${levelName(hit.level)}.` };
      at = target.at;
      note = ` (${target.dist.toFixed(1)} m from the click)`;
    } else {
      const room = roomAt(doc, hit);
      at = room ? { room: room.id } : { point: [round(hit.x), round(hit.z)], level: hit.level };
    }
    const params: Record<string, unknown> = {};
    if (type === "rumor" || type === "pa_announcement") {
      params.text = text?.trim() || MESSAGE_DEFAULT[type === "rumor" ? "rumor" : "pa"];
      if (type === "rumor") params.radius_m = 8;
      // A message about a locked or open exit claims the exit nearest the click, so the sim can act on it.
      const exit = nearestTarget(doc, hit, true);
      if (exit?.at.exit && /locked|blocked|closed|jammed/i.test(String(params.text))) params.claim = { exit_blocked: exit.at.exit };
      else if (exit?.at.exit && /\bopen\b/i.test(String(params.text))) params.claim = { exit_open: exit.at.exit };
    }
    return { event: { id: `ev_edit_${++eventCount}`, type, t_start_s: 0, at, params }, note };
  }

  /** Keyword parser used when the scenario server is not running. */
  function localParse(text: string, hit: Hit) {
    const rules: [RegExp, string][] = [
      [/rumou?r|heard|someone said|people say/i, "rumor"],
      [/\bpa\b|announce|speaker|intercom/i, "pa_announcement"],
      [/fire|flame|burn/i, "fire"],
      [/smoke/i, "smoke"],
      [/block|locked|jammed|closed|obstruct/i, "blocked_exit"],
      [/\bopen/i, "open_exit"],
    ];
    const type = rules.find(([pattern]) => pattern.test(text))?.[1];
    if (!type) return null;
    const message = text.replace(/^\s*(rumou?r|pa|announcement)\s*:\s*/i, "");
    return buildEvent(type, hit, message);
  }

  /** Where a sentence points when there is no click: a named room or exit, else the last click. */
  function locate(text: string): Hit | null {
    const lower = text.toLowerCase();
    const exit = doc.exits.find((item) => item.label.length > 2 && lower.includes(item.label.toLowerCase()));
    if (exit) return { level: exit.level, x: exit.position[0], z: exit.position[1] };
    const room = doc.rooms.find((item) => item.name.length > 2 && lower.includes(item.name.toLowerCase()));
    if (room) return { level: room.level, x: room.centroid[0], z: room.centroid[1] };
    if (lastHit) return lastHit;
    const first = doc.rooms.find((item) => item.level === context.level()) ?? doc.rooms[0];
    return first ? { level: first.level, x: first.centroid[0], z: first.centroid[1] } : null;
  }

  function describeAt(at: At) {
    if (at.room) return `in ${doc.rooms.find((room) => room.id === at.room)?.name ?? at.room}`;
    if (at.exit) return `at ${doc.exits.find((exit) => exit.id === at.exit)?.label ?? at.exit}`;
    if (at.door) return `at door ${at.door}`;
    if (at.stair) return `at stair ${at.stair}`;
    return `on ${levelName(at.level ?? "")}`;
  }
  const describeEvent = (event: ScenarioEvent) => `${TYPE_LABEL[event.type] ?? event.type} ${describeAt(event.at)}`;

  // ---- traits and people ----
  function traits() {
    const value = (key: string) => Number(section.querySelector<HTMLInputElement>(`#edit-${key}`)!.value);
    return {
      ...Object.fromEntries(TRAITS.map(([key]) => [key, value(key)])),
      // cognition/personas.py reads Big Five in [-1, 1]; the sliders show 0-1.
      ocean: Object.fromEntries(OCEAN.map(([key]) => [key, round(value(`ocean-${key}`) * 2 - 1)])),
    };
  }
  function placePeople(points: Hit[]) {
    const who = persona.value;
    const shared = { count: 1, personas: { [who]: 1.0 }, traits: traits() };
    const occupants: Occupant[] = points.map((p) => ({ where: { point: [round(p.x), round(p.z)], level: p.level }, ...shared }));
    const name = PERSONAS.find((p) => p[0] === who)?.[1].toLowerCase() ?? who;
    const label = `${points.length === 1 ? "1 person" : `${points.length} people`} (${name}) on ${levelName(points[0].level)}`;
    commit({ label, occupants, people: points.map((p) => ({ ...p, persona: who })) });
  }

  // ---- markers ----
  const markerMaterial = (token: string, opacity = 1) =>
    new THREE.MeshBasicMaterial({ color: context.token(token), transparent: true, opacity, depthTest: false, side: THREE.DoubleSide });
  const discGeometry = new THREE.CircleGeometry(1, 24).rotateX(-Math.PI / 2);
  const ringGeometry = new THREE.RingGeometry(0.75, 1, 24).rotateX(-Math.PI / 2);
  const barGeometry = new THREE.PlaneGeometry(1.7, 0.22).rotateX(-Math.PI / 2);
  const materials = {
    hazard: markerMaterial("hazard"), hazardBright: markerMaterial("hazard-bright"), smoke: markerMaterial("text-muted", 0.85),
    exit: markerMaterial("exit-bright"), warn: markerMaterial("warn"), drafting: markerMaterial("drafting"),
    ground: markerMaterial("ground", 0.9),
  };
  const markers = new Set<THREE.Object3D>();
  /** Flat marker at the click (so events in one room do not stack on its label) or at the snapped target. */
  function eventMarker(event: ScenarioEvent, hit?: Hit) {
    const snapped = event.at.exit || event.at.door || event.at.stair;
    const where = (!snapped && hit) || atPosition(doc, event.at);
    if (!where) return undefined;
    const group = new THREE.Group();
    const add = (geometry: THREE.BufferGeometry, material: THREE.Material, y = 0, turn = 0) => {
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.y = y;
      mesh.rotation.y = turn;
      mesh.renderOrder = 20;
      group.add(mesh);
    };
    if (event.type === "fire") { add(discGeometry, materials.hazard); add(ringGeometry, materials.hazardBright, 0.001); }
    else if (event.type === "smoke") { add(discGeometry, materials.smoke); add(ringGeometry, materials.ground, 0.001); }
    else if (event.type === "blocked_exit") {
      // Hatching, not color alone: a crossed ring reads as blocked in grayscale too.
      add(discGeometry, materials.ground); add(ringGeometry, materials.hazardBright, 0.001);
      add(barGeometry, materials.hazardBright, 0.002, Math.PI / 4); add(barGeometry, materials.hazardBright, 0.002, -Math.PI / 4);
    } else if (event.type === "open_exit") { add(discGeometry, materials.ground); add(ringGeometry, materials.exit, 0.001); }
    else if (event.type === "pa_announcement") { add(ringGeometry, materials.drafting); add(discGeometry.clone().scale(0.4, 1, 0.4), materials.drafting); }
    else { add(ringGeometry, materials.warn); add(discGeometry.clone().scale(0.4, 1, 0.4), materials.warn); }
    const elevation = doc.levels.find((level) => level.id === where.level)?.elevation_m ?? 0;
    group.position.set(where.x, elevation + 0.12, where.z);
    group.userData.title = describeEvent(event);
    context.levelRoot(where.level).add(group);
    markers.add(group);
    return group;
  }
  // Keep markers about MARKER_PX on screen: measure pixels per meter at each marker with the camera
  // that last rendered (context.pxPerMeter is taken at the camera target, which is far off in plan grids).
  const a = new THREE.Vector3(), b = new THREE.Vector3();
  context.onFrame(() => {
    if (!markers.size || !canvas) return;
    const halfWidth = canvas.clientWidth / (stage?.classList.contains("split") ? 2 : 1) / 2;
    for (const marker of markers) {
      marker.getWorldPosition(a);
      b.copy(a).x += 1;
      const pxPerMeter = a.project(activeCamera).distanceTo(b.project(activeCamera)) * halfWidth;
      marker.scale.setScalar(Math.min(20, Math.max(0.2, MARKER_PX / Math.max(pxPerMeter, 1e-3))));
    }
  });

  // One Points cloud per level, rebuilt only when people change.
  const dotTexture = (() => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 32;
    const g = canvas.getContext("2d")!;
    // Vertex colors multiply the texture, so the dark rim becomes an outline that reads on any floor.
    g.fillStyle = "#0b1622";
    g.beginPath(); g.arc(16, 16, 15, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#fff";
    g.beginPath(); g.arc(16, 16, 11, 0, Math.PI * 2); g.fill();
    return new THREE.CanvasTexture(canvas);
  })();
  const dotMaterial = new THREE.PointsMaterial({
    size: DOT_PX * window.devicePixelRatio, sizeAttenuation: false, vertexColors: true, map: dotTexture,
    alphaTest: 0.5, transparent: true, depthTest: false,
  });
  const clouds = new Map<string, THREE.Points>();
  function drawPeople() {
    for (const cloud of clouds.values()) { cloud.removeFromParent(); cloud.geometry.dispose(); }
    clouds.clear();
    const byLevel = new Map<string, Person[]>();
    for (const item of items) for (const p of item.people) byLevel.set(p.level, [...(byLevel.get(p.level) ?? []), p]);
    for (const [level, people] of byLevel) {
      const elevation = doc.levels.find((item) => item.id === level)?.elevation_m ?? 0;
      const positions = new Float32Array(people.length * 3);
      const colors = new Float32Array(people.length * 3);
      people.forEach((p, i) => {
        positions.set([p.x, elevation + 0.15, p.z], i * 3);
        personaColor(p.persona).toArray(colors, i * 3);
      });
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      const cloud = new THREE.Points(geometry, dotMaterial);
      cloud.renderOrder = 21;
      context.levelRoot(level).add(cloud);
      clouds.set(level, cloud);
    }
  }
  /** Dots for occupants given by room (server results): random points in the room polygon. */
  function peopleFor(occupant: Occupant): Person[] {
    const who = Object.entries(occupant.personas ?? {}).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "regular";
    if ("point" in occupant.where) return [{ level: occupant.where.level, x: occupant.where.point[0], z: occupant.where.point[1], persona: who }];
    const room = doc.rooms.find((item) => item.id === (occupant.where as { room: string }).room);
    if (!room) return [];
    const xs = room.polygon.map((p) => p[0]), zs = room.polygon.map((p) => p[1]);
    const inRoom = samplePoints({ ...doc, rooms: [room] }, room.level, Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs),
      Math.min(occupant.count, MAX_DOTS_PER_GROUP));
    return inRoom.map((p) => ({ ...p, persona: who }));
  }

  // ---- draft vs live ----
  function commit(change: { label: string; event?: ScenarioEvent; occupants?: Occupant[]; people?: Person[]; hit?: Hit }) {
    const payload = { type: "edit", t_s: null, ...(change.event && { event: change.event }), ...(change.occupants && { occupants: change.occupants }) };
    const sent = !window.dispatchEvent(new CustomEvent("nova:edit", { detail: payload, cancelable: true }));
    if (!sent) {
      draft = {
        events: change.event ? [...draft.events, change.event] : draft.events,
        occupants: change.occupants ? [...draft.occupants, ...change.occupants] : draft.occupants,
      };
    }
    const people = change.people ?? (change.occupants ?? []).flatMap(peopleFor);
    items.push({ label: change.label, sent, event: change.event, occupants: change.occupants, people, marker: change.event && eventMarker(change.event, change.hit) });
    if (people.length) drawPeople();
    renderList();
    return sent;
  }
  function remove(item: Item) {
    items.splice(items.indexOf(item), 1);
    draft = {
      events: draft.events.filter((event) => event !== item.event),
      occupants: draft.occupants.filter((occupant) => !item.occupants?.includes(occupant)),
    };
    if (item.marker) { item.marker.removeFromParent(); markers.delete(item.marker); }
    if (item.people.length) drawPeople();
    renderList();
  }
  function renderList() {
    $<HTMLElement>("#edit-empty").hidden = items.length > 0;
    list.replaceChildren(...items.map((item) => {
      const li = document.createElement("li");
      li.className = item.sent ? "sent" : "draft";
      const text = document.createElement("div");
      const name = document.createElement("span");
      name.textContent = item.label;
      const status = document.createElement("small");
      status.textContent = item.sent ? "Sent to the live run" : "Added to scenario";
      text.append(name, status);
      li.append(text);
      if (!item.sent) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Remove";
        button.setAttribute("aria-label", `Remove ${item.label}`);
        button.addEventListener("click", () => remove(item));
        li.append(button);
      }
      return li;
    }));
  }
  $<HTMLButtonElement>("#edit-download").addEventListener("click", () => {
    const scenario = {
      schema_version: "0.1", scene: SCENE, seed: 42, duration_s: 600,
      events: draft.events, occupants: draft.occupants, assumptions: [],
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(scenario, null, 2)], { type: "application/json" }));
    const link = Object.assign(document.createElement("a"), { href: url, download: "scenario.json" });
    link.click();
    URL.revokeObjectURL(url);
  });

  // ---- scenario server (scenario/serve.py via the /scenario proxy) ----
  async function post(path: string, body: object) {
    const response = await fetch(`/scenario/${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
    // The vite proxy answers {offline: true} when nothing listens on 8792 (see vite.config.ts).
    if (!response.ok || data.offline || data.error) throw new Error(data.error ?? `HTTP ${response.status}`);
    return data;
  }
  /** Commit server events and occupants; returns a one-line summary. */
  function commitServer(data: { events?: ScenarioEvent[]; occupants?: Occupant[] }) {
    const labels: string[] = [];
    for (const event of data.events ?? []) {
      const own = { ...event, id: `ev_edit_${++eventCount}` };
      commit({ label: describeEvent(own), event: own });
      labels.push(describeEvent(own));
    }
    for (const occupant of data.occupants ?? []) {
      const where = "room" in occupant.where ? describeAt({ room: occupant.where.room }) : describeAt({ level: occupant.where.level });
      const label = `${occupant.count} people ${where}`;
      commit({ label, occupants: [occupant] });
      labels.push(label);
    }
    return labels.join("; ");
  }

  async function clickAndType(hit: Hit) {
    const text = describe.value.trim();
    if (!text) { hint.textContent = "Type what happens first, then click the spot."; describe.focus(); return; }
    hint.textContent = "Reading that...";
    try {
      const data = await post("parse", { text, at: { level: hit.level, point: [round(hit.x), round(hit.z)] }, scene: SCENE, scene_path: SCENE_PATH, t_s: 0 });
      const summary = commitServer(data);
      hint.textContent = summary || data.questions?.[0]?.question || "The parser found no event in that.";
    } catch (error) {
      console.warn("scenario parse unavailable, using the offline parser:", error);
      const parsed = localParse(text, hit);
      if (!parsed) hint.textContent = "Could not tell what happens. Try fire, smoke, rumor, blocked, PA or open (offline parser).";
      else if ("error" in parsed) hint.textContent = `${parsed.error} (offline parser)`;
      else {
        commit({ label: describeEvent(parsed.event), event: parsed.event, hit });
        hint.textContent = `${describeEvent(parsed.event)}${parsed.note} (offline parser)`;
      }
    }
  }

  function chatLine(role: "user" | "assistant", content: string) {
    const li = document.createElement("li");
    li.className = role;
    li.textContent = content;
    chatLog.append(li);
    chatLog.scrollTop = chatLog.scrollHeight;
  }
  const chatForm = $<HTMLFormElement>("#edit-chat-form");
  const chatInput = $<HTMLInputElement>("#edit-chat-input");
  chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = "";
    chat = [...chat, { role: "user", content: text }];
    chatLine("user", text);
    chatForm.querySelector("button")!.disabled = true;
    try {
      const data = await post("chat", { messages: chat, scene: SCENE, scene_path: SCENE_PATH });
      const summary = commitServer(data);
      chat = Array.isArray(data.messages) ? data.messages : chat;
      const reply = data.questions?.[0]?.question ?? chat.at(-1)?.content ?? summary;
      if (chat.at(-1)?.role !== "assistant") chat = [...chat, { role: "assistant", content: reply }];
      chatLine("assistant", summary && reply !== summary ? `${reply} Added: ${summary}.` : reply);
    } catch (error) {
      console.warn("scenario chat unavailable, replying offline:", error);
      const hit = locate(text);
      const parsed = hit && localParse(text, hit);
      let reply: string;
      if (!parsed) reply = "Offline: I could not tell what happens. Mention fire, smoke, a rumor, a blocked or open exit, or a PA.";
      else if ("error" in parsed) reply = `Offline: ${parsed.error}`;
      else {
        commit({ label: describeEvent(parsed.event), event: parsed.event, hit: hit ?? undefined });
        reply = `Offline, understood as "${describeEvent(parsed.event)}". Added it.`;
      }
      chat = [...chat, { role: "assistant", content: reply }];
      chatLine("assistant", reply);
    } finally {
      chatForm.querySelector("button")!.disabled = false;
    }
  });

  // ---- stage input ----
  context.onPick((raycaster) => {
    if (!tool) return false;
    const hit = context.pickFloor(raycaster);
    if (!hit) { hint.textContent = "That is not on a floor; click inside the plan."; return true; }
    lastHit = hit;
    if (tool === "type") void clickAndType(hit);
    else if (tool === "people") { placePeople([hit]); hint.textContent = "Placed one person. Drag a box to spread more."; }
    else {
      const built = buildEvent(EVENT_TYPE[tool]!, hit, message.value);
      if ("error" in built) hint.textContent = built.error;
      else {
        const sent = commit({ label: describeEvent(built.event), event: built.event, hit });
        hint.textContent = `${describeEvent(built.event)}${built.note}. ${sent ? "Sent to the live run." : "Added to scenario."}`;
      }
    }
    return true;
  });

  // Drag box for the people tool.
  const previousHook = context.scene.onBeforeRender;
  context.scene.onBeforeRender = function (...args: Parameters<THREE.Object3D["onBeforeRender"]>) {
    activeCamera = args[2];
    previousHook.apply(this, args);
  };
  const box = document.createElement("div");
  box.className = "edit-box";
  box.hidden = true;
  stage?.append(box);
  let dragStart: { x: number; y: number } | null = null;

  /** Floor point (level coordinates) under a screen point, on the plane of `level`. */
  function floorAt(clientX: number, clientY: number, level?: string): Hit | null {
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const halves = stage?.classList.contains("split") ? 2 : 1;
    const width = rect.width / halves;
    const side = Math.min(halves - 1, Math.floor((dragStart!.x - rect.left) / width));
    const pointer = new THREE.Vector2(((clientX - rect.left - side * width) / width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(pointer, activeCamera);
    const id = level ?? context.pickFloor(raycaster)?.level;
    if (!id) return null;
    const origin = context.place(id, 0, 0); // level origin in world space: (offset.x, elevation + offset.y, offset.z)
    const world = raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), -origin.y), new THREE.Vector3());
    return world && { level: id, x: world.x - origin.x, z: world.z - origin.z };
  }
  canvas?.addEventListener("pointerdown", (event) => {
    if (tool !== "people" || event.button !== 0) return;
    dragStart = { x: event.clientX, y: event.clientY };
  });
  window.addEventListener("pointermove", (event) => {
    if (!dragStart || !stage) return;
    const rect = stage.getBoundingClientRect();
    const moved = Math.hypot(event.clientX - dragStart.x, event.clientY - dragStart.y) > DRAG_PX;
    box.hidden = !moved;
    Object.assign(box.style, {
      left: `${Math.min(dragStart.x, event.clientX) - rect.left}px`, top: `${Math.min(dragStart.y, event.clientY) - rect.top}px`,
      width: `${Math.abs(event.clientX - dragStart.x)}px`, height: `${Math.abs(event.clientY - dragStart.y)}px`,
    });
  });
  window.addEventListener("pointerup", (event) => {
    if (!dragStart) return;
    const start = dragStart;
    const dragged = !box.hidden;
    box.hidden = true;
    if (!dragged) { dragStart = null; return; } // a click: the onPick handler places one person
    const centerLevel = floorAt((start.x + event.clientX) / 2, (start.y + event.clientY) / 2)?.level
      ?? floorAt(start.x, start.y)?.level ?? floorAt(event.clientX, event.clientY)?.level;
    const a = centerLevel ? floorAt(start.x, start.y, centerLevel) : null;
    const b = centerLevel ? floorAt(event.clientX, event.clientY, centerLevel) : null;
    dragStart = null;
    if (!a || !b) { hint.textContent = "Drag the box over a floor."; return; }
    const n = Math.max(1, Math.min(200, Math.round(Number(count.value) || 1)));
    const points = samplePoints(doc, a.level, Math.min(a.x, b.x), Math.max(a.x, b.x), Math.min(a.z, b.z), Math.max(a.z, b.z), n);
    if (!points.length) { hint.textContent = "No room floor inside that box."; return; }
    placePeople(points);
    hint.textContent = points.length < n ? `Placed ${points.length} of ${n}; the box has little room floor.` : `Placed ${n} people.`;
  });
}

function slider(key: string, name: string, min: number, max: number, step: number, value: number) {
  return `<label class="edit-slider"><span>${name}</span><input id="edit-${key}" type="range" min="${min}" max="${max}" step="${step}" value="${value}" /><output>${value}</output></label>`;
}
