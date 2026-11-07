// "Suggested changes": the post-run analysis written next to the replay (recommendations.json).
// Every reference is a scene id (room, door, exit, stair); the plan highlights them, so nothing here
// depends on a particular building.
import * as THREE from "three";
import type { SceneDoc, ViewerContext } from "./context";

type Finding = { title: string; detail: string; refs?: string[] };
type Recommendation = {
  title: string;
  kind: string;
  refs?: string[];
  why: string;
  expected_effect?: string;
  effort?: "low" | "medium" | "high";
};
type Analysis = { model?: string; summary?: string; findings?: Finding[]; recommendations?: Recommendation[] };
type Spot = { level: string; x: number; z: number; shape: "area" | "point"; polygon?: number[][] };

const KIND_LABEL: Record<string, string> = {
  add_exit: "New exit", widen_door: "Wider door", refuge: "Refuge", assisted_plan: "Assisted evacuation",
  signage: "Signage", staffing: "Staffing", procedure: "Procedure", layout: "Layout", other: "Other",
};
const EFFORT_LABEL: Record<string, string> = { low: "Low effort", medium: "Medium effort", high: "High effort" };

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

const center = (points: number[][]) => points.reduce((acc, p) => [acc[0] + p[0] / points.length, acc[1] + p[1] / points.length], [0, 0]);

/** Scene id -> where it is. Unknown ids are skipped (the backend validates, but the viewer never trusts it). */
function locate(doc: SceneDoc, id: string): Spot | null {
  const room = doc.rooms.find((item) => item.id === id);
  if (room) return { level: room.level, x: room.centroid[0], z: room.centroid[1], shape: "area", polygon: room.polygon };
  const stair = doc.stairs?.find((item) => item.id === id);
  if (stair) {
    const [x, z] = center(stair.polygon);
    return { level: stair.from_level, x, z, shape: "area", polygon: stair.polygon };
  }
  const exit = doc.exits.find((item) => item.id === id);
  if (exit) return { level: exit.level, x: exit.position[0], z: exit.position[1], shape: "point" };
  const door = doc.doors.find((item) => item.id === id);
  if (door) {
    const [x, z] = center(door.segment);
    return { level: door.level, x, z, shape: "point" };
  }
  return null;
}

export interface Suggestions {
  /** Show the section (called when the replay reaches its end). */
  reveal: () => void;
}

export function startSuggestions(context: ViewerContext, replayUrl: string): Suggestions {
  const section = el("section", "suggest");
  section.hidden = true;
  section.setAttribute("aria-labelledby", "suggest-title");
  const title = el("h2", "", "Suggested changes");
  title.id = "suggest-title";
  section.append(title);
  document.querySelector("#inside")?.after(section);

  const placed: THREE.Object3D[] = []; // highlight groups, each under its own floor
  const pulses: THREE.Mesh[] = [];
  let data: Analysis | null = null;
  let shown = false;

  const elevation = (level: string) => context.doc.levels.find((item) => item.id === level)?.elevation_m ?? 0;
  function clear() {
    for (const object of placed.splice(0)) object.removeFromParent();
    pulses.length = 0;
  }
  // Refs light up on their floors: rooms and stairs as a tinted area, doors and exits as a pulsing ring.
  // Amber means "look here"; a proposed new exit uses the way-out green.
  function mark(refs: string[] | undefined, kind: string, move: boolean) {
    clear();
    const color = context.token(kind === "add_exit" ? "route" : "warn");
    const spots = (refs ?? []).map((id) => locate(context.doc, id)).filter((spot): spot is Spot => spot !== null);
    for (const spot of spots) {
      const group = new THREE.Group();
      const y = elevation(spot.level) + 0.11;
      if (spot.shape === "area" && spot.polygon) {
        const shape = new THREE.Shape(spot.polygon.map(([x, z]) => new THREE.Vector2(x, -z)));
        const fill = new THREE.Mesh(
          new THREE.ShapeGeometry(shape).rotateX(-Math.PI / 2).translate(0, y, 0),
          new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.35, depthWrite: false, depthTest: false, side: THREE.DoubleSide }),
        );
        const outline = new THREE.LineLoop(
          new THREE.BufferGeometry().setFromPoints(spot.polygon.map(([x, z]) => new THREE.Vector3(x, y, z))),
          new THREE.LineBasicMaterial({ color, depthTest: false }),
        );
        fill.renderOrder = outline.renderOrder = 15;
        group.add(fill, outline);
      } else {
        const ring = new THREE.Mesh(
          new THREE.RingGeometry(0.9, 1.3, 40).rotateX(-Math.PI / 2),
          new THREE.MeshBasicMaterial({ color, transparent: true, depthTest: false, side: THREE.DoubleSide }),
        );
        ring.position.set(spot.x, y, spot.z);
        ring.renderOrder = 15;
        pulses.push(ring);
        group.add(ring);
      }
      context.levelRoot(spot.level).add(group);
      placed.push(group);
    }
    if (move && spots[0]) context.focusOn(spots[0].level, spots[0].x, spots[0].z);
  }
  context.onFrame((now) => {
    const phase = (now % 1600) / 1600;
    for (const ring of pulses) {
      ring.scale.setScalar(1 + phase * 0.8);
      (ring.material as THREE.MeshBasicMaterial).opacity = 0.9 * (1 - phase);
      // Rings stay readable when zoomed out: at least ~16 px across.
      const minimum = Math.max(1, 16 / (2.6 * context.pxPerMeter()));
      ring.scale.multiplyScalar(minimum);
    }
  });

  function row(item: Recommendation) {
    const li = el("li", "suggest-row");
    const head = el("button", "suggest-head");
    head.type = "button";
    head.setAttribute("aria-expanded", "false");
    const name = el("span", "suggest-name", item.title);
    const tags = el("span", "suggest-tags");
    tags.append(el("span", "suggest-tag", KIND_LABEL[item.kind] ?? KIND_LABEL.other));
    if (item.effort) tags.append(el("span", "suggest-effort", EFFORT_LABEL[item.effort] ?? item.effort));
    head.append(name, tags);
    const body = el("div", "suggest-body");
    body.hidden = true;
    body.append(el("p", "", item.why));
    if (item.expected_effect) body.append(el("p", "suggest-effect", `Expected effect: ${item.expected_effect}`));
    head.addEventListener("mouseenter", () => mark(item.refs, item.kind, false));
    head.addEventListener("focus", () => mark(item.refs, item.kind, false));
    head.addEventListener("click", () => {
      const open = body.hidden;
      for (const other of section.querySelectorAll<HTMLElement>(".suggest-body")) other.hidden = true;
      for (const other of section.querySelectorAll(".suggest-head")) other.setAttribute("aria-expanded", "false");
      body.hidden = !open;
      head.setAttribute("aria-expanded", String(open));
      mark(item.refs, item.kind, true);
    });
    li.append(head, body);
    return li;
  }

  function render() {
    if (!data) return;
    section.replaceChildren(title);
    if (data.summary) section.append(el("p", "suggest-summary", data.summary));
    const list = el("ol", "suggest-list");
    for (const item of data.recommendations ?? []) list.append(row(item));
    section.append(list);
    if (data.findings?.length) {
      const fold = el("details", "suggest-findings");
      fold.append(el("summary", "", "What the analysis noticed"));
      const items = el("ul");
      for (const finding of data.findings) {
        const li = el("li");
        const button = el("button", "", finding.title);
        button.type = "button";
        button.addEventListener("click", () => mark(finding.refs, "other", true));
        li.append(button, el("p", "", finding.detail));
        items.append(li);
      }
      fold.append(items);
      section.append(fold);
    }
    if (data.model) section.append(el("p", "suggest-model", `Written by ${data.model} from this run's log.`));
  }

  const base = replayUrl.slice(0, replayUrl.lastIndexOf("/") + 1);
  const ready = fetch(`${base}recommendations.json`)
    .then(async (response) => {
      if (!response.ok) return null;
      const text = await response.text();
      if (text.trimStart().startsWith("<")) return null; // dev server fallback page
      return JSON.parse(text) as Analysis;
    })
    .catch(() => null)
    .then((loaded) => {
      data = loaded;
      if (!data) return;
      // Before the end of the run the section is one button, so the result is not given away early.
      const early = el("button", "suggest-early", "Show suggestions");
      early.type = "button";
      early.addEventListener("click", reveal);
      section.append(early);
      section.hidden = false;
    });

  function reveal() {
    void ready.then(() => {
      if (!data || shown) return;
      shown = true;
      render();
      section.hidden = false;
    });
  }
  return { reveal };
}
