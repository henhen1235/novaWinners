// Per-level building model and the four floor views (single, side by side, stacked, exploded).
// Every level lives in its own group in natural scene coordinates (y = elevation); a view only
// moves those groups, so rooms, doors, walls, analysis overlays and labels follow for free.
// Conventions follow Pathfinder and MassMotion: no ceilings, walls cut at 1.2 m by default,
// non-focused floors as transparent ghosts in 3D, one flat 2D plan per floor for watching a run.
import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { SceneDoc } from "./context";

export type ViewMode = "single" | "side" | "stacked" | "exploded";
export type WallMode = "off" | "cutaway" | "full";
export type LayerKey = "navmesh" | "rooms" | "doors" | "exits";

export const CUTAWAY_HEIGHT_M = 1.2;
// A room label needs about this much screen space (px) before it is drawn; smaller rooms stay unlabeled.
const LABEL_MIN_W_PX = 72;
const LABEL_MIN_D_PX = 30;
// Colour carries meaning (green way out, red fire, amber needs a human), so stairs stay neutral:
// the shaft letter in an outlined badge says which stair is which.
const STAIR_COLORS = [getComputedStyle(document.documentElement).getPropertyValue("--color-drafting").trim() || "#8DB4DC"];

const css = getComputedStyle(document.documentElement);
const cssColor = (name: string, fallback = "#888888") => new THREE.Color(css.getPropertyValue(name).trim() || fallback);
const token = (name: string) => cssColor(`--color-${name}`);

type Room = SceneDoc["rooms"][number];
type RoomLabel = { room: Room; w: number; d: number; tag: CSS2DObject | null };

export interface FloorLevel {
  id: string;
  name: string;
  short: string; // "3" for the reference bubble
  index: number;
  elevation: number;
  ceiling: number;
  root: THREE.Group;
  layers: Record<LayerKey | "walls" | "model" | "badges" | "exitTags", THREE.Group>;
  roomLabels: RoomLabel[];
  caption: CSS2DObject;
  countEl: HTMLElement;
  clip: THREE.Plane;
  ghost: boolean;
  mats: {
    wall: THREE.MeshStandardMaterial;
    edge: THREE.LineBasicMaterial;
    tint: THREE.MeshBasicMaterial;
    outline: THREE.LineBasicMaterial;
    model: THREE.Material[];
  };
  wallCache: Partial<Record<WallMode, THREE.Group>>;
}

export interface Shaft {
  letter: string;
  color: THREE.Color;
  // One stop per level the stair serves, in natural coordinates.
  stops: Map<string, [number, number]>;
}

export interface Floors {
  levels: FloorLevel[];
  byId: Map<string, FloorLevel>;
  shafts: Shaft[];
  pulses: THREE.Mesh[];
  footprint: THREE.Box3;
  attachModel: (gltfRoot: THREE.Object3D | null) => void;
  setWalls: (mode: WallMode) => void;
  layout: (mode: ViewMode, focus: string, spacing: number, aspect: number) => THREE.Box3;
  declutter: (pxPerMeter: number) => void;
  setLayer: (key: LayerKey, visible: boolean) => void;
  setCount: (level: string, people: number | null) => void;
  setRecalled: (recalled: boolean) => void;
  levelOf: (object: THREE.Object3D) => FloorLevel | undefined;
  place: (level: string, x: number, z: number, out?: THREE.Vector3) => THREE.Vector3;
}

function el(tag: string, className: string, content = "") {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = content;
  return element;
}

function tag(element: HTMLElement) {
  return new CSS2DObject(element);
}

// ISO 7010 E001-style running figure, white on exit green.
const EXIT_PICTOGRAM = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="14.5" cy="4" r="2.2" fill="currentColor" stroke="none"/><path d="M6.5 10 9.5 7.6 13.5 7.5 16 11 19 11.5M13.5 7.5 10.5 14M4.5 20 8.5 17.5 10.5 14 14 16.5 13.5 21.5"/></svg>`;

function segments(points: number[][], closed: boolean, y: number, out: number[]) {
  const count = closed ? points.length : points.length - 1;
  for (let i = 0; i < count; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    out.push(a[0], y, a[1], b[0], y, b[1]);
  }
}

function lineSegments(positions: number[], material: THREE.LineBasicMaterial) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const lines = new THREE.LineSegments(geometry, material);
  lines.raycast = () => {};
  return lines;
}

function roomTint(type: string | undefined) {
  return cssColor(`--plan-room-${type ?? "unknown"}`, css.getPropertyValue("--plan-room-unknown").trim() || "#E9EDF2");
}

// Restyle display.glb meshes as a white card model. Mesh names come from the exporters:
// LiDAR writes "scan" plus one "w_*" per wall, blueprint writes walls/floors/doors/exits, Tepper floor_L*/walls_L*.
function modelColor(name: string) {
  if (name.startsWith("exits")) return token("exit");
  if (name.startsWith("floor") || name.startsWith("scan")) return token("line-strong");
  if (name.startsWith("doors") || name.startsWith("stairs")) return token("drafting");
  return token("model");
}

function wallsGroup(walls: NonNullable<SceneDoc["walls"]>, elevation: number, mode: WallMode, level: FloorLevel) {
  const group = new THREE.Group();
  if (mode === "off" || !walls.length) return group;
  const geometries = walls.map((wall) => {
    const shape = new THREE.Shape(wall.polygon.map(([x, z]) => new THREE.Vector2(x, -z)));
    const height = mode === "cutaway" ? Math.min(wall.height_m, CUTAWAY_HEIGHT_M) : wall.height_m;
    return new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false }).rotateX(-Math.PI / 2).translate(0, elevation, 0);
  });
  const merged = mergeGeometries(geometries.map((geometry) => (geometry.index ? geometry.toNonIndexed() : geometry)), false);
  geometries.forEach((geometry) => geometry.dispose());
  if (!merged) return group;
  const mesh = new THREE.Mesh(merged, level.mats.wall);
  mesh.name = `walls_${level.id}`;
  mesh.raycast = () => {}; // clicks land on the floor, not wall tops
  // Ink outlines give the walls their drawing-set look and keep ghosted floors legible.
  group.add(mesh, lineSegments(Array.from(new THREE.EdgesGeometry(merged, 30).attributes.position.array), level.mats.edge));
  return group;
}

// Group stair links into shafts: link endpoints on the same level within 3 m are one stair.
function findShafts(doc: SceneDoc): Shaft[] {
  const links = (doc.links ?? []).filter((link) => link.type === "stairs");
  const parent = links.map((_, i) => i);
  const find = (i: number): number => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const ends = links.flatMap((link, i) => [
    { i, level: link.from.level, p: link.from.position },
    { i, level: link.to.level, p: link.to.position },
  ]);
  for (const a of ends) {
    for (const b of ends) {
      if (a.i < b.i && a.level === b.level && Math.hypot(a.p[0] - b.p[0], a.p[1] - b.p[1]) < 3) parent[find(b.i)] = find(a.i);
    }
  }
  const groups = new Map<number, typeof ends>();
  for (const end of ends) groups.set(find(end.i), [...(groups.get(find(end.i)) ?? []), end]);
  const order = (level: string) => doc.levels.findIndex((item) => item.id === level);
  return [...groups.values()]
    .sort((a, b) => Math.min(...a.map((e) => order(e.level))) - Math.min(...b.map((e) => order(e.level))) || a[0].p[0] - b[0].p[0])
    .map((members, k) => {
      const stops = new Map<string, [number, number]>();
      for (const end of members) if (!stops.has(end.level)) stops.set(end.level, [end.p[0], end.p[1]]);
      return { letter: String.fromCharCode(65 + k), color: new THREE.Color(STAIR_COLORS[k % STAIR_COLORS.length]), stops };
    });
}

const TREAD_M = 0.45;

// Lines across a stair polygon, perpendicular to its longest edge (the run), clipped to the polygon.
function treadLines(polygon: number[][], y: number, out: number[]) {
  let longest = 0;
  let u: [number, number] = [1, 0];
  polygon.forEach((p, i) => {
    const q = polygon[(i + 1) % polygon.length];
    const length = Math.hypot(q[0] - p[0], q[1] - p[1]);
    if (length > longest) [longest, u] = [length, [(q[0] - p[0]) / length, (q[1] - p[1]) / length]];
  });
  const v: [number, number] = [-u[1], u[0]];
  const ab = polygon.map((p) => [p[0] * u[0] + p[1] * u[1], p[0] * v[0] + p[1] * v[1]]);
  const as = ab.map((p) => p[0]);
  for (let a = Math.min(...as) + TREAD_M / 2; a < Math.max(...as); a += TREAD_M) {
    const bs: number[] = [];
    ab.forEach((p, i) => {
      const q = ab[(i + 1) % ab.length];
      if ((p[0] - a) * (q[0] - a) < 0) bs.push(p[1] + ((a - p[0]) * (q[1] - p[1])) / (q[0] - p[0]));
    });
    bs.sort((m, n) => m - n);
    for (let k = 0; k + 1 < bs.length; k += 2) {
      out.push(a * u[0] + bs[k] * v[0], y, a * u[1] + bs[k] * v[1], a * u[0] + bs[k + 1] * v[0], y, a * u[1] + bs[k + 1] * v[1]);
    }
  }
}

// The floor's own label for the caption bubble: "Floor A" -> A, "Floor 3" -> 3, "Ground floor" -> 1st by order.
function levelShort(name: string, index: number) {
  const last = name.trim().split(/\s+/).pop() ?? "";
  if (/^[A-Z0-9]{1,3}$/i.test(last)) return last.toUpperCase();
  return name.match(/\d+/)?.[0] ?? String(index + 1);
}

export function buildFloors(doc: SceneDoc, scene: THREE.Scene): Floors {
  const pulses: THREE.Mesh[] = [];
  const footprint = new THREE.Box3();
  for (const vertex of doc.navmesh.vertices) footprint.expandByPoint(new THREE.Vector3(vertex[0], 0, vertex[2]));
  for (const wall of doc.walls ?? []) for (const [x, z] of wall.polygon) footprint.expandByPoint(new THREE.Vector3(x, 0, z));
  // Exit signs sit on or just outside the wall line; keep them (and their labels) inside the fitted view.
  for (const exit of doc.exits) footprint.expandByPoint(new THREE.Vector3(exit.position[0], 0, exit.position[1]));
  footprint.expandByVector(new THREE.Vector3(3, 0, 3));
  const shafts = findShafts(doc);
  const layerOn: Record<LayerKey, boolean> = { navmesh: true, rooms: true, doors: true, exits: true };
  let wallMode: WallMode = "cutaway";
  let viewMode: ViewMode = "single";
  // Levels whose walls come from display.glb meshes instead of scene.json (scans, curved walls).
  const glbWallLevels = new Set<string>();

  const levels: FloorLevel[] = doc.levels.map((info, index) => {
    const name = info.name ?? info.id;
    const root = new THREE.Group();
    root.userData.level = info.id;
    const layers = {
      navmesh: new THREE.Group(), rooms: new THREE.Group(), doors: new THREE.Group(), exits: new THREE.Group(),
      walls: new THREE.Group(), model: new THREE.Group(), badges: new THREE.Group(), exitTags: new THREE.Group(),
    };
    layers.exits.add(layers.exitTags);
    root.add(...Object.values(layers).filter((group) => group !== layers.exitTags));
    const countEl = el("span", "fc-count", "");
    const captionEl = el("div", "floor-caption");
    const bubble = el("span", "fc-bubble", levelShort(name, index));
    captionEl.append(bubble, el("b", "fc-name", name), countEl);
    const caption = tag(captionEl);
    caption.center.set(0, 0);
    caption.visible = false;
    root.add(caption);
    scene.add(root);
    return {
      id: info.id, name, short: levelShort(name, index), index, elevation: info.elevation_m,
      ceiling: (info as { ceiling_height_m?: number }).ceiling_height_m ?? 3, root, layers, roomLabels: [], caption, countEl,
      clip: new THREE.Plane(new THREE.Vector3(0, -1, 0), info.elevation_m + CUTAWAY_HEIGHT_M), ghost: false,
      mats: {
        wall: new THREE.MeshStandardMaterial({ color: token("model"), roughness: 0.9, metalness: 0 }),
        edge: new THREE.LineBasicMaterial({ color: token("ground"), transparent: true }),
        tint: new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.16, depthWrite: false, side: THREE.DoubleSide }),
        outline: new THREE.LineBasicMaterial({ color: token("drafting"), transparent: true, opacity: 0.8 }),
        model: [],
      },
      wallCache: {},
    };
  });
  const byId = new Map(levels.map((level) => [level.id, level]));
  const first = levels[0];

  // ---- static per-level overlays: room tints, outlines, labels, doors, exits, navmesh ----
  for (const level of levels) {
    const y = level.elevation;
    const tints: THREE.BufferGeometry[] = [];
    const outline: number[] = [];
    for (const room of doc.rooms.filter((item) => item.level === level.id)) {
      if (room.polygon.length < 3) continue;
      const shape = new THREE.Shape(room.polygon.map(([x, z]) => new THREE.Vector2(x, -z)));
      const shaped = new THREE.ShapeGeometry(shape).rotateX(-Math.PI / 2).translate(0, y + 0.03, 0);
      const geometry = shaped.index ? shaped.toNonIndexed() : shaped;
      const color = roomTint((room as { type?: string }).type);
      const colors = new Float32Array(geometry.attributes.position.count * 3);
      for (let i = 0; i < colors.length; i += 3) color.toArray(colors, i);
      geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      geometry.deleteAttribute("normal");
      geometry.deleteAttribute("uv");
      tints.push(geometry);
      segments(room.polygon, true, y + 0.06, outline);
      const xs = room.polygon.map((point) => point[0]);
      const zs = room.polygon.map((point) => point[1]);
      level.roomLabels.push({ room, w: Math.max(...xs) - Math.min(...xs), d: Math.max(...zs) - Math.min(...zs), tag: null });
    }
    if (tints.length) {
      const tint = new THREE.Mesh(mergeGeometries(tints, false)!, level.mats.tint);
      tint.raycast = () => {};
      level.layers.rooms.add(tint);
    }
    level.layers.rooms.add(lineSegments(outline, level.mats.outline));

    const doorLines: number[] = [];
    const exitLines: number[] = [];
    for (const door of doc.doors.filter((item) => item.level === level.id)) segments(door.segment, false, y + 0.08, door.is_exit ? exitLines : doorLines);
    level.layers.doors.add(lineSegments(doorLines, new THREE.LineBasicMaterial({ color: token("model") })));
    level.layers.doors.add(lineSegments(exitLines, new THREE.LineBasicMaterial({ color: token("exit-bright") })));

    const markerGeometry = new THREE.CylinderGeometry(0.32, 0.32, 0.04, 32);
    const markerMaterial = new THREE.MeshBasicMaterial({ color: token("exit-bright") });
    for (const exit of doc.exits.filter((item) => item.level === level.id)) {
      const marker = new THREE.Mesh(markerGeometry, markerMaterial);
      marker.position.set(exit.position[0], y + 0.1, exit.position[1]);
      const pulse = new THREE.Mesh(
        new THREE.RingGeometry(0.34, 0.42, 48).rotateX(-Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: token("exit-bright"), transparent: true, depthWrite: false }),
      );
      pulse.position.copy(marker.position);
      pulses.push(pulse);
      const element = el("div", "exit-label");
      element.innerHTML = EXIT_PICTOGRAM;
      element.append(el("span", "exit-text", exit.label));
      element.title = exit.label;
      const label = tag(element);
      label.position.copy(marker.position);
      label.center.set(0.5, 1.7); // sit the sign just above its marker in every view
      level.layers.exits.add(marker, pulse);
      level.layers.exitTags.add(label);
    }

    const positions: number[] = [];
    doc.navmesh.triangles.forEach((triangle, index) => {
      if (doc.navmesh.triangle_level[index] !== level.id) return;
      for (const corner of triangle) {
        const vertex = doc.navmesh.vertices[corner];
        positions.push(vertex[0], vertex[1] + 0.04, vertex[2]);
      }
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    level.layers.navmesh.add(new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: token("walkable"), transparent: true, opacity: 0.4, depthWrite: false, side: THREE.DoubleSide })));
  }

  // ---- stairs: polygons in the shaft color on both levels, badges per stop, shaft lines in 3D ----
  const shaftOf = (level: string, p: number[]) => {
    let best: Shaft | null = null;
    let bestD = 6; // a stair polygon belongs to the shaft whose stop is within 6 m
    for (const shaft of shafts) {
      const stop = shaft.stops.get(level);
      if (!stop) continue;
      const d = Math.hypot(stop[0] - p[0], stop[1] - p[1]);
      if (d < bestD) [best, bestD] = [shaft, d];
    }
    return best;
  };
  for (const stair of doc.stairs ?? []) {
    const center = stair.polygon.reduce((acc, p) => [acc[0] + p[0] / stair.polygon.length, acc[1] + p[1] / stair.polygon.length], [0, 0]);
    for (const levelId of [stair.from_level, stair.to_level]) {
      const level = byId.get(levelId);
      const shaft = shaftOf(levelId, stair.bottom ?? center) ?? shaftOf(levelId, center);
      if (!level) continue;
      const color = shaft?.color ?? token("drafting");
      const positions: number[] = [];
      segments(stair.polygon, true, level.elevation + 0.09, positions);
      level.layers.badges.add(lineSegments(positions, new THREE.LineBasicMaterial({ color })));
      // Tread lines across the run: the drawing-set symbol for a stair, readable at any zoom.
      const treads: number[] = [];
      treadLines(stair.polygon, level.elevation + 0.09, treads);
      level.layers.badges.add(lineSegments(treads, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.75 })));
      const shape = new THREE.Shape(stair.polygon.map(([x, z]) => new THREE.Vector2(x, -z)));
      const fill = new THREE.Mesh(
        new THREE.ShapeGeometry(shape).rotateX(-Math.PI / 2).translate(0, level.elevation + 0.05, 0),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.22, depthWrite: false, side: THREE.DoubleSide }),
      );
      fill.raycast = () => {};
      level.layers.badges.add(fill);
    }
  }
  for (const shaft of shafts) {
    const served = [...shaft.stops.keys()].map((id) => byId.get(id)?.short ?? id).sort((a, b) => Number(a) - Number(b));
    for (const [levelId, stop] of shaft.stops) {
      const level = byId.get(levelId);
      if (!level) continue;
      const element = el("div", "stair-badge", shaft.letter);
      element.style.setProperty("--stair", `#${shaft.color.getHexString()}`);
      element.title = `Stair ${shaft.letter}, floors ${served[0]} to ${served[served.length - 1]}`;
      const badge = tag(element);
      badge.position.set(stop[0], level.elevation + 0.1, stop[1]);
      level.layers.badges.add(badge);
    }
  }
  // Elevators: a shaft symbol (square with an X) on every floor served. In a fire they are recalled
  // and not used, so the symbol greys out with a small "Recalled" tag.
  const ELEVATOR_M = 1.6;
  const elevatorMaterial = new THREE.LineBasicMaterial({ color: token("model") });
  const recalledTags: CSS2DObject[] = [];
  for (const elevator of doc.elevators ?? []) {
    const [x, z] = elevator.position;
    const h = ELEVATOR_M / 2;
    for (const levelId of elevator.levels) {
      const level = byId.get(levelId);
      if (!level) continue;
      const y = level.elevation + 0.09;
      const square = [[x - h, z - h], [x + h, z - h], [x + h, z + h], [x - h, z + h]];
      const positions: number[] = [];
      segments(square, true, y, positions);
      positions.push(x - h, y, z - h, x + h, y, z + h, x + h, y, z - h, x - h, y, z + h);
      level.layers.badges.add(lineSegments(positions, elevatorMaterial));
      const label = tag(el("div", "elevator-tag", "Recalled"));
      label.position.set(x, y, z + h);
      label.center.set(0.5, -0.2);
      label.visible = false;
      level.layers.badges.add(label);
      recalledTags.push(label);
    }
  }
  function setRecalled(recalled: boolean) {
    elevatorMaterial.color.copy(token(recalled ? "line-strong" : "model"));
    for (const label of recalledTags) label.visible = recalled;
  }

  // Shaft lines connect the stops of each stair through the stacked or exploded floors.
  const shaftLines = new THREE.Group();
  scene.add(shaftLines);
  function drawShaftLines() {
    shaftLines.clear();
    if (viewMode !== "stacked" && viewMode !== "exploded") return;
    const tmp = new THREE.Vector3();
    for (const link of (doc.links ?? []).filter((item) => item.type === "stairs")) {
      const shaft = shaftOf(link.from.level, link.from.position);
      const a = place(link.from.level, link.from.position[0], link.from.position[1], tmp).clone();
      const b = place(link.to.level, link.to.position[0], link.to.position[1], tmp).clone();
      const geometry = new THREE.BufferGeometry().setFromPoints([a.setY(a.y + 0.1), b.setY(b.y + 0.1)]);
      const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: shaft?.color ?? token("drafting"), depthTest: false, transparent: true }));
      line.renderOrder = 8;
      shaftLines.add(line);
    }
  }

  function place(level: string, x: number, z: number, out = new THREE.Vector3()) {
    const item = byId.get(level) ?? first;
    return out.set(x + item.root.position.x, item.elevation + item.root.position.y, z + item.root.position.z);
  }

  // ---- display.glb ----
  function attachModel(gltfRoot: THREE.Object3D | null) {
    const meshes: THREE.Mesh[] = [];
    gltfRoot?.updateMatrixWorld(true);
    gltfRoot?.traverse((object) => {
      if (object instanceof THREE.Mesh) meshes.push(object);
    });
    for (const mesh of meshes) {
      const suffix = mesh.name.match(/_(L\w+?)(?:_\d+)?$/)?.[1];
      const level = (suffix && byId.get(suffix)) || null;
      if (mesh.name.startsWith("stairs")) {
        // The stair mesh spans every level; the stair polygons and shaft lines stand in for it.
        continue;
      }
      // Wall meshes are rebuilt per level from scene.json so the cut has clean tops and the view can change height.
      if (mesh.name.startsWith("walls") && (doc.walls ?? []).length) continue;
      const owner = level ?? first;
      if (mesh.name.startsWith("w_") || mesh.name.startsWith("walls")) glbWallLevels.add(owner.id);
      const isFloor = mesh.name.startsWith("floor");
      const material = new THREE.MeshStandardMaterial({
        color: modelColor(mesh.name), roughness: 0.9, metalness: 0,
        clippingPlanes: isFloor ? [] : [owner.clip], side: isFloor ? THREE.FrontSide : THREE.DoubleSide,
      });
      mesh.material = material;
      owner.mats.model.push(material);
      if (mesh.name.startsWith("w_") || mesh.name.startsWith("walls")) {
        const edgeMaterial = new THREE.LineBasicMaterial({ color: token("ground"), clippingPlanes: [owner.clip], transparent: true });
        const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 30), edgeMaterial);
        edges.raycast = () => {};
        mesh.add(edges);
        owner.mats.model.push(edgeMaterial);
      }
      // Re-parent into the level group keeping its world transform (glTF nodes may carry offsets).
      const world = mesh.matrixWorld.clone();
      owner.layers.model.add(mesh);
      world.decompose(mesh.position, mesh.quaternion, mesh.scale);
    }
    // Scenes without a floor mesh get one from the walkable navmesh, just under it.
    for (const level of levels) {
      if (level.layers.model.children.some((child) => child.name.startsWith("floor") || child.name.startsWith("scan"))) continue;
      const source = level.layers.navmesh.children[0] as THREE.Mesh;
      const material = new THREE.MeshStandardMaterial({ color: token("line-strong"), roughness: 0.9, metalness: 0, side: THREE.DoubleSide });
      const floor = new THREE.Mesh(source.geometry.clone().translate(0, -0.09, 0), material);
      floor.name = `floor_${level.id}`;
      level.mats.model.push(material);
      level.layers.model.add(floor);
    }
    setWalls(wallMode);
  }

  function updateClip(level: FloorLevel) {
    const height = wallMode === "cutaway" ? CUTAWAY_HEIGHT_M : wallMode === "off" ? 0.05 : Math.max(level.ceiling - 0.15, CUTAWAY_HEIGHT_M);
    level.clip.constant = level.elevation + level.root.position.y + height;
  }

  function setWalls(mode: WallMode) {
    wallMode = mode;
    for (const level of levels) {
      updateClip(level);
      if (glbWallLevels.has(level.id)) continue;
      const walls = (doc.walls ?? []).filter((wall) => wall.level === level.id);
      level.wallCache[mode] ??= wallsGroup(walls, level.elevation, mode, level);
      level.layers.walls.clear();
      level.layers.walls.add(level.wallCache[mode]!);
    }
  }

  // Ghosted floors keep only their outlines (MassMotion's "faded levels"); the focused floor stays solid.
  function style(level: FloorLevel, ghost: boolean, above: boolean) {
    level.ghost = ghost;
    const { wall, edge, tint, outline, model } = level.mats;
    const solidOpacity = viewMode === "stacked" || viewMode === "exploded" ? 0.92 : 1;
    for (const material of [wall, ...model.filter((m) => !(m instanceof THREE.LineBasicMaterial))]) {
      const transparent = ghost || solidOpacity < 1;
      if (material.transparent !== transparent) material.needsUpdate = true;
      material.transparent = transparent;
      material.opacity = ghost ? (above ? 0.03 : 0.14) : solidOpacity;
      material.depthWrite = !ghost;
    }
    // Floors above the focus are a faint silhouette so they never veil the floor being read.
    edge.opacity = ghost ? (above ? 0.1 : 0.4) : 1;
    for (const material of model.filter((m) => m instanceof THREE.LineBasicMaterial)) material.opacity = edge.opacity;
    tint.opacity = ghost ? (above ? 0.015 : 0.04) : 0.16;
    outline.opacity = ghost ? (above ? 0.08 : 0.22) : 0.8;
    level.layers.navmesh.visible = layerOn.navmesh && !ghost;
    level.layers.exitTags.visible = !ghost;
    level.layers.badges.visible = viewMode === "side" || viewMode === "single" || !ghost;
    if (ghost) for (const label of level.roomLabels) detach(label);
  }

  function detach(label: RoomLabel) {
    if (!label.tag) return;
    label.tag.removeFromParent(); // CSS2DObject removes its element from the DOM on "removed"
    label.tag = null;
  }

  function layout(mode: ViewMode, focus: string, spacing: number, aspect: number) {
    viewMode = mode;
    const width = footprint.max.x - footprint.min.x;
    const depth = footprint.max.z - footprint.min.z;
    const bounds = new THREE.Box3();
    const focusLevel = byId.get(focus) ?? first;
    if (mode === "side") {
      // Pick the column count that makes each plan largest on this stage.
      const gap = Math.max(2, 0.1 * Math.max(width, depth));
      let best = { cols: 1, scale: 0 };
      for (let cols = 1; cols <= levels.length; cols += 1) {
        const rows = Math.ceil(levels.length / cols);
        const tw = cols * width + (cols - 1) * gap;
        const th = rows * depth + rows * gap; // room for the caption under each plan
        const scale = Math.min(aspect / tw, 1 / th);
        if (scale > best.scale) best = { cols, scale };
      }
      levels.forEach((level, i) => {
        const col = i % best.cols;
        const row = Math.floor(i / best.cols);
        level.root.position.set(col * (width + gap), -level.elevation, row * (depth + gap));
        level.root.visible = true;
        level.caption.position.set(footprint.min.x, level.elevation + 0.1, footprint.max.z + gap * 0.3);
        level.caption.visible = true;
      });
      const rows = Math.ceil(levels.length / best.cols);
      bounds.set(
        new THREE.Vector3(footprint.min.x, 0, footprint.min.z),
        new THREE.Vector3(footprint.min.x + best.cols * width + (best.cols - 1) * gap, 1, footprint.min.z + rows * depth + rows * gap),
      );
    } else {
      for (const level of levels) {
        // On a 150 m footprint a 4 m storey gap is invisible, so the pull-apart grows with the footprint.
        const storey = Math.max(level.elevation / Math.max(level.index, 1), 0.08 * Math.max(width, depth));
        const lift = mode === "exploded" ? level.index * storey * (spacing - 1) : 0;
        level.root.position.set(0, lift, 0);
        level.root.visible = mode !== "single" || level === focusLevel;
        level.caption.visible = false;
        if (level.root.visible) {
          bounds.expandByPoint(new THREE.Vector3(footprint.min.x, level.elevation + lift, footprint.min.z));
          bounds.expandByPoint(new THREE.Vector3(footprint.max.x, level.elevation + lift + CUTAWAY_HEIGHT_M, footprint.max.z));
        }
      }
    }
    const threeD = mode === "stacked" || mode === "exploded";
    for (const level of levels) {
      updateClip(level);
      style(level, threeD && level !== focusLevel, level.index > focusLevel.index);
    }
    drawShaftLines();
    return bounds;
  }

  function declutter(pxPerMeter: number) {
    document.body.classList.toggle("zoomed-out", pxPerMeter < 24); // exit names only when there is room
    document.body.classList.toggle("close-zoom", pxPerMeter >= 30); // room areas only up close
    for (const level of levels) {
      const allowed = layerOn.rooms && level.root.visible && !level.ghost;
      for (const label of level.roomLabels) {
        const show = allowed && label.w * pxPerMeter > LABEL_MIN_W_PX && label.d * pxPerMeter > LABEL_MIN_D_PX;
        if (show && !label.tag) {
          const element = el("div", "room-label");
          element.append(el("b", "", label.room.name));
          if (label.room.area_m2) element.append(el("span", "", `${label.room.area_m2.toFixed(1)} m²`));
          label.tag = tag(element);
          label.tag.position.set(label.room.centroid[0], level.elevation + 0.1, label.room.centroid[1]);
          level.layers.rooms.add(label.tag);
        } else if (!show) detach(label);
      }
    }
  }

  function setLayer(key: LayerKey, visible: boolean) {
    layerOn[key] = visible;
    for (const level of levels) level.layers[key].visible = key === "navmesh" ? visible && !level.ghost : visible;
  }

  function setCount(level: string, people: number | null) {
    const item = byId.get(level);
    if (!item) return;
    const value = people === null ? "" : `${people} ${people === 1 ? "person" : "people"}`;
    if (item.countEl.textContent !== value) item.countEl.textContent = value;
  }

  function levelOf(object: THREE.Object3D) {
    for (let node: THREE.Object3D | null = object; node; node = node.parent) {
      if (node.userData.level) return byId.get(node.userData.level);
    }
    return undefined;
  }

  return { levels, byId, shafts, pulses, footprint, attachModel, setWalls, layout, declutter, setLayer, setCount, setRecalled, levelOf, place };
}
