// Analysis overlay (?analysis=/path/analysis.json from `python -m analysis scene.json`):
// single points of failure in red, pinch points in amber, smoke time at head height per room.
import * as THREE from "three";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import type { ViewerContext } from "./context";

type SpofRow = {
  kind: "room" | "door";
  id: string;
  stranded_rooms: string[];
  stranded_occupants: number;
  leaf: boolean;
  exit_width_lost_share: number;
};
type PinchRow = { room: string; pinch_width_m: number; pinch_at: number[]; pinch_from: string; flow_p_per_min: number; below_0_9_m: boolean };
type Analysis = {
  spof?: { ranked?: SpofRow[] };
  pinch?: { rooms?: PinchRow[]; narrowest?: string | null; flagged?: string[] };
  smoke?: { rooms?: Record<string, { t_head_height_s?: number }> };
};

// A door is worth showing when losing it strands a room that is not a dead end, or costs a big share of exit width.
const SPOF_WIDTH_SHARE = 0.4;

export async function startAnalysis(context: ViewerContext, url: string | null) {
  if (!url) return;
  let data: Analysis;
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    const note = document.createElement("p");
    note.className = "analysis-error";
    note.textContent = `Could not load ${url} (${error instanceof Error ? error.message : error}).`;
    document.querySelector("#warnings")!.prepend(note);
    return;
  }
  const { doc } = context;
  const level = context.level();
  const group = new THREE.Group();
  const y = (doc.levels.find((item) => item.id === level)?.elevation_m ?? 0) + 0.12;
  const stage = document.querySelector<HTMLElement>("#stage")!;
  const materials: LineMaterial[] = [];

  function outline(points: number[][], tokenName: string, closed: boolean) {
    // Doors get a heavy stroke; a whole room (often a ragged scan outline) gets a light one.
    const material = new LineMaterial({ color: context.token(tokenName).getHex(), linewidth: closed ? 2 : 7, depthTest: false });
    material.resolution.set(stage.clientWidth, stage.clientHeight);
    materials.push(material);
    const path = closed ? [...points, points[0]] : points;
    const line = new Line2(new LineGeometry().setPositions(path.flatMap(([x, z]) => [x, y, z])), material);
    line.renderOrder = 9;
    group.add(line);
  }

  function tag(point: number[], className: string, title: string, note: string, center: [number, number] = [0.5, -0.35]) {
    const label = context.label(`analysis-label ${className}`, (element) => {
      const heading = document.createElement("b");
      heading.textContent = title;
      const detail = document.createElement("span");
      detail.textContent = note;
      element.append(heading, detail);
    });
    label.position.set(point[0], y, point[1]);
    label.center.set(...center);
    group.add(label);
  }

  for (const row of data.spof?.ranked ?? []) {
    if (row.leaf || (!row.stranded_rooms.length && row.exit_width_lost_share < SPOF_WIDTH_SHARE)) continue;
    const note = row.stranded_rooms.length
      ? `Losing it strands ${row.stranded_rooms.length} rooms (${row.stranded_occupants} people)`
      : `Losing it removes ${Math.round(row.exit_width_lost_share * 100)}% of exit width`;
    if (row.kind === "door") {
      const door = doc.doors.find((item) => item.id === row.id && item.level === level);
      if (!door) continue;
      outline(door.segment, "hazard-bright", false);
      const [a, b] = door.segment;
      tag([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], "spof", "Single point of failure", note);
    } else {
      const room = doc.rooms.find((item) => item.id === row.id && item.level === level);
      if (!room) continue;
      outline(room.polygon, "hazard-bright", true);
      tag(room.centroid, "spof", `Single point of failure: ${room.name}`, note, [0.5, 1.9]); // above the room name
    }
  }

  // The narrowest route plus every route under 0.9 m.
  const pinchRooms = new Set([...(data.pinch?.flagged ?? []), ...(data.pinch?.narrowest ? [data.pinch.narrowest] : [])]);
  for (const row of data.pinch?.rooms ?? []) {
    if (!pinchRooms.has(row.room) || !row.pinch_at) continue;
    const disc = new THREE.Mesh(
      new THREE.CircleGeometry(0.4, 32).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: context.token("warn"), transparent: true, opacity: 0.6, depthTest: false }),
    );
    disc.position.set(row.pinch_at[0], y, row.pinch_at[1]);
    disc.renderOrder = 9;
    group.add(disc);
    tag(row.pinch_at, "pinch", "Pinch point", `${row.pinch_width_m.toFixed(2)} m clear, ${row.flow_p_per_min} people/min`);
  }

  for (const [roomId, smoke] of Object.entries(data.smoke?.rooms ?? {})) {
    const room = doc.rooms.find((item) => item.id === roomId && item.level === level);
    if (!room || smoke.t_head_height_s === undefined) continue;
    const badge = context.label("smoke-badge", (element) => {
      const value = document.createElement("b");
      value.textContent = `${Math.round(smoke.t_head_height_s!)} s`;
      element.title = "Smoke layer reaches head height (1.8 m), Tanaka-Yamana estimate";
      element.append("Smoke ", value);
    });
    badge.position.set(room.centroid[0], y, room.centroid[1]);
    badge.center.set(0.5, -0.9); // below the room name
    group.add(badge);
  }
  window.addEventListener("resize", () => materials.forEach((material) => material.resolution.set(stage.clientWidth, stage.clientHeight)));

  context.levelRoot(level).add(group); // follows the floor when the view moves it
  context.addLayer("analysis", group);
  const toggle = document.querySelector<HTMLInputElement>("#show-analysis")!;
  toggle.closest("label")!.hidden = false;
  toggle.addEventListener("change", () => (group.visible = toggle.checked));
}
