import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

type SceneDoc = {
  levels: { id: string; elevation_m: number }[];
  rooms: { id: string; level: string; name: string; polygon: number[][]; centroid: number[] }[];
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
};
for (const group of Object.values(groups)) scene.add(group);

const modelRoot = new THREE.Group();
scene.add(modelRoot);

let doc: SceneDoc | null = null;
let activeLevel = "";

function frame(x: number, z: number, elevation = 0) {
  controls.target.set(x, elevation, z);
  camera.position.set(x + 6, elevation + 10, z + 8);
  controls.update();
}

function setTopDown() {
  const target = controls.target.clone();
  camera.position.set(target.x, target.y + 24, target.z + 0.01);
  camera.up.set(0, 0, -1);
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

function drawScene(data: SceneDoc) {
  doc = data;
  levelSelect.innerHTML = "";
  for (const level of data.levels) {
    const option = document.createElement("option");
    option.value = level.id;
    option.textContent = level.id;
    levelSelect.appendChild(option);
  }
  activeLevel = data.levels[0]?.id ?? "";
  rebuildOverlays();
  warningList.innerHTML = "";
  for (const warning of data.warnings) {
    const button = document.createElement("button");
    button.textContent = `${warning.code}: ${warning.message}`;
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
    groups.rooms.add(lineLoop(room.polygon, 0x6aa6ff, elevation + 0.06));
  }
  for (const door of doc.doors.filter((item) => item.level === activeLevel)) {
    groups.doors.add(lineLoop(door.segment, door.is_exit ? 0x3ddc78 : 0xff6655, elevation + 0.08));
  }
  for (const exit of doc.exits.filter((item) => item.level === activeLevel)) {
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(0.18, 12, 8),
      new THREE.MeshBasicMaterial({ color: 0x3ddc78 }),
    );
    marker.position.set(exit.position[0], elevation + 0.2, exit.position[1]);
    groups.exits.add(marker);
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
  groups.navmesh.add(new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0x3d7ec9, transparent: true, opacity: 0.35, side: THREE.DoubleSide })));
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
  exits.forEach((exit) => {
    centroids.forEach((centroid, index) => {
      if (levels[index] !== activeLevel) return;
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
  const points = [new THREE.Vector3(start.x, centroids[startTri].y + 0.15, start.z)];
  for (const node of nodes) points.push(centroids[node].clone().setY(centroids[node].y + 0.15));
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
  pathOnMesh(hits[0].point);
});

new GLTFLoader().load(glbUrl, (gltf) => {
  modelRoot.add(gltf.scene);
  const box = new THREE.Box3().setFromObject(gltf.scene);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  controls.target.copy(center);
  camera.position.set(center.x + size.x, center.y + Math.max(size.y, 8), center.z + size.z);
  controls.update();
  readout.textContent = "Click the floor to probe a path to an exit";
});

fetch(sceneUrl)
  .then((response) => response.json())
  .then((data: SceneDoc) => drawScene(data))
  .catch(() => {
    readout.textContent = "scene.json was not found next to the viewer";
  });

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
