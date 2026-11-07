// Occupant rendering with three levels of detail, the same language in 2D plans and 3D stacks:
//   far  - small dots plus a crowd-density heat layer per floor (crowding without clutter);
//   mid  - chevrons pointing along the walking heading, filled by persona, ringed by state;
//   near - low-poly figures with persona cues (staff vest, wheelchair, child, fruit fly),
//          a fear ring and a walk bob.
// Every shape is one InstancedMesh for all floors, so the cost is a fixed handful of draw calls.
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { ViewerContext } from "./context";

export type Lod = "far" | "mid" | "near";
// Screen pixels per meter where the representation changes (a person is ~0.5 m wide).
const MID_PX_PER_M = 6;
const NEAR_PX_PER_M = 22;
const DOT_PX = 4;
const CHEVRON_PX = 13;
const HEAT_CELL_M = 2;
const HEAT_PERIOD_MS = 250;
// 5x5 Gaussian (sigma = 1 cell), normalized so one person adds one person in total.
const KERNEL = (() => {
  const raw = Array.from({ length: 25 }, (_, i) => Math.exp(-(((i % 5) - 2) ** 2 + (Math.floor(i / 5) - 2) ** 2) / 2));
  const sum = raw.reduce((a, b) => a + b, 0);
  return raw.map((value) => value / sum);
})();
const VEST = new THREE.Color("#FF5FD2"); // staff: not exit green, hazard red or warning amber
const FLY_SCALE = 3; // a 1 cm fly drawn the size of a hand so it can be found

export function lodFor(pxPerMeter: number): Lod {
  return pxPerMeter < MID_PX_PER_M ? "far" : pxPerMeter < NEAR_PX_PER_M ? "mid" : "near";
}

function flat(geometry: THREE.BufferGeometry) {
  const g = geometry.index ? geometry.toNonIndexed() : geometry;
  for (const name of Object.keys(g.attributes)) if (name !== "position" && name !== "normal") g.deleteAttribute(name);
  return g;
}

function merge(parts: THREE.BufferGeometry[]) {
  return mergeGeometries(parts.map(flat), false)!;
}

function bodyGeometry() {
  return merge([
    new THREE.BoxGeometry(0.13, 0.8, 0.13).translate(0, 0.4, 0.09),
    new THREE.BoxGeometry(0.13, 0.8, 0.13).translate(0, 0.4, -0.09),
    new THREE.CylinderGeometry(0.16, 0.2, 0.62, 6).translate(0, 1.12, 0),
    new THREE.BoxGeometry(0.1, 0.55, 0.1).translate(0.02, 1.08, 0.25),
    new THREE.BoxGeometry(0.1, 0.55, 0.1).translate(0.02, 1.08, -0.25),
    new THREE.IcosahedronGeometry(0.13, 0).translate(0.02, 1.6, 0),
    new THREE.BoxGeometry(0.12, 0.05, 0.12).translate(0.12, 1.58, 0), // nose: shows which way they face
  ]);
}

function wheelchairGeometry() {
  return merge([
    new THREE.TorusGeometry(0.3, 0.035, 5, 14).translate(0, 0.3, 0.3),
    new THREE.TorusGeometry(0.3, 0.035, 5, 14).translate(0, 0.3, -0.3),
    new THREE.BoxGeometry(0.46, 0.06, 0.5).translate(0, 0.52, 0),
    new THREE.BoxGeometry(0.06, 0.5, 0.5).translate(-0.24, 0.8, 0),
  ]);
}

function flyGeometry() {
  return merge([
    new THREE.SphereGeometry(0.06, 8, 6).scale(1.8, 0.8, 0.8).translate(0, 0.9, 0),
    new THREE.SphereGeometry(0.035, 6, 4).translate(0.12, 0.92, 0), // head
    new THREE.CircleGeometry(0.09, 10).rotateX(-Math.PI / 2).scale(1.4, 1, 0.7).translate(-0.04, 0.96, 0.09),
    new THREE.CircleGeometry(0.09, 10).rotateX(-Math.PI / 2).scale(1.4, 1, 0.7).translate(-0.04, 0.96, -0.09),
  ]);
}

function chevronGeometry() {
  const shape = new THREE.Shape([
    new THREE.Vector2(0.6, 0), new THREE.Vector2(-0.45, 0.45), new THREE.Vector2(-0.18, 0), new THREE.Vector2(-0.45, -0.45),
  ]);
  return new THREE.ShapeGeometry(shape).rotateX(-Math.PI / 2).translate(0, 0.06, 0);
}

// Hatched ring for people waiting at a refuge: the dashes read as "held", not "moving".
function dashedRing() {
  const parts: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 8; i += 1) parts.push(new THREE.RingGeometry(0.75, 0.95, 3, 1, (i * Math.PI) / 4, Math.PI / 7));
  return merge(parts).rotateX(-Math.PI / 2).translate(0, 0.05, 0);
}

type Heat = { level: string; minX: number; minZ: number; w: number; h: number; counts: Float32Array; texture: THREE.DataTexture; mesh: THREE.Mesh };

export interface AgentSample {
  level: string;
  x: number; // scene coordinates on that level
  z: number;
  world: THREE.Vector3;
  heading: number; // radians about Y; 0 faces +x
  state: string;
  moving: boolean;
  fear: number; // 0..1, NaN when unknown
  fill: THREE.Color;
  ring: THREE.Color;
  phase: number; // per-agent walk phase
}

export class People {
  readonly root = new THREE.Group();
  readonly worldPositions: Float32Array; // NaN when the agent is not drawn
  lod: Lod = "mid";
  private readonly count: number;
  private readonly personas: string[];
  private readonly meshes: Record<string, THREE.InstancedMesh> = {};
  private readonly materials: THREE.Material[] = [];
  private readonly heats = new Map<string, Heat>();
  private readonly links: THREE.LineSegments;
  private readonly linkPositions: number[] = [];
  private heatAt = 0;
  private readonly m = new THREE.Matrix4();
  private readonly q = new THREE.Quaternion();
  private readonly s = new THREE.Vector3();
  private readonly p = new THREE.Vector3();
  private readonly c = new THREE.Color();
  private readonly hidden = new THREE.Matrix4().makeScale(0, 0, 0);
  private readonly up = new THREE.Vector3(0, 1, 0);
  private readonly hazard: THREE.Color;
  private readonly calm: THREE.Color;
  // The caller reuses one sample object, so each agent's last sample is copied into its own slot.
  private readonly samples: (AgentSample | null)[];
  private readonly slots: AgentSample[];

  constructor(private readonly context: ViewerContext, personas: string[], withHeat = true) {
    this.count = Math.max(personas.length, 1);
    this.personas = personas;
    this.worldPositions = new Float32Array(this.count * 3).fill(NaN);
    this.samples = new Array(this.count).fill(null);
    this.slots = Array.from({ length: this.count }, () => ({
      level: "", x: 0, z: 0, world: new THREE.Vector3(), heading: 0, state: "", moving: false, fear: NaN, fill: new THREE.Color(), ring: new THREE.Color(), phase: 0,
    }));
    this.hazard = context.token("hazard-bright");
    this.calm = context.token("drafting");
    const basic = (options: THREE.MeshBasicMaterialParameters = {}) => this.track(new THREE.MeshBasicMaterial(options));
    const lit = (options: THREE.MeshStandardMaterialParameters = {}) => this.track(new THREE.MeshStandardMaterial({ roughness: 0.7, flatShading: true, ...options }));
    this.add("dot", new THREE.CircleGeometry(1, 12).rotateX(-Math.PI / 2).translate(0, 0.07, 0), basic());
    this.add("ghost", new THREE.CircleGeometry(1, 12).rotateX(-Math.PI / 2).translate(0, 0.06, 0), basic({ color: context.token("exit-bright"), transparent: true, opacity: 0.35, depthWrite: false }));
    this.add("chevron", chevronGeometry(), basic({ side: THREE.DoubleSide }));
    this.add("ring", new THREE.RingGeometry(0.75, 0.95, 24).rotateX(-Math.PI / 2).translate(0, 0.05, 0), basic({ side: THREE.DoubleSide }));
    this.add("refuge", dashedRing(), basic({ color: context.token("warn"), side: THREE.DoubleSide }));
    this.add("fear", new THREE.RingGeometry(0.42, 0.58, 24).rotateX(-Math.PI / 2).translate(0, 0.04, 0), basic({ side: THREE.DoubleSide, transparent: true, opacity: 0.85, depthWrite: false }));
    this.add("body", bodyGeometry(), lit());
    this.add("vest", new THREE.CylinderGeometry(0.175, 0.212, 0.42, 6).translate(0, 1.16, 0), lit({ color: VEST }));
    this.add("chair", wheelchairGeometry(), lit({ color: context.token("line-strong") }));
    this.add("child", bodyGeometry().scale(0.58, 0.58, 0.58).translate(0, 0, 0.5), lit());
    this.add("fly", flyGeometry(), lit({ color: context.token("model") }));
    const linkMaterial = this.track(new THREE.LineBasicMaterial({ color: context.token("model") }));
    this.links = new THREE.LineSegments(new THREE.BufferGeometry(), linkMaterial);
    this.links.frustumCulled = false;
    this.root.add(this.links);
    if (withHeat) this.buildHeat(); // the compare run shares the floors, so only the first run paints heat
  }

  private track<T extends THREE.Material>(material: T): T {
    this.materials.push(material);
    return material;
  }

  private add(name: string, geometry: THREE.BufferGeometry, material: THREE.Material) {
    const mesh = new THREE.InstancedMesh(geometry, material, this.count);
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    for (let k = 0; k < this.count; k += 1) mesh.setMatrixAt(k, this.hidden);
    mesh.setColorAt(0, new THREE.Color(1, 1, 1));
    this.meshes[name] = mesh;
    this.root.add(mesh);
  }

  // One density texture per floor over its walkable footprint, in the floor's own group so it follows the views.
  private buildHeat() {
    const { doc } = this.context;
    for (const level of doc.levels) {
      const box = new THREE.Box2();
      doc.navmesh.triangles.forEach((triangle, index) => {
        if (doc.navmesh.triangle_level[index] !== level.id) return;
        for (const corner of triangle) box.expandByPoint(new THREE.Vector2(doc.navmesh.vertices[corner][0], doc.navmesh.vertices[corner][2]));
      });
      if (box.isEmpty()) continue;
      const w = Math.ceil((box.max.x - box.min.x) / HEAT_CELL_M) + 1;
      const h = Math.ceil((box.max.y - box.min.y) / HEAT_CELL_M) + 1;
      const texture = new THREE.DataTexture(new Uint8Array(w * h * 4), w, h);
      texture.magFilter = THREE.LinearFilter;
      texture.needsUpdate = true;
      const material = this.track(new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false, side: THREE.DoubleSide }));
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w * HEAT_CELL_M, h * HEAT_CELL_M).rotateX(-Math.PI / 2), material);
      // Texture row 0 is the plane's -v edge, which rotateX puts at +z: flip so row 0 is minZ.
      mesh.scale.z = -1;
      mesh.position.set(box.min.x + (w * HEAT_CELL_M) / 2 - HEAT_CELL_M / 2, level.elevation_m + 0.05, box.min.y + (h * HEAT_CELL_M) / 2 - HEAT_CELL_M / 2);
      mesh.raycast = () => {};
      mesh.renderOrder = 5;
      this.context.levelRoot(level.id).add(mesh);
      this.heats.set(level.id, { level: level.id, minX: box.min.x - HEAT_CELL_M / 2, minZ: box.min.y - HEAT_CELL_M / 2, w, h, counts: new Float32Array(w * h), texture, mesh });
    }
  }

  setOnTop(onTop: boolean) {
    for (const material of this.materials) material.depthTest = !onTop;
    for (const mesh of Object.values(this.meshes)) mesh.renderOrder = onTop ? 20 : 2;
    this.links.renderOrder = onTop ? 20 : 2;
  }

  setVisible(visible: boolean) {
    this.root.visible = visible;
    for (const heat of this.heats.values()) heat.mesh.visible = visible && this.lod === "far";
  }

  /** Start a frame: pick the level of detail from the zoom. */
  begin(pxPerMeter: number) {
    this.lod = lodFor(pxPerMeter);
    const far = this.lod === "far";
    const mid = this.lod === "mid";
    const near = this.lod === "near";
    this.meshes.dot.visible = far;
    this.meshes.chevron.visible = mid;
    this.meshes.ring.visible = !far;
    this.meshes.refuge.visible = !far;
    for (const name of ["body", "vest", "chair", "child", "fear"]) this.meshes[name].visible = near;
    this.meshes.fly.visible = !far;
    for (const heat of this.heats.values()) heat.mesh.visible = far && this.root.visible;
    this.pxPerMeter = pxPerMeter;
    this.linkPositions.length = 0;
  }

  private pxPerMeter = 10;

  hide(k: number) {
    for (const mesh of Object.values(this.meshes)) mesh.setMatrixAt(k, this.hidden);
    this.worldPositions[k * 3] = NaN;
    this.samples[k] = null;
  }

  set(k: number, a: AgentSample, now: number) {
    const persona = this.personas[k] ?? "";
    const exited = a.state === "exited";
    const px = this.pxPerMeter;
    for (const mesh of Object.values(this.meshes)) mesh.setMatrixAt(k, this.hidden);
    if (exited) this.samples[k] = null;
    else {
      const slot = this.slots[k];
      Object.assign(slot, { level: a.level, x: a.x, z: a.z, heading: a.heading, state: a.state, moving: a.moving, fear: a.fear, phase: a.phase });
      slot.world.copy(a.world);
      this.samples[k] = slot;
    }
    if (exited) {
      this.worldPositions[k * 3] = NaN;
      this.s.setScalar(Math.max(0.2, (DOT_PX * 0.8) / px));
      this.m.compose(a.world, this.q.identity(), this.s);
      this.meshes.ghost.setMatrixAt(k, this.m);
      return;
    }
    a.world.toArray(this.worldPositions, k * 3);
    this.q.setFromAxisAngle(this.up, a.heading);
    const isFly = persona === "fruit_fly" || persona === "fly";
    if (this.lod === "far") {
      this.s.setScalar(Math.max(0.25, DOT_PX / px));
      this.m.compose(a.world, this.q, this.s);
      this.meshes.dot.setMatrixAt(k, this.m);
      this.meshes.dot.setColorAt(k, a.fill);
      return;
    }
    const size = this.lod === "mid" ? Math.max(0.9, CHEVRON_PX / px) : 0.6;
    // Rings say what state someone is in: amber pulse while deciding, dashed while held at a refuge.
    const pulse = a.state === "deciding" ? 1 + 0.25 * Math.sin(now / 130) : 1;
    this.s.setScalar(size * pulse);
    this.m.compose(a.world, this.q, this.s);
    if (a.state === "waiting_at_refuge") this.meshes.refuge.setMatrixAt(k, this.m);
    else {
      this.meshes.ring.setMatrixAt(k, this.m);
      this.meshes.ring.setColorAt(k, a.ring);
    }
    if (isFly) {
      this.s.setScalar(this.lod === "mid" ? size * 1.6 : FLY_SCALE * 0.5);
      this.p.copy(a.world);
      this.p.y += Math.sin(now / 90 + a.phase) * 0.05;
      this.m.compose(this.p, this.q, this.s);
      this.meshes.fly.setMatrixAt(k, this.m);
      return;
    }
    if (this.lod === "mid") {
      this.s.setScalar(size);
      this.m.compose(a.world, this.q, this.s);
      this.meshes.chevron.setMatrixAt(k, this.m);
      this.meshes.chevron.setColorAt(k, a.fill);
      return;
    }
    // Near: a figure that bobs as it walks; people in wheelchairs sit lower and do not bob.
    const seated = persona === "mobility_impaired" || a.state === "carried";
    this.p.copy(a.world);
    if (a.moving && !seated) this.p.y += Math.abs(Math.sin(now / 160 + a.phase)) * 0.06;
    this.s.set(1, seated ? 0.78 : 1, 1);
    if (seated) this.p.y += 0.12;
    this.m.compose(this.p, this.q, this.s);
    this.meshes.body.setMatrixAt(k, this.m);
    this.meshes.body.setColorAt(k, a.fill);
    if (persona === "staff") this.meshes.vest.setMatrixAt(k, this.m);
    if (persona === "parent" || persona === "parent_with_child") {
      this.meshes.child.setMatrixAt(k, this.m);
      this.meshes.child.setColorAt(k, a.fill);
    }
    if (seated) {
      this.m.compose(a.world, this.q, this.s.setScalar(1));
      this.meshes.chair.setMatrixAt(k, this.m);
    }
    if (a.fear >= 0.2) {
      this.s.setScalar(0.8 + a.fear * 0.6);
      this.m.compose(a.world, this.q, this.s);
      this.meshes.fear.setMatrixAt(k, this.m);
      this.meshes.fear.setColorAt(k, this.c.copy(this.calm).lerp(this.hazard, Math.min(1, a.fear * 1.4)));
    }
  }

  /** Finish a frame: upload instances, pair carriers with the people they carry, refresh the heat layer. */
  end(now: number) {
    for (const mesh of Object.values(this.meshes)) {
      if (!mesh.visible && mesh !== this.meshes.ghost) continue;
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
    this.pairCarried();
    if (this.lod === "far" && now - this.heatAt > HEAT_PERIOD_MS) {
      this.heatAt = now;
      this.paintHeat();
    }
  }

  private pairCarried() {
    const positions = this.linkPositions;
    for (let k = 0; k < this.count; k += 1) {
      const a = this.samples[k];
      if (!a || a.state !== "carried") continue;
      let best = -1;
      let bestD = 3;
      for (let j = 0; j < this.count; j += 1) {
        const b = this.samples[j];
        if (!b || b.state !== "helping" || b.level !== a.level) continue;
        const d = Math.hypot(a.x - b.x, a.z - b.z);
        if (d < bestD) [best, bestD] = [j, d];
      }
      if (best < 0) continue;
      const b = this.samples[best]!;
      positions.push(a.world.x, a.world.y + 0.9, a.world.z, b.world.x, b.world.y + 0.9, b.world.z);
    }
    const geometry = this.links.geometry;
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.computeBoundingSphere();
  }

  // Persons per square meter, Gaussian-smoothed over 5x5 cells: pale when sparse, amber at 1, red from 2 (Fruin LOS D-F).
  private paintHeat() {
    for (const heat of this.heats.values()) heat.counts.fill(0);
    for (const a of this.samples) {
      if (!a) continue;
      const heat = this.heats.get(a.level);
      if (!heat) continue;
      const cx = Math.floor((a.x - heat.minX) / HEAT_CELL_M);
      const cz = Math.floor((a.z - heat.minZ) / HEAT_CELL_M);
      for (let dz = -2; dz <= 2; dz += 1) {
        for (let dx = -2; dx <= 2; dx += 1) {
          const x = cx + dx;
          const z = cz + dz;
          if (x < 0 || z < 0 || x >= heat.w || z >= heat.h) continue;
          heat.counts[z * heat.w + x] += KERNEL[(dz + 2) * 5 + dx + 2];
        }
      }
    }
    const low = this.context.token("text");
    const mid = this.context.token("warn");
    const high = this.hazard;
    for (const heat of this.heats.values()) {
      const data = heat.texture.image.data as Uint8Array;
      for (let i = 0; i < heat.counts.length; i += 1) {
        const density = heat.counts[i] / (HEAT_CELL_M * HEAT_CELL_M); // the kernel sums to 1
        if (density < 0.015) {
          data[i * 4 + 3] = 0;
          continue;
        }
        const t = Math.min(density / 2, 1);
        this.c.copy(t < 0.5 ? low : mid).lerp(t < 0.5 ? mid : high, t < 0.5 ? t * 2 : (t - 0.5) * 2);
        data[i * 4] = this.c.r * 255;
        data[i * 4 + 1] = this.c.g * 255;
        data[i * 4 + 2] = this.c.b * 255;
        data[i * 4 + 3] = 50 + 190 * Math.sqrt(t);
      }
      heat.texture.needsUpdate = true;
    }
  }

  /** Agent nearest the ray within ~14 px, or -1. */
  pick(raycaster: THREE.Raycaster) {
    const reach = Math.max(0.6, 14 / this.pxPerMeter);
    let best = -1;
    let bestD = reach * reach;
    for (let k = 0; k < this.count; k += 1) {
      if (Number.isNaN(this.worldPositions[k * 3])) continue;
      this.p.fromArray(this.worldPositions, k * 3);
      const d = raycaster.ray.distanceSqToPoint(this.p);
      if (d < bestD) [best, bestD] = [k, d];
    }
    return best;
  }
}
