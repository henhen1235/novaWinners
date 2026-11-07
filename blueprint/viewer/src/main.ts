import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import type { SceneDoc, ViewerContext } from "./context";
import { buildNav, locate, nearestExitAcross, onMesh, type Leg, type Nav, type Vec2 } from "./nav";
import { buildFloors, type Floors, type LayerKey, type ViewMode, type WallMode } from "./floors";
import { startReplay } from "./replay";
import { startAnalysis } from "./analysis";

const params = new URLSearchParams(location.search);
// An empty ?glb= skips the model and extrudes walls from scene.json (fixtures ship without a display.glb).
const glbUrl = params.get("glb") ?? "/office/display.glb";
const sceneUrl = params.get("scene") ?? "/office/scene.json";
const VIEWS: ViewMode[] = ["single", "side", "stacked", "exploded"];
const WALLS: WallMode[] = ["off", "cutaway", "full"];

const stage = document.querySelector<HTMLDivElement>("#stage")!;
const readout = document.querySelector<HTMLSpanElement>("#readout")!;
const levelSelect = document.querySelector<HTMLSelectElement>("#level")!;
const warningList = document.querySelector<HTMLElement>("#warnings")!;
const buildingName = document.querySelector<HTMLElement>("#building")!;
const titleBlock = document.querySelector<HTMLElement>("#title-block")!;
const spacingInput = document.querySelector<HTMLInputElement>("#spacing")!;

// Colors come from design/tokens.css so the viewer and the replay UI share one palette.
const css = getComputedStyle(document.documentElement);
const token = (name: string) => new THREE.Color(css.getPropertyValue(`--color-${name}`).trim());
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const WALK_SPEED = 1.3;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(stage.clientWidth, stage.clientHeight);
renderer.localClippingEnabled = true; // per-level cutaway planes on display.glb meshes
stage.appendChild(renderer.domElement);
const labelRenderer = new CSS2DRenderer();
labelRenderer.setSize(stage.clientWidth, stage.clientHeight);
// z-index makes a stacking context: CSS2DRenderer sets per-label z-indexes that would otherwise float over panels.
Object.assign(labelRenderer.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none", zIndex: "1" });
stage.appendChild(labelRenderer.domElement);

const scene = new THREE.Scene();
scene.background = token("ground");
const perspective = new THREE.PerspectiveCamera(40, stage.clientWidth / stage.clientHeight, 0.05, 2000);
perspective.position.set(8, 14, 12);
// Side by side is a set of 2D plans: orthographic, top-down, like a drawing sheet.
const orthographic = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 2000);
let camera: THREE.PerspectiveCamera | THREE.OrthographicCamera = perspective;
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(4, 0, 4);
controls.enableDamping = true;
controls.update();

scene.add(new THREE.HemisphereLight(token("text"), token("ground"), 2.2));
const sun = new THREE.DirectionalLight(0xffffff, 3.2);
sun.position.set(-60, 180, 100);
scene.add(sun);

const pathGroup = new THREE.Group();
scene.add(pathGroup);
const grid = new THREE.Group();
scene.add(grid);
// The route draws over walls: it is the one thing the audience must always see.
const pathMaterial = new LineMaterial({ color: token("route").getHex(), linewidth: 4, depthTest: false });
pathMaterial.resolution.set(stage.clientWidth, stage.clientHeight);
let drawStart = 0;

let doc: SceneDoc | null = null;
let nav: Nav | null = null;
let floors: Floors | null = null;
let activeLevel = "";
let view: ViewMode = "single";
let walls: WallMode = WALLS.includes(params.get("walls") as WallMode) ? (params.get("walls") as WallMode) : "cutaway";
let spacing = Math.min(3, Math.max(1, Number(params.get("spacing")) || 2));
let bounds = new THREE.Box3(new THREE.Vector3(-5, 0, -5), new THREE.Vector3(5, 1, 5));
let lastRoute: { legs: Leg[]; meters: number; exit: string; via: string[] } | null = null;
const layoutHooks: ((mode: ViewMode) => void)[] = [];

function frame(x: number, z: number, level = activeLevel) {
  const point = floors ? floors.place(level, x, z) : new THREE.Vector3(x, 0, z);
  const offset = camera.position.clone().sub(controls.target);
  controls.target.copy(point);
  if (camera === perspective) camera.position.copy(point).add(offset.setLength(Math.min(offset.length(), 18)));
  else {
    camera.position.copy(point).add(offset);
    orthographic.zoom = Math.max(orthographic.zoom, 4);
    orthographic.updateProjectionMatrix();
  }
  controls.update();
}

function stageSize() {
  return { width: stage.clientWidth / (splitView ? 2 : 1), height: Math.max(stage.clientHeight, 1) };
}

// Fit the visible floors inside both the vertical and horizontal field of view.
function fitDistance() {
  const size = bounds.getSize(new THREE.Vector3());
  const radius = Math.hypot(size.x, size.z) / 2 + size.y / 2;
  const halfV = THREE.MathUtils.degToRad(perspective.fov / 2);
  const halfH = Math.atan(Math.tan(halfV) * perspective.aspect);
  return (radius / Math.tan(Math.min(halfV, halfH))) * 1.02;
}

function setTopDown() {
  if (camera !== perspective) return;
  const target = bounds.getCenter(new THREE.Vector3()).setY(bounds.min.y);
  controls.target.copy(target);
  camera.position.set(target.x, target.y + fitDistance() * 1.05, target.z + 0.01);
  camera.up.set(0, 0, -1);
  controls.update();
  markView("top");
}

function setOrbit() {
  if (camera !== perspective) return;
  camera.up.set(0, 1, 0);
  const target = bounds.getCenter(new THREE.Vector3());
  controls.target.copy(target);
  // Three-quarter view from the south-west, about 50 degrees above the floor so rooms stay visible.
  const offset = new THREE.Vector3(-0.45, 0.95, 0.75).normalize().multiplyScalar(fitDistance());
  camera.position.copy(target).add(offset);
  controls.update();
  markView("orbit");
}

function fitPlans() {
  const { width, height } = stageSize();
  const size = bounds.getSize(new THREE.Vector3());
  const center = bounds.getCenter(new THREE.Vector3());
  const half = Math.max(size.z / 2, size.x / 2 / (width / height)) * 1.06;
  Object.assign(orthographic, { left: (-half * width) / height, right: (half * width) / height, top: half, bottom: -half, zoom: 1 });
  orthographic.updateProjectionMatrix();
  orthographic.position.set(center.x, 500, center.z + 0.001);
  orthographic.up.set(0, 0, -1);
  controls.target.set(center.x, 0, center.z);
  controls.update();
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
] as [string, LayerKey][]) {
  document.querySelector<HTMLInputElement>(`#${id}`)!.addEventListener("change", (event) => {
    floors?.setLayer(key, (event.target as HTMLInputElement).checked);
    scheduleDeclutter();
  });
}

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
    [data.levels.length > 1 ? "Floors, rooms, exits" : "Rooms, exits", `${data.levels.length > 1 ? `${data.levels.length}, ` : ""}${data.rooms.length}, ${data.exits.length}`],
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

// ---- floors rail: one row per level with its live count; clicking a row focuses that floor ----
const floorRows = new Map<string, HTMLElement>();
function drawFloorList(data: SceneDoc) {
  const list = document.querySelector<HTMLOListElement>("#floor-list")!;
  for (const level of [...data.levels].reverse()) {
    const button = document.createElement("button");
    button.type = "button";
    const count = text("span", "");
    count.className = "fl-count";
    button.append(text("b", level.name ?? level.id), count);
    button.addEventListener("click", () => setLevel(level.id));
    floorRows.set(level.id, count);
    const item = document.createElement("li");
    item.dataset.level = level.id;
    item.append(button);
    list.append(item);
  }
  document.querySelector<HTMLElement>("#floors")!.hidden = false;
}

function setFloorCount(level: string, people: number | null) {
  floors?.setCount(level, people);
  const row = floorRows.get(level);
  const value = people === null ? "" : String(people);
  if (row && row.textContent !== value) row.textContent = value;
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
  activeLevel = data.levels.some((level) => level.id === params.get("level")) ? params.get("level")! : data.levels[0]?.id ?? "";
  levelSelect.value = activeLevel;
  nav = buildNav(data.navmesh);
  floors = buildFloors(data, scene);
  drawTitleBlock(data);
  if (data.levels.length > 1) drawFloorList(data);
  else document.body.classList.add("one-level");
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
      if (!room) return;
      if (view === "single" && room.level !== activeLevel) setLevel(room.level);
      frame(room.centroid[0], room.centroid[1], room.level);
    });
    warningList.appendChild(button);
  }
}

function setLevel(level: string) {
  activeLevel = level;
  levelSelect.value = level;
  applyView(view, view === "single");
}

levelSelect.addEventListener("change", () => setLevel(levelSelect.value));

// ---- views ----
function updateUrl() {
  const url = new URL(location.href);
  url.searchParams.set("view", view);
  url.searchParams.set("walls", walls);
  if (view === "exploded") url.searchParams.set("spacing", String(spacing));
  else url.searchParams.delete("spacing");
  if (doc && doc.levels.length > 1) url.searchParams.set("level", activeLevel);
  history.replaceState(null, "", url);
}

function rebuildGrid() {
  grid.clear();
  const size = bounds.getSize(new THREE.Vector3());
  const center = bounds.getCenter(new THREE.Vector3());
  const span = Math.ceil(Math.max(size.x, size.z) / 5) * 5 + 10;
  const minor = new THREE.GridHelper(span, span, token("line"), token("line"));
  const major = new THREE.GridHelper(span, span / 5, token("line-strong"), token("line-strong"));
  for (const helper of [minor, major]) {
    helper.position.set(Math.round(center.x), bounds.min.y - 0.12, Math.round(center.z));
    (helper.material as THREE.Material).transparent = true;
    (helper.material as THREE.Material).opacity = helper === major ? 0.35 : 0.5;
    helper.raycast = () => {};
    grid.add(helper);
  }
}

function applyView(mode: ViewMode, refit = true) {
  if (!floors) return;
  const changed = mode !== view;
  view = mode;
  const { width, height } = stageSize();
  bounds = floors.layout(mode, activeLevel, spacing, width / height);
  floors.setWalls(walls);
  const plans = mode === "side";
  camera = plans ? orthographic : perspective;
  controls.object = camera;
  controls.enableRotate = !plans;
  controls.mouseButtons.LEFT = plans ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
  if (refit || changed) {
    if (plans) fitPlans();
    else if (mode === "single" && doc && doc.levels.length === 1) setOrbit();
    else setOrbit();
  }
  rebuildGrid();
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-view]")) button.setAttribute("aria-pressed", String(button.dataset.view === mode));
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-walls]")) button.setAttribute("aria-pressed", String(button.dataset.walls === walls));
  for (const id of ["top", "orbit"]) (document.querySelector(`#${id}`) as HTMLButtonElement).disabled = plans;
  for (const row of document.querySelectorAll<HTMLElement>("#floor-list li")) row.classList.toggle("focus", row.dataset.level === activeLevel && mode !== "side");
  document.querySelector<HTMLElement>("#spacing-control")!.hidden = mode !== "exploded";
  document.body.dataset.view = mode;
  if (lastRoute) drawRoute(lastRoute.legs, false);
  for (const hook of layoutHooks) hook(mode);
  updateUrl();
  scheduleDeclutter();
}

for (const button of document.querySelectorAll<HTMLButtonElement>("[data-view]")) {
  button.addEventListener("click", () => applyView(button.dataset.view as ViewMode));
}
for (const button of document.querySelectorAll<HTMLButtonElement>("[data-walls]")) {
  button.addEventListener("click", () => {
    walls = button.dataset.walls as WallMode;
    applyView(view, false);
  });
}
spacingInput.value = String(spacing);
spacingInput.addEventListener("input", () => {
  spacing = Number(spacingInput.value);
  document.querySelector("#spacing-value")!.textContent = `${spacing.toFixed(1)}x`;
  applyView(view, false);
});
document.querySelector("#spacing-value")!.textContent = `${spacing.toFixed(1)}x`;

// ---- labels follow the zoom: only rooms big enough on screen get a name ----
let declutterQueued = false;
function pxPerMeter() {
  const { height } = stageSize();
  if (camera === orthographic) return (height * orthographic.zoom) / (orthographic.top - orthographic.bottom);
  const distance = camera.position.distanceTo(controls.target);
  return height / (2 * Math.tan(THREE.MathUtils.degToRad(perspective.fov / 2)) * Math.max(distance, 0.1));
}
function scheduleDeclutter() {
  if (declutterQueued) return;
  declutterQueued = true;
  setTimeout(() => {
    declutterQueued = false;
    floors?.declutter(pxPerMeter());
  }, 120);
}
controls.addEventListener("change", scheduleDeclutter);

// ---- route on click, across floors through the stairs ----
function shaftLetter(linkId: string) {
  const link = doc?.links?.find((item) => item.id === linkId);
  if (!link || !floors) return "";
  const shaft = floors.shafts.find((item) => {
    const stop = item.stops.get(link.from.level);
    return stop && Math.hypot(stop[0] - link.from.position[0], stop[1] - link.from.position[1]) < 3;
  });
  return shaft ? `Stair ${shaft.letter}` : "the stairs";
}

function routeFrom(point: THREE.Vector3, level: string) {
  if (!doc || !nav) return;
  let from: Vec2 = [point.x, point.z];
  // A click on furniture or a wall top starts from the nearest walkable triangle instead.
  if (!onMesh(nav, from, level, 0.05)) from = nav.centroids[locate(nav, from, level)];
  const best = nearestExitAcross(nav, from, level, doc.exits, (doc.links ?? []).filter((link) => link.type === "stairs"));
  if (!best) {
    readout.textContent = "No walkable route from here to an exit.";
    readout.className = "";
    return;
  }
  const via = [...new Set(best.legs.filter((leg) => leg.via).map((leg) => shaftLetter(leg.via!)))];
  lastRoute = { legs: best.legs, meters: best.meters, exit: best.exit.label, via };
  drawRoute(best.legs, true);
  showRoute(best.meters, best.exit.label, via);
}

function drawRoute(legs: Leg[], animate: boolean) {
  if (!floors) return;
  pathGroup.clear();
  for (const leg of legs) {
    const points = leg.points.map(([x, z]) => floors!.place(leg.level, x, z).setY(floors!.place(leg.level, x, z).y + 0.15));
    const line = new Line2(new LineGeometry().setPositions(points.flatMap((point) => point.toArray())), pathMaterial);
    line.computeLineDistances();
    line.renderOrder = 10;
    line.userData.segments = points.length - 1;
    pathGroup.add(line);
  }
  // In 3D the stair legs join up vertically; in side by side the matching stair badges carry the jump.
  for (let i = 0; i + 1 < legs.length && view !== "side"; i += 1) {
    const a = legs[i].points[legs[i].points.length - 1];
    const b = legs[i + 1].points[0];
    const p = floors.place(legs[i].level, a[0], a[1]);
    const q = floors.place(legs[i + 1].level, b[0], b[1]);
    const line = new Line2(new LineGeometry().setPositions([p.x, p.y + 0.15, p.z, q.x, q.y + 0.15, q.z]), pathMaterial);
    line.userData.segments = 1;
    line.renderOrder = 10;
    pathGroup.add(line);
  }
  const start = legs[0].points[0];
  const origin = new THREE.Mesh(
    new THREE.RingGeometry(0.18, 0.28, 32).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: token("route"), depthTest: false }),
  );
  origin.renderOrder = 11;
  origin.position.copy(floors.place(legs[0].level, start[0], start[1])).setY(origin.position.y + 0.16);
  pathGroup.add(origin);
  if (animate) drawStart = performance.now();
}

function figure(value: string, unit: string) {
  const element = text("span", "");
  element.className = "figure";
  element.append(text("b", value), text("span", unit));
  return element;
}

function showRoute(meters: number, exitLabel: string, via: string[]) {
  const note = text("span", `to ${exitLabel}${via.length ? `, down ${via.join(" and ")}` : ""}, walking at ${WALK_SPEED} m/s`);
  note.className = "route-note";
  readout.replaceChildren(figure(meters.toFixed(1), "m"), figure((meters / WALK_SPEED).toFixed(1), "s"), note);
  readout.className = "has-route";
}

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const pickers: ((raycaster: THREE.Raycaster, side: number) => boolean)[] = [];
const CLICK_SLOP_PX = 5;
let downAt: { x: number; y: number } | null = null;
renderer.domElement.addEventListener("pointerdown", (event) => {
  downAt = { x: event.clientX, y: event.clientY };
});

function shown(object: THREE.Object3D) {
  for (let node: THREE.Object3D | null = object; node; node = node.parent) if (!node.visible) return false;
  return true;
}

// Only a click without a drag picks; orbiting the camera must not redraw the route.
renderer.domElement.addEventListener("pointerup", (event) => {
  if (!downAt || Math.hypot(event.clientX - downAt.x, event.clientY - downAt.y) > CLICK_SLOP_PX) return;
  downAt = null;
  if (!floors) return;
  const rect = renderer.domElement.getBoundingClientRect();
  const halves = splitView ? 2 : 1;
  const width = rect.width / halves;
  const side = Math.min(halves - 1, Math.floor((event.clientX - rect.left) / width));
  pointer.x = ((event.clientX - rect.left - side * width) / width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  if (pickers.some((pick) => pick(raycaster, side))) return;
  const targets = floors.levels.filter((level) => level.root.visible && !level.ghost).flatMap((level) => [level.layers.model, level.layers.navmesh]);
  const hit = raycaster.intersectObjects(targets, true).find((item) => shown(item.object));
  if (!hit) return;
  const level = floors.levelOf(hit.object);
  if (!level) return;
  routeFrom(hit.point.clone().sub(level.root.position), level.id);
});

// ---- loading ----
const sceneLoaded = fetch(sceneUrl)
  .then((response) => {
    if (!response.ok) throw new Error(`${response.status}`);
    return response.json();
  })
  .then((data: SceneDoc) => {
    drawScene(data);
    return data;
  });
sceneLoaded.catch(() => {
  readout.textContent = `Could not load ${sceneUrl}. Pass ?scene=/path/scene.json and check the file is in public/.`;
});

const modelLoaded: Promise<THREE.Object3D | null> = glbUrl
  ? new GLTFLoader()
      .loadAsync(glbUrl)
      .then((gltf) => gltf.scene)
      .catch(() => {
        readout.textContent = `Could not load ${glbUrl}; showing walls from scene.json.`;
        return null;
      })
  : Promise.resolve(null);

const ready = Promise.all([sceneLoaded, modelLoaded]).then(([data, model]) => {
  floors!.attachModel(model);
  const requested = params.get("view") as ViewMode;
  const multi = data.levels.length > 1;
  // Watching a multi-floor run reads best as one plan per floor; a building on its own reads best stacked.
  const initial = multi ? (VIEWS.includes(requested) ? requested : params.get("replay") ? "side" : "stacked") : "single";
  view = initial;
  applyView(initial);
  if (!readout.textContent?.startsWith("Could not")) readout.textContent = "Click any floor to draw the walking route to the nearest exit.";
  return data;
});
ready.catch((error) => {
  readout.textContent = `Could not build the scene (${error instanceof Error ? error.message : error}).`;
});

// ---- render loop, split view for ?compare= ----
const frameHooks: ((now: number) => void)[] = [];
// Split view (?compare=): one camera, two viewports; the right half gets its own label layer.
let splitView: { left: THREE.Object3D; right: THREE.Object3D } | null = null;
const rightLabelScene = new THREE.Scene();
const rightLabelRenderer = new CSS2DRenderer();
const mirrorRoot = new THREE.Group();
rightLabelScene.add(mirrorRoot);
const mirrors = new Map<CSS2DObject, CSS2DObject>();

// Static labels (rooms, exits, captions, analysis) are cloned into the right half each frame they change.
function syncMirrors(left: THREE.Object3D) {
  const seen = new Set<CSS2DObject>();
  scene.traverseVisible((object) => {
    if (!(object instanceof CSS2DObject)) return;
    for (let node: THREE.Object3D | null = object; node; node = node.parent) if (node === left) return;
    seen.add(object);
    let copy = mirrors.get(object);
    if (!copy) {
      copy = new CSS2DObject(object.element.cloneNode(true) as HTMLElement);
      copy.center.copy(object.center);
      mirrors.set(object, copy);
      mirrorRoot.add(copy);
    }
    object.getWorldPosition(copy.position);
  });
  for (const [source, copy] of mirrors) {
    if (seen.has(source)) continue;
    copy.removeFromParent();
    mirrors.delete(source);
  }
}

function resize() {
  const { width, height } = stageSize();
  perspective.aspect = width / height;
  perspective.updateProjectionMatrix();
  renderer.setSize(stage.clientWidth, stage.clientHeight);
  labelRenderer.setSize(width, height);
  rightLabelRenderer.setSize(width, height);
  pathMaterial.resolution.set(width, height);
  if (floors) applyView(view, view === "side");
}

function renderSplit(split: { left: THREE.Object3D; right: THREE.Object3D }) {
  const { width, height } = stageSize();
  renderer.setScissorTest(true);
  for (const side of [0, 1]) {
    split.left.visible = side === 0;
    split.right.visible = side === 1;
    renderer.setViewport(side * width, 0, width, height);
    renderer.setScissor(side * width, 0, width, height);
    renderer.render(scene, camera);
  }
  renderer.setScissorTest(false);
  split.left.visible = true;
  syncMirrors(split.left);
  labelRenderer.render(scene, camera);
  rightLabelRenderer.render(rightLabelScene, camera);
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  const now = performance.now();
  for (const hook of frameHooks) hook(now);
  // The route draws itself from the click point to the exit, one segment at a time.
  const progress = reduceMotion ? 1 : Math.min((now - drawStart) / 700, 1);
  for (const child of pathGroup.children) {
    if (child instanceof Line2) child.geometry.instanceCount = Math.ceil(child.userData.segments * (1 - (1 - progress) ** 3));
  }
  // Exits pulse slowly so they can be found from across the room.
  const phase = reduceMotion ? 0.5 : (now % 2400) / 2400;
  for (const pulse of floors?.pulses ?? []) {
    pulse.scale.setScalar(1 + phase * 1.2);
    (pulse.material as THREE.MeshBasicMaterial).opacity = 0.7 * (1 - phase);
  }
  if (splitView) renderSplit(splitView);
  else {
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  }
}
animate();

window.addEventListener("resize", resize);

// Replay (?replay=, ?compare=) and analysis (?analysis=) build on the loaded scene.
ready.then((data) => {
  const context: ViewerContext = {
    scene,
    camera: perspective,
    doc: data,
    nav: nav!,
    level: () => activeLevel,
    elevation: () => data.levels.find((level) => level.id === activeLevel)?.elevation_m ?? 0,
    token,
    label,
    onFrame: (fn) => frameHooks.push(fn),
    onPick: (fn) => pickers.push(fn),
    addLayer: (_key, group) => {
      if (!group.parent) scene.add(group);
    },
    split: (left, right, rightLabels) => {
      splitView = { left, right };
      rightLabelScene.add(rightLabels);
      Object.assign(rightLabelRenderer.domElement.style, { position: "absolute", top: "0", right: "0", pointerEvents: "none", zIndex: "1" });
      Object.assign(labelRenderer.domElement.style, { inset: "0 auto auto 0" });
      stage.appendChild(rightLabelRenderer.domElement);
      stage.classList.add("split");
      resize();
      applyView(view);
    },
    focus: (x, z) => frame(x, z),
    place: (level, x, z, out) => floors!.place(level, x, z, out),
    levelRoot: (level) => (floors!.byId.get(level) ?? floors!.levels[0]).root,
    view: () => view,
    onLayout: (fn) => layoutHooks.push(fn),
    pxPerMeter,
    setFloorCount,
  };
  startAnalysis(context, params.get("analysis"));
  startReplay(context, params.get("replay"), params.get("compare"));
});
