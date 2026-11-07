// Analysis overlay (?analysis=/path/analysis.json): single points of failure in red,
// pinch points in amber, smoke arrival time per room. Field names are read loosely
// until the analysis agent's format settles: items point at a door, an exit or a point.
import * as THREE from "three";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import type { SceneDoc, ViewerContext } from "./context";

type Item = Record<string, unknown>;

const list = (value: unknown): Item[] => (Array.isArray(value) ? value : []);
const str = (value: unknown) => (typeof value === "string" ? value : undefined);
const num = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : undefined);

/** Door segment or point for an item that names a door, an exit (via its door) or a point. */
function locateItem(doc: SceneDoc, item: Item): { segment?: number[][]; point: number[] } | null {
  const doorId = str(item.door) ?? str(item.door_id) ?? str(item.ref) ?? str(item.id);
  const exitId = str(item.exit) ?? str(item.exit_id);
  const exit = doc.exits.find((entry) => entry.id === (exitId ?? doorId));
  const door = doc.doors.find((entry) => entry.id === doorId) ?? doc.doors.find((entry) => entry.id === exit?.door_id);
  if (door) {
    const [a, b] = door.segment;
    return { segment: door.segment, point: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] };
  }
  if (exit) return { point: exit.position };
  const point = (item.point ?? item.position ?? item.at) as number[] | undefined;
  return Array.isArray(point) && point.length >= 2 ? { point } : null;
}

function smokeTimes(data: Item): { room: string; t: number }[] {
  const raw = data.smoke_times ?? data.smoke ?? data.smoke_time_s;
  if (Array.isArray(raw)) {
    return raw
      .map((item: Item) => ({ room: str(item.room) ?? str(item.room_id) ?? "", t: num(item.t_s) ?? num(item.t) ?? num(item.smoke_s) ?? NaN }))
      .filter((item) => item.room && Number.isFinite(item.t));
  }
  if (raw && typeof raw === "object") {
    return Object.entries(raw as Record<string, unknown>)
      .map(([room, value]) => ({ room, t: num(value) ?? num((value as Item)?.t_s) ?? NaN }))
      .filter((item) => Number.isFinite(item.t));
  }
  return [];
}

export async function startAnalysis(context: ViewerContext, url: string | null) {
  if (!url) return;
  let data: Item;
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    document.querySelector("#warnings")!.prepend(
      Object.assign(document.createElement("p"), { className: "analysis-error", textContent: `Could not load ${url} (${error instanceof Error ? error.message : error}).` }),
    );
    return;
  }
  const { doc } = context;
  const group = new THREE.Group();
  const y = context.elevation() + 0.12;
  const stage = document.querySelector<HTMLElement>("#stage")!;
  const materials: LineMaterial[] = [];

  function mark(item: Item, tokenName: string, className: string, title: string) {
    const where = locateItem(doc, item);
    if (!where) return;
    if (where.segment) {
      const material = new LineMaterial({ color: context.token(tokenName).getHex(), linewidth: 7, depthTest: false });
      material.resolution.set(stage.clientWidth, stage.clientHeight);
      materials.push(material);
      const line = new Line2(new LineGeometry().setPositions(where.segment.flatMap(([x, z]) => [x, y, z])), material);
      line.renderOrder = 9;
      group.add(line);
    } else {
      const disc = new THREE.Mesh(
        new THREE.CircleGeometry(0.45, 32).rotateX(-Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: context.token(tokenName), transparent: true, opacity: 0.55, depthTest: false }),
      );
      disc.position.set(where.point[0], y, where.point[1]);
      group.add(disc);
    }
    const detail = str(item.label) ?? str(item.reason) ?? str(item.why) ?? str(item.text);
    const width = num(item.width_m);
    const tag = context.label(`analysis-label ${className}`, (element) => {
      const heading = document.createElement("b");
      heading.textContent = title;
      element.append(heading);
      const note = [detail, width ? `${width.toFixed(2)} m wide` : ""].filter(Boolean).join(", ");
      if (note) element.append(Object.assign(document.createElement("span"), { textContent: note }));
    });
    tag.position.set(where.point[0], y, where.point[1]);
    tag.center.set(0.5, -0.35);
    group.add(tag);
  }

  for (const item of list(data.spof ?? data.single_points_of_failure ?? data.spofs)) mark(item, "hazard-bright", "spof", "Single point of failure");
  for (const item of list(data.pinch_points ?? data.pinches ?? data.bottlenecks)) mark(item, "warn", "pinch", "Pinch point");
  for (const { room, t } of smokeTimes(data)) {
    const target = doc.rooms.find((entry) => entry.id === room);
    if (!target) continue;
    const tag = context.label("smoke-badge", (element) => {
      element.append("Smoke ", Object.assign(document.createElement("b"), { textContent: `${Math.round(t)} s` }));
    });
    tag.position.set(target.centroid[0], y, target.centroid[1]);
    tag.center.set(0.5, -0.9); // below the room name
    group.add(tag);
  }
  window.addEventListener("resize", () => materials.forEach((material) => material.resolution.set(stage.clientWidth, stage.clientHeight)));

  context.addLayer("analysis", group);
  const toggle = document.querySelector<HTMLInputElement>("#show-analysis")!;
  toggle.closest("label")!.hidden = false;
  toggle.addEventListener("change", () => (group.visible = toggle.checked));
}
