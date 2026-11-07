import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

type SceneDoc = {
  levels: { id: string; name?: string; elevation_m: number }[];
  rooms: { id: string; level: string; name: string; polygon: number[][]; centroid: number[] }[];
  doors: { id: string; level: string; segment: number[][]; is_exit: boolean }[];
  exits: { id: string; level: string; position: number[]; label: string }[];
  warnings: { code: string; message: string; severity: string; ref?: string }[];
  navmesh: { vertices: number[][]; triangles: number[][]; triangle_level: string[] };
};

type FloorLayout = {
  id: string;
  name: string;
  elevation: number;
  shiftX: number;
  shiftZ: number;
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
};

const params = new URLSearchParams(location.search);
const glbUrl = params.get("glb") ?? "/office/display.glb";
const sceneUrl = params.get("scene") ?? "/office/scene.json";

const stage = document.querySelector<HTMLDivElement>("#stage")!;
const readout = document.querySelector<HTMLSpanElement>("#readout")!;
const levelSelect = document.querySelector<HTMLSelectElement>("#level")!;
const warningList = document.querySelector<HTMLElement>("#warnings")!;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(stage.clientWidth, stage.clientHeight);
stage.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x121212);
const camera = new THREE.PerspectiveCamera(50, stage.clientWidth / stage.clientHeight, 0.05, 500);
camera.position.set(8, 14, 12);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(4, 0, 4);
controls.update();

scene.add(new THREE.AmbientLight(0xffffff, 0.7));
const sun = new THREE.DirectionalLight(0xffffff, 1.1);
sun.position.set(8, 18, 6);
scene.add(sun);

const groups: Record<string, THREE.Object3D> = {
  navmesh: new THREE.Group(),
  rooms: new THREE.Group(),
  doors: new THREE.Group(),
  exits: new THREE.Group(),
  warnings: new THREE.Group(),
  path: new THREE.Group(),
  labels: new THREE.Group(),
};
for (const group of Object.values(groups)) scene.add(group);

const modelRoot = new THREE.Group();
scene.add(modelRoot);

let doc: SceneDoc | null = null;
let activeLevel = "";
let floors: FloorLayout[] = [];
let loadedModel: THREE.Object3D | null = null;

function frame(x: number, z: number, elevation = 0) {
  controls.target.set(x, elevation, z);
  camera.position.set(x + 6, elevation + 10, z + 8);
  controls.update();
}

function setTopDown() {
  const box = new THREE.Box3().setFromObject(modelRoot);
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const span = Math.max(size.x, size.z, 8);
  camera.up.set(0, 0, -1);
  camera.far = Math.max(800, span * 8);
  camera.updateProjectionMatrix();
  controls.target.set(center.x, 0, center.z);
  camera.position.set(center.x, span * 1.2, center.z + 0.01);
  controls.update();
}

function setOrbit() {
  camera.up.set(0, 1, 0);
  const target = controls.target.clone();
  camera.position.set(target.x + 8, target.y + 12, target.z + 10);
  controls.update();
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

function lineLoop(points: number[][], color: number, y: number) {
  const vectors = points.map((point) => new THREE.Vector3(point[0], y, point[1]));
  if (vectors.length) vectors.push(vectors[0].clone());
  const geometry = new THREE.BufferGeometry().setFromPoints(vectors);
  return new THREE.Line(geometry, new THREE.LineBasicMaterial({ color }));
}

function layoutFloors(data: SceneDoc): FloorLayout[] {
  const sorted = [...data.levels].sort((a, b) => a.elevation_m - b.elevation_m);
  let cursor = 0;
  const gap = 18;
  return sorted.map((level) => {
    const xs: number[] = [];
    const zs: number[] = [];
    for (const room of data.rooms) {
      if (room.level !== level.id) continue;
      for (const point of room.polygon) {
        xs.push(point[0]);
        zs.push(point[1]);
      }
    }
    const minX = xs.length ? Math.min(...xs) : 0;
    const maxX = xs.length ? Math.max(...xs) : minX + 10;
    const minZ = zs.length ? Math.min(...zs) : 0;
    const maxZ = zs.length ? Math.max(...zs) : minZ + 10;
    const laid = {
      id: level.id,
      name: level.name || level.id,
      elevation: level.elevation_m,
      shiftX: cursor - minX,
      shiftZ: -minZ,
      minX,
      maxX,
      minZ,
      maxZ,
    };
    cursor += maxX - minX + gap;
    return laid;
  });
}

function floorById(levelId: string) {
  return floors.find((floor) => floor.id === levelId);
}

function floorAtHeight(y: number) {
  let chosen: FloorLayout | undefined;
  for (const floor of floors) {
    if (floor.elevation <= y + 0.05) chosen = floor;
  }
  return chosen;
}

function displayX(levelId: string, x: number) {
  return x + (floorById(levelId)?.shiftX ?? 0);
}

function displayY(levelId: string, y: number) {
  const floor = floorById(levelId);
  return y - (floor?.elevation ?? 0);
}

function displayZ(levelId: string, z: number) {
  return z + (floorById(levelId)?.shiftZ ?? 0);
}

function roomPhrase(data: SceneDoc, roomId: string) {
  const room = data.rooms.find((item) => item.id === roomId);
  if (!room) return roomId;
  const floor = data.levels.find((level) => level.id === room.level);
  return `${floor?.name || floor?.id || room.level} · ${room.name}`;
}

function plainWarning(data: SceneDoc, warning: SceneDoc["warnings"][number]) {
  if (warning.code === "ROOM_UNREACHABLE") {
    const roomId = warning.ref || warning.message.match(/r_[A-Za-z0-9_]+/)?.[0];
    const place = roomId ? roomPhrase(data, roomId) : "A room";
    return `${place} has no walking path to an exit. Someone starting there cannot get out.`;
  }
  if (warning.code === "NAVMESH_INVALID") {
    return "One patch of walkable floor never touches an exit, so the path line cannot cross it.";
  }
  if (warning.code === "LEAKY_WALL_SUSPECTED") {
    const ids = warning.message.match(/r_[A-Za-z0-9_]+/g) ?? [];
    const pair = ids.map((id) => roomPhrase(data, id)).join(" and ");
    return `${pair || "Two rooms"} are open to each other with no door. A wall between them is probably missing.`;
  }
  if (warning.code === "NO_EXITS") return "This plan has no exits, so there is nowhere to evacuate to.";
  if (warning.code === "EXIT_UNREACHABLE") return "An exit is marked, but the walkable floor does not reach it.";
  return warning.message;
}

function floorLabel(text: string) {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 96;
  const context = canvas.getContext("2d");
  if (!context) return new THREE.Sprite();
  context.fillStyle = "rgba(0,0,0,0.6)";
  context.fillRect(0, 0, 512, 96);
  context.fillStyle = "#ffffff";
  context.font = "bold 56px sans-serif";
  context.fillText(text, 16, 66);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthTest: false }));
  sprite.scale.set(14, 2.6, 1);
  return sprite;
}

function spreadModel(root: THREE.Object3D) {
  if (root.userData.spread) return;
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh || !mesh.geometry) return;
    if (!mesh.geometry.getAttribute("position")) return;
    const geometry = mesh.geometry.clone();
    const position = geometry.getAttribute("position");
    for (let index = 0; index < position.count; index += 1) {
      const floor = floorAtHeight(position.getY(index));
      if (!floor) continue;
      position.setXYZ(
        index,
        position.getX(index) + floor.shiftX,
        position.getY(index) - floor.elevation,
        position.getZ(index) + floor.shiftZ,
      );
    }
    position.needsUpdate = true;
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    mesh.geometry = geometry;
  });
  root.userData.spread = true;
}

function frameFloor(floor: FloorLayout) {
  const width = Math.max(floor.maxX - floor.minX, 8);
  const depth = Math.max(floor.maxZ - floor.minZ, 8);
  const centerX = (floor.minX + floor.maxX) / 2 + floor.shiftX;
  const centerZ = (floor.minZ + floor.maxZ) / 2 + floor.shiftZ;
  const aspect = camera.aspect || 1.6;
  const verticalFov = (camera.fov * Math.PI) / 180;
  const distance = (Math.max(depth / 2, width / 2 / aspect) / Math.tan(verticalFov / 2)) * 1.35;
  camera.far = Math.max(4000, distance * 8);
  camera.updateProjectionMatrix();
  controls.target.set(centerX, 0, centerZ);
  camera.position.set(centerX, distance, centerZ + depth * 0.12);
  controls.update();
}

function frameEverything() {
  if (floors.length > 0) {
    const floor = floors[Math.min(1, floors.length - 1)];
    frameFloor(floor);
    return;
  }
  const box = new THREE.Box3().setFromObject(modelRoot);
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const span = Math.max(size.z, 8);
  camera.far = Math.max(4000, span * 8);
  camera.updateProjectionMatrix();
  controls.target.set(center.x, 0, center.z);
  camera.position.set(center.x, span * 1.6, center.z + span * 0.2);
  controls.update();
}

function present() {
  if (!doc || !loadedModel) return;
  floors = layoutFloors(doc);
  spreadModel(loadedModel);
  clear(groups.labels);
  for (const floor of floors) {
    const label = floorLabel(floor.name);
    label.position.set(floor.minX + floor.shiftX + 6, 4.2, 2);
    groups.labels.add(label);
  }
  rebuildOverlays();
  frameEverything();
}

function drawScene(data: SceneDoc) {
  doc = data;
  floors = layoutFloors(data);
  levelSelect.innerHTML = "";
  for (const level of [...data.levels].sort((a, b) => a.elevation_m - b.elevation_m)) {
    const option = document.createElement("option");
    option.value = level.id;
    option.textContent = level.name || level.id;
    levelSelect.appendChild(option);
  }
  activeLevel = data.levels[0]?.id ?? "";
  warningList.innerHTML = "";
  const summary = document.createElement("p");
  const unreachable = data.warnings.filter((warning) => warning.code === "ROOM_UNREACHABLE").length;
  const leaks = data.warnings.filter((warning) => warning.code === "LEAKY_WALL_SUSPECTED").length;
  const brokenMesh = data.warnings.some((warning) => warning.code === "NAVMESH_INVALID");
  const sentences = ["These are checks on the plan. The model still loaded."];
  if (unreachable) sentences.push(`${unreachable} rooms have no route to an exit.`);
  if (leaks) sentences.push(`${leaks} room pairs are open with no door, so a wall is probably missing.`);
  if (brokenMesh) sentences.push("One walkable patch never touches an exit, so the path line stops there.");
  if (data.warnings.length === 0) sentences.push("No problems were reported.");
  summary.textContent = sentences.join(" ");
  warningList.appendChild(summary);
  const ordered = [...data.warnings].sort((a, b) => Number(b.severity === "error") - Number(a.severity === "error"));
  for (const warning of ordered) {
    const button = document.createElement("button");
    button.className = warning.severity === "error" ? "error" : "warn";
    button.textContent = plainWarning(data, warning);
    button.addEventListener("click", () => {
      const roomId = warning.ref || warning.message.match(/r_[A-Za-z0-9_]+/)?.[0];
      const room = data.rooms.find((item) => item.id === roomId);
      if (!room) return;
      activeLevel = room.level;
      levelSelect.value = room.level;
      frame(displayX(room.level, room.centroid[0]), displayZ(room.level, room.centroid[1]), 0.5);
    });
    warningList.appendChild(button);
  }
  present();
}

function rebuildOverlays() {
  if (!doc) return;
  clear(groups.rooms);
  clear(groups.doors);
  clear(groups.exits);
  clear(groups.navmesh);
  for (const room of doc.rooms) {
    const shifted = room.polygon.map((point) => [displayX(room.level, point[0]), displayZ(room.level, point[1])]);
    groups.rooms.add(lineLoop(shifted, room.level === activeLevel ? 0x6aa6ff : 0x335588, displayY(room.level, 0.06)));
  }
  for (const door of doc.doors) {
    const shifted = door.segment.map((point) => [displayX(door.level, point[0]), displayZ(door.level, point[1])]);
    groups.doors.add(lineLoop(shifted, door.is_exit ? 0x3ddc78 : 0xff6655, displayY(door.level, 0.08)));
  }
  for (const exit of doc.exits) {
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(0.18, 12, 8),
      new THREE.MeshBasicMaterial({ color: 0x3ddc78 }),
    );
    marker.position.set(displayX(exit.level, exit.position[0]), displayY(exit.level, 0.2), displayZ(exit.level, exit.position[1]));
    groups.exits.add(marker);
  }
  const vertices = doc.navmesh.vertices;
  const positions: number[] = [];
  doc.navmesh.triangles.forEach((triangle, index) => {
    const level = doc!.navmesh.triangle_level[index];
    for (const corner of triangle) {
      const vertex = vertices[corner];
      positions.push(displayX(level, vertex[0]), displayY(level, vertex[1] + 0.04), displayZ(level, vertex[2]));
    }
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  groups.navmesh.add(new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0x3d7ec9, transparent: true, opacity: 0.35, side: THREE.DoubleSide })));
}

levelSelect.addEventListener("change", () => {
  activeLevel = levelSelect.value;
  rebuildOverlays();
  const floor = floorById(activeLevel);
  if (floor) frameFloor(floor);
});

function planFromDisplay(point: THREE.Vector3) {
  for (const floor of floors) {
    const left = floor.minX + floor.shiftX - 1;
    const right = floor.maxX + floor.shiftX + 1;
    if (point.x >= left && point.x <= right) {
      return { level: floor.id, x: point.x - floor.shiftX, z: point.z - floor.shiftZ };
    }
  }
  return null;
}

function pathOnMesh(startX: number, startZ: number, levelId: string) {
  if (!doc) return;
  activeLevel = levelId;
  levelSelect.value = levelId;
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
    if (levels[index] !== levelId) return;
    const distance = Math.hypot(centroid.x - startX, centroid.z - startZ);
    if (distance < best) {
      best = distance;
      startTri = index;
    }
  });
  const exits = doc.exits.filter((exit) => exit.level === levelId);
  let goal = -1;
  let goalDistance = Infinity;
  exits.forEach((exit) => {
    centroids.forEach((centroid, index) => {
      if (levels[index] !== levelId) return;
      const distance = Math.hypot(centroid.x - exit.position[0], centroid.z - exit.position[1]);
      if (distance < goalDistance) {
        goalDistance = distance;
        goal = index;
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
    readout.textContent = "No path";
    return;
  }
  const nodes = [goal];
  while (nodes[0] !== startTri) {
    const prior = previous.get(nodes[0]);
    if (prior === undefined) break;
    nodes.unshift(prior);
  }
  const points = [new THREE.Vector3(displayX(levelId, startX), displayY(levelId, centroids[startTri].y + 0.15), displayZ(levelId, startZ))];
  for (const node of nodes) {
    const centroid = centroids[node];
    points.push(new THREE.Vector3(displayX(levelId, centroid.x), displayY(levelId, centroid.y + 0.15), displayZ(levelId, centroid.z)));
  }
  let meters = 0;
  for (let index = 1; index < points.length; index += 1) {
    meters += Math.hypot(points[index].x - points[index - 1].x, points[index].z - points[index - 1].z);
  }
  clear(groups.path);
  groups.path.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({ color: 0xd7ff4a })));
  readout.textContent = `${meters.toFixed(2)} m, ${(meters / 1.3).toFixed(1)} s`;
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
  const plan = planFromDisplay(hits[0].point);
  if (!plan) return;
  pathOnMesh(plan.x, plan.z, plan.level);
});

const loader = new GLTFLoader();
let loadToken = 0;

type BuildingTab = {
  name: string;
  slug: string;
  gloss: string;
  floors: string[];
  sheets: { page: number; title: string; floors: string[] }[];
  scene_url: string | null;
  glb_url: string | null;
  status?: string;
};

let buildings: BuildingTab[] = [];
let selectedSlug = "";
const buildingNav = document.querySelector<HTMLElement>("#buildings")!;

function buildingLabel(building: BuildingTab) {
  return building.gloss ? `${building.name} · ${building.gloss}` : building.name;
}

function wipeView() {
  while (modelRoot.children.length) modelRoot.remove(modelRoot.children[0]);
  loadedModel = null;
  doc = null;
  floors = [];
  for (const group of Object.values(groups)) clear(group);
  levelSelect.innerHTML = "";
  warningList.innerHTML = "";
}

function renderBuildingTabs() {
  buildingNav.innerHTML = "";
  for (const building of buildings) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "tab");
    button.dataset.slug = building.slug;
    button.setAttribute("aria-selected", building.slug === selectedSlug ? "true" : "false");
    button.className = building.scene_url ? "ready" : "unread";
    button.textContent = buildingLabel(building);
    const count = building.floors?.length ? `${building.floors.length} floors` : "floor plans";
    button.title = building.scene_url ? count : `${count}. Not read yet`;
    button.addEventListener("click", () => selectBuilding(building));
    buildingNav.appendChild(button);
  }
  buildingNav.querySelector<HTMLButtonElement>('[aria-selected="true"]')?.scrollIntoView({ inline: "nearest", block: "nearest" });
}

function selectBuilding(building: BuildingTab) {
  selectedSlug = building.slug;
  renderBuildingTabs();
  const token = ++loadToken;
  wipeView();
  if (!building.glb_url || !building.scene_url) {
    const message = `${buildingLabel(building)} has not been read yet. The model account has no credit, so this building was not generated.`;
    readout.textContent = message;
    const note = document.createElement("p");
    note.textContent = message;
    warningList.appendChild(note);
    return;
  }
  readout.textContent = `Loading ${buildingLabel(building)}…`;
  showModel(building.glb_url, token);
  showScene(building.scene_url, token);
}

function showModel(url: string, token = ++loadToken) {
  loader.load(
    url,
    (gltf) => {
      if (token !== loadToken) return;
      while (modelRoot.children.length) modelRoot.remove(modelRoot.children[0]);
      clear(groups.path);
      loadedModel = gltf.scene;
      modelRoot.add(gltf.scene);
      present();
      readout.textContent = "Floors are side by side. Their real heights stay in the scene file. Click a floor to probe a path.";
    },
    undefined,
    () => {
      if (token === loadToken) readout.textContent = "Could not load the 3D model";
    },
  );
}

function showScene(url: string, token = loadToken) {
  fetch(url)
    .then((response) => {
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    })
    .then((data: SceneDoc) => {
      if (token !== loadToken) return;
      drawScene(data);
    })
    .catch(() => {
      if (token === loadToken) readout.textContent = "scene.json was not found next to the viewer";
    });
}

document.querySelector<HTMLInputElement>("#blueprint")!.addEventListener("change", async (event) => {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  readout.textContent = "Reading the blueprint… this can take a few minutes for a multi-floor set.";
  input.disabled = true;
  const body = new FormData();
  body.append("file", file);
  try {
    const response = await fetch("/api/generate", { method: "POST", body });
    const result = await response.json();
    if (!response.ok) {
      readout.textContent = result.detail ?? "The plan could not be generated";
      return;
    }
    const building = result.building ? `${result.building}, ` : "";
    const floors = Number.isFinite(result.floors) ? `${result.floors} floors, ` : "";
    const problem = result.exit_code === 0 ? "" : " Validator reported an error.";
    const via = result.note ? ` ${result.note}` : "";
    readout.textContent = `${building}${floors}${result.rooms} rooms, ${result.exits} exits.${problem}${via} Click the floor for a path.`;
    if (Array.isArray(result.buildings)) {
      buildings = result.buildings;
      const match = buildings.find((item) => item.name === result.building);
      selectedSlug = match?.slug ?? "";
      renderBuildingTabs();
    }
    showModel(result.glb_url);
    showScene(result.scene_url);
  } catch {
    readout.textContent = "The local generator is not running. Start it with: python -m editor.app";
  } finally {
    input.disabled = false;
  }
});

async function bootBuildings() {
  if (params.has("glb") || params.has("scene")) {
    showModel(glbUrl);
    showScene(sceneUrl);
    return;
  }
  try {
    const response = await fetch("/api/buildings");
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    buildings = Array.isArray(data.buildings) ? data.buildings : [];
    renderBuildingTabs();
    const city = buildings.find((item) => item.slug === "city-hotel" || item.name === "シティホテル");
    const linked = buildings.find((item) => item.scene_url && (item.scene_url === sceneUrl || item.glb_url === glbUrl));
    const chosen = city ?? linked ?? buildings.find((item) => item.scene_url) ?? buildings[0];
    if (chosen) {
      selectBuilding(chosen);
      return;
    }
  } catch {
    // The catalog is optional. The URL still points at a model.
  }
  showModel(glbUrl);
  showScene(sceneUrl);
}

bootBuildings();

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}
animate();

window.addEventListener("resize", () => {
  camera.aspect = stage.clientWidth / Math.max(stage.clientHeight, 1);
  camera.updateProjectionMatrix();
  renderer.setSize(stage.clientWidth, stage.clientHeight);
});
