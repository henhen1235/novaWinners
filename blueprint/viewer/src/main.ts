import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";

type SceneDoc = {
  meta?: { name: string; source: string; scale_method: string; generated_at: string };
  levels: { id: string; name?: string; elevation_m: number }[];
  rooms: { id: string; level: string; name: string; polygon: number[][]; centroid: number[]; area_m2?: number }[];
  doors: { id: string; level: string; segment: number[][]; is_exit: boolean }[];
  exits: { id: string; level: string; position: number[]; label: string }[];
  warnings: { code: string; message: string; severity: string }[];
  navmesh: { vertices: number[][]; triangles: number[][]; triangle_level: string[] };
};

const params = new URLSearchParams(location.search);
const glbUrl = params.get("glb") ?? "/office/display.glb";
const sceneUrl = params.get("scene") ?? "/office/scene.json";

const stage = document.querySelector<HTMLDivElement>("#stage")!;
const readout = document.querySelector<HTMLSpanElement>("#readout")!;
const levelSelect = document.querySelector<HTMLSelectElement>("#level")!;
const warningList = document.querySelector<HTMLElement>("#warnings")!;
const buildingName = document.querySelector<HTMLElement>("#building")!;
const titleBlock = document.querySelector<HTMLElement>("#title-block")!;

// Colors come from design/tokens.css so the viewer and the replay UI share one palette.
const css = getComputedStyle(document.documentElement);
const token = (name: string) => new THREE.Color(css.getPropertyValue(`--color-${name}`).trim());
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const WALK_SPEED = 1.3;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(stage.clientWidth, stage.clientHeight);
stage.appendChild(renderer.domElement);
const labelRenderer = new CSS2DRenderer();
labelRenderer.setSize(stage.clientWidth, stage.clientHeight);
Object.assign(labelRenderer.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
stage.appendChild(labelRenderer.domElement);

const scene = new THREE.Scene();
scene.background = token("ground");
const camera = new THREE.PerspectiveCamera(40, stage.clientWidth / stage.clientHeight, 0.05, 500);
camera.position.set(8, 14, 12);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(4, 0, 4);
controls.enableDamping = true;
controls.update();

scene.add(new THREE.HemisphereLight(token("text"), token("ground"), 2.2));
const sun = new THREE.DirectionalLight(0xffffff, 3.2);
sun.position.set(-6, 18, 10);
scene.add(sun);

const groups: Record<string, THREE.Object3D> = {
  navmesh: new THREE.Group(),
  rooms: new THREE.Group(),
  doors: new THREE.Group(),
  exits: new THREE.Group(),
  warnings: new THREE.Group(),
  path: new THREE.Group(),
};
for (const group of Object.values(groups)) scene.add(group);
const pulses: THREE.Mesh[] = [];
// The route draws over walls: it is the one thing the audience must always see.
const pathMaterial = new LineMaterial({ color: token("route").getHex(), linewidth: 4, depthTest: false });
pathMaterial.resolution.set(stage.clientWidth, stage.clientHeight);
let drawStart = 0;
let segmentCount = 0;

const modelRoot = new THREE.Group();
scene.add(modelRoot);

let doc: SceneDoc | null = null;
let activeLevel = "";
// Distance that fits the whole model in view; set when display.glb loads.
let fitDistance = 20;

function frame(x: number, z: number, elevation = 0) {
  controls.target.set(x, elevation, z);
  camera.position.set(x + 6, elevation + 10, z + 8);
  controls.update();
}

function setTopDown() {
  const target = controls.target.clone();
  camera.position.set(target.x, target.y + fitDistance * 1.1, target.z + 0.01);
  camera.up.set(0, 0, -1);
  controls.update();
  markView("top");
}

function setOrbit() {
  camera.up.set(0, 1, 0);
  const target = controls.target.clone();
  // Three-quarter view from the south-west, about 55 degrees above the floor so rooms stay visible.
  const offset = new THREE.Vector3(-0.4, 1, 0.6).normalize().multiplyScalar(fitDistance);
  camera.position.copy(target).add(offset);
  controls.update();
  markView("orbit");
}

function markView(active: "top" | "orbit") {
  for (const id of ["top", "orbit"]) {
    document.querySelector(`#${id}`)!.setAttribute("aria-pressed", String(id === active));
  }
}

document.querySelector("#top")!.addEventListener("click", setTopDown);
document.querySelector("#orbit")!.addEventListener("click", setOrbit);
for (const [id, key] of [
  ["show-nav", "navmesh"],
  ["show-rooms", "rooms"],
  ["show-doors", "doors"],
  ["show-exits", "exits"],
  ["show-warnings", "warnings"],
] as const) {
  document.querySelector<HTMLInputElement>(`#${id}`)!.addEventListener("change", (event) => {
    groups[key].visible = (event.target as HTMLInputElement).checked;
  });
}
groups.warnings.visible = false;

function clear(group: THREE.Object3D) {
  while (group.children.length) group.remove(group.children[0]);
}

function lineLoop(points: number[][], color: THREE.Color, y: number) {
  const vectors = points.map((point) => new THREE.Vector3(point[0], y, point[1]));
  if (vectors.length) vectors.push(vectors[0].clone());
  const geometry = new THREE.BufferGeometry().setFromPoints(vectors);
  return new THREE.Line(geometry, new THREE.LineBasicMaterial({ color }));
}

// ISO 7010 E001-style running figure, white on exit green.
const EXIT_PICTOGRAM = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="14.5" cy="4" r="2.2" fill="currentColor" stroke="none"/><path d="M6.5 10 9.5 7.6 13.5 7.5 16 11 19 11.5M13.5 7.5 10.5 14M4.5 20 8.5 17.5 10.5 14 14 16.5 13.5 21.5"/></svg>`;

function label(className: string, build: (element: HTMLDivElement) => void) {
  const element = document.createElement("div");
  element.className = className;
  build(element);
  return new CSS2DObject(element);
}

function text(tag: string, content: string) {
  const element = document.createElement(tag);
  element.textContent = content;
  return element;
}

function drawTitleBlock(data: SceneDoc) {
  const errors = data.warnings.filter((warning) => warning.severity === "error").length;
  const date = data.meta?.generated_at.slice(0, 10) ?? "";
  const rows: [string, string, string?][] = [
    ["Source", data.meta?.source ?? "unknown"],
    ["Scale", data.meta?.scale_method.replace(/_/g, " ") ?? ""],
    ["Rooms, exits", `${data.rooms.length}, ${data.exits.length}`],
    ["Generated", date],
    ["Checks", errors ? `${errors} errors` : `${data.warnings.length} warnings`, errors ? "fail" : "pass"],
  ];
  titleBlock.replaceChildren(text("dt", data.meta?.name ?? "Scene"));
  titleBlock.firstElementChild!.className = "tb-name";
  for (const [key, value, state] of rows) {
    const dd = text("dd", value);
    if (state) dd.className = state;
    titleBlock.append(text("dt", key), dd);
  }
  titleBlock.hidden = false;
  buildingName.textContent = data.meta?.name ?? "";
}

function drawScene(data: SceneDoc) {
  doc = data;
  levelSelect.innerHTML = "";
  for (const level of data.levels) {
    const option = document.createElement("option");
    option.value = level.id;
    option.textContent = level.name ?? level.id;
    levelSelect.appendChild(option);
  }
  activeLevel = data.levels[0]?.id ?? "";
  rebuildOverlays();
  drawTitleBlock(data);
  warningList.innerHTML = "";
  if (!data.warnings.length) {
    warningList.append(text("p", "No warnings in this scene."));
    warningList.firstElementChild!.className = "clear";
  }
  for (const warning of data.warnings) {
    const button = document.createElement("button");
    button.className = warning.severity;
    button.append(text("code", warning.code), warning.message);
    button.addEventListener("click", () => {
      const room = data.rooms.find((item) => warning.message.includes(item.id));
      if (room) frame(room.centroid[0], room.centroid[1]);
    });
    warningList.appendChild(button);
  }
}

function rebuildOverlays() {
  if (!doc) return;
  const elevation = doc.levels.find((level) => level.id === activeLevel)?.elevation_m ?? 0;
  clear(groups.rooms);
  clear(groups.doors);
  clear(groups.exits);
  clear(groups.navmesh);
  for (const room of doc.rooms.filter((item) => item.level === activeLevel)) {
    groups.rooms.add(lineLoop(room.polygon, token("drafting"), elevation + 0.06));
    const tag = label("room-label", (element) => {
      element.append(text("b", room.name));
      if (room.area_m2) element.append(text("span", `${room.area_m2.toFixed(1)} m²`));
    });
    tag.position.set(room.centroid[0], elevation + 0.1, room.centroid[1]);
    groups.rooms.add(tag);
  }
  for (const door of doc.doors.filter((item) => item.level === activeLevel)) {
    groups.doors.add(lineLoop(door.segment, door.is_exit ? token("exit-bright") : token("model"), elevation + 0.08));
  }
  pulses.length = 0;
  for (const exit of doc.exits.filter((item) => item.level === activeLevel)) {
    const marker = new THREE.Mesh(
      new THREE.CylinderGeometry(0.32, 0.32, 0.04, 32),
      new THREE.MeshBasicMaterial({ color: token("exit-bright") }),
    );
    marker.position.set(exit.position[0], elevation + 0.1, exit.position[1]);
    const pulse = new THREE.Mesh(
      new THREE.RingGeometry(0.34, 0.42, 48).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: token("exit-bright"), transparent: true, depthWrite: false }),
    );
    pulse.position.copy(marker.position);
    pulses.push(pulse);
    const tag = label("exit-label", (element) => {
      element.innerHTML = EXIT_PICTOGRAM;
      element.append(exit.label);
    });
    tag.position.set(exit.position[0], elevation + 0.1, exit.position[1]);
    tag.center.set(0.5, 1.7); // sit the sign just above its marker in every view
    groups.exits.add(marker, pulse, tag);
  }
  const vertices = doc.navmesh.vertices;
  const positions: number[] = [];
  doc.navmesh.triangles.forEach((triangle, index) => {
    if (doc!.navmesh.triangle_level[index] !== activeLevel) return;
    for (const corner of triangle) {
      const vertex = vertices[corner];
      positions.push(vertex[0], vertex[1] + 0.04, vertex[2]);
    }
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  groups.navmesh.add(new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: token("walkable"), transparent: true, opacity: 0.4, depthWrite: false, side: THREE.DoubleSide })));
}

levelSelect.addEventListener("change", () => {
  activeLevel = levelSelect.value;
  rebuildOverlays();
});

function pathOnMesh(start: THREE.Vector3) {
  if (!doc) return;
  const vertices = doc.navmesh.vertices.map((vertex) => new THREE.Vector3(vertex[0], vertex[1], vertex[2]));
  const triangles = doc.navmesh.triangles;
  const levels = doc.navmesh.triangle_level;
  const centroids = triangles.map((triangle) => {
    const a = vertices[triangle[0]];
    const b = vertices[triangle[1]];
    const c = vertices[triangle[2]];
    return new THREE.Vector3().add(a).add(b).add(c).multiplyScalar(1 / 3);
  });
  const graph = new Map<number, number[]>();
  const edgeOwner = new Map<string, number>();
  triangles.forEach((triangle, index) => {
    graph.set(index, []);
    for (let corner = 0; corner < 3; corner += 1) {
      const u = triangle[corner];
      const v = triangle[(corner + 1) % 3];
      const key = u < v ? `${u}:${v}` : `${v}:${u}`;
      const other = edgeOwner.get(key);
      if (other === undefined) edgeOwner.set(key, index);
      else {
        graph.get(index)!.push(other);
        graph.get(other)!.push(index);
      }
    }
  });
  let startTri = 0;
  let best = Infinity;
  centroids.forEach((centroid, index) => {
    if (levels[index] !== activeLevel) return;
    const distance = Math.hypot(centroid.x - start.x, centroid.z - start.z);
    if (distance < best) {
      best = distance;
      startTri = index;
    }
  });
  const exits = doc.exits.filter((exit) => exit.level === activeLevel);
  let goal = -1;
  let goalDistance = Infinity;
  let goalExit = exits[0];
  exits.forEach((exit) => {
    centroids.forEach((centroid, index) => {
      if (levels[index] !== activeLevel) return;
      const distance = Math.hypot(centroid.x - exit.position[0], centroid.z - exit.position[1]);
      if (distance < goalDistance) {
        goalDistance = distance;
        goal = index;
        goalExit = exit;
      }
    });
  });
  if (goal < 0) return;
  const previous = new Map<number, number>();
  const distance = new Map<number, number>([[startTri, 0]]);
  const queue = [startTri];
  while (queue.length) {
    queue.sort((a, b) => (distance.get(a) ?? 0) - (distance.get(b) ?? 0));
    const current = queue.shift()!;
    if (current === goal) break;
    for (const next of graph.get(current) ?? []) {
      const step = centroids[current].distanceTo(centroids[next]);
      const nextDistance = (distance.get(current) ?? 0) + step;
      if (nextDistance < (distance.get(next) ?? Infinity)) {
        distance.set(next, nextDistance);
        previous.set(next, current);
        queue.push(next);
      }
    }
  }
  if (!distance.has(goal)) {
    readout.textContent = "No walkable route from here to an exit.";
    readout.className = "";
    return;
  }
  const nodes = [goal];
  while (nodes[0] !== startTri) {
    const prior = previous.get(nodes[0]);
    if (prior === undefined) break;
    nodes.unshift(prior);
  }
  const points = [new THREE.Vector3(start.x, centroids[startTri].y + 0.15, start.z)];
  for (const node of nodes) points.push(centroids[node].clone().setY(centroids[node].y + 0.15));
  let meters = 0;
  for (let index = 1; index < points.length; index += 1) {
    meters += Math.hypot(points[index].x - points[index - 1].x, points[index].z - points[index - 1].z);
  }
  clear(groups.path);
  const line = new Line2(new LineGeometry().setPositions(points.flatMap((point) => point.toArray())), pathMaterial);
  line.computeLineDistances();
  line.renderOrder = 10;
  drawStart = performance.now();
  segmentCount = points.length - 1;
  const origin = new THREE.Mesh(
    new THREE.RingGeometry(0.18, 0.28, 32).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: token("route") }),
  );
  origin.position.copy(points[0]);
  groups.path.add(line, origin);
  showRoute(meters, goalExit?.label ?? "exit");
}

function figure(value: string, unit: string) {
  const element = text("span", "");
  element.className = "figure";
  element.append(text("b", value), text("span", unit));
  return element;
}

function showRoute(meters: number, exitLabel: string) {
  const note = text("span", `to ${exitLabel}, walking at ${WALK_SPEED} m/s`);
  note.className = "route-note";
  readout.replaceChildren(figure(meters.toFixed(1), "m"), figure((meters / WALK_SPEED).toFixed(1), "s"), note);
  readout.className = "has-route";
}

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
renderer.domElement.addEventListener("pointerdown", (event) => {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(modelRoot.children, true);
  if (!hits.length) return;
  pathOnMesh(hits[0].point);
});

// Restyle display.glb as a white card model. Mesh names come from the exporters:
// LiDAR writes "scan" plus one "w_*" per wall, blueprint writes walls/floors/doors/exits.
function modelColor(name: string) {
  if (name.startsWith("exits")) return token("exit");
  if (name.startsWith("floors") || name.startsWith("scan")) return token("line-strong");
  if (name.startsWith("doors") || name.startsWith("stairs")) return token("drafting");
  return token("model");
}

function styleModel(root: THREE.Object3D) {
  const meshes: THREE.Mesh[] = [];
  root.traverse((object) => {
    if (object instanceof THREE.Mesh) meshes.push(object);
  });
  for (const mesh of meshes) {
    mesh.material = new THREE.MeshStandardMaterial({ color: modelColor(mesh.name), roughness: 0.9, metalness: 0 });
    // Ink outlines on the clean walls give the model its drawing-set look; the raw scan is too dense.
    if (mesh.name.startsWith("w_") || mesh.name.startsWith("walls")) {
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 30), new THREE.LineBasicMaterial({ color: token("ground") }));
      edges.raycast = () => {}; // clicks must land on the floor mesh, not on outlines
      mesh.add(edges);
    }
  }
}

function addGrid(box: THREE.Box3, center: THREE.Vector3) {
  const span = Math.ceil(Math.max(box.max.x - box.min.x, box.max.z - box.min.z) / 5) * 5 + 10;
  const minor = new THREE.GridHelper(span, span, token("line"), token("line"));
  const major = new THREE.GridHelper(span, span / 5, token("line-strong"), token("line-strong"));
  for (const grid of [minor, major]) {
    grid.position.set(Math.round(center.x), box.min.y - 0.02, Math.round(center.z));
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = grid === major ? 0.35 : 0.6;
    scene.add(grid);
  }
}

new GLTFLoader().load(glbUrl, (gltf) => {
  styleModel(gltf.scene);
  modelRoot.add(gltf.scene);
  const box = new THREE.Box3().setFromObject(gltf.scene);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  addGrid(box, center);
  const radius = Math.hypot(size.x, size.z) / 2;
  fitDistance = (radius / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))) * 1.05;
  controls.target.copy(center).setY(box.min.y);
  setOrbit();
  readout.textContent = "Click any floor to draw the walking route to the nearest exit.";
});

fetch(sceneUrl)
  .then((response) => response.json())
  .then((data: SceneDoc) => drawScene(data))
  .catch(() => {
    readout.textContent = `Could not load ${sceneUrl}. Pass ?scene=/path/scene.json and check the file is in public/.`;
  });

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  const now = performance.now();
  // The route draws itself from the click point to the exit, one segment at a time.
  const progress = reduceMotion ? 1 : Math.min((now - drawStart) / 700, 1);
  for (const child of groups.path.children) {
    if (child instanceof Line2) child.geometry.instanceCount = Math.ceil(segmentCount * (1 - (1 - progress) ** 3));
  }
  // Exits pulse slowly so they can be found from across the room.
  const phase = reduceMotion ? 0.5 : (now % 2400) / 2400;
  for (const pulse of pulses) {
    pulse.scale.setScalar(1 + phase * 1.2);
    (pulse.material as THREE.MeshBasicMaterial).opacity = 0.7 * (1 - phase);
  }
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
}
animate();

window.addEventListener("resize", () => {
  camera.aspect = stage.clientWidth / Math.max(stage.clientHeight, 1);
  camera.updateProjectionMatrix();
  renderer.setSize(stage.clientWidth, stage.clientHeight);
  labelRenderer.setSize(stage.clientWidth, stage.clientHeight);
  pathMaterial.resolution.set(stage.clientWidth, stage.clientHeight);
});
