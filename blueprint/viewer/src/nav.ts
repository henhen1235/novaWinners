// Navmesh routing shared by the viewer and scripts/fake_replay.ts: A* over the
// triangle graph, then the simple stupid funnel algorithm (Mononen) over the
// triangle corridor so the route is straight and hugs corners.
// Plain TypeScript with no imports so Node can run it with type stripping.

export type Vec2 = [number, number]; // scene [x, z]

export interface NavMesh {
  vertices: number[][];
  triangles: number[][];
  triangle_level: string[];
}

export interface Nav {
  mesh: NavMesh;
  corners: Vec2[][]; // per triangle, its three corners in [x, z]
  centroids: Vec2[];
  neighbours: { tri: number; edge: [number, number] }[][]; // edge = shared vertex ids
}

export function buildNav(mesh: NavMesh): Nav {
  const xz = (index: number): Vec2 => [mesh.vertices[index][0], mesh.vertices[index][2]];
  const corners = mesh.triangles.map((tri) => tri.map(xz));
  const centroids = corners.map((c): Vec2 => [(c[0][0] + c[1][0] + c[2][0]) / 3, (c[0][1] + c[1][1] + c[2][1]) / 3]);
  const neighbours: Nav["neighbours"] = mesh.triangles.map(() => []);
  const owner = new Map<string, number>();
  mesh.triangles.forEach((tri, index) => {
    for (let k = 0; k < 3; k += 1) {
      const u = tri[k];
      const v = tri[(k + 1) % 3];
      const key = u < v ? `${u}:${v}` : `${v}:${u}`;
      const other = owner.get(key);
      if (other === undefined) owner.set(key, index);
      else {
        neighbours[index].push({ tri: other, edge: [u, v] });
        neighbours[other].push({ tri: index, edge: [u, v] });
      }
    }
  });
  return { mesh, corners, centroids, neighbours };
}

// Twice the signed area of abc, in Mononen's sign convention.
function triArea2(a: Vec2, b: Vec2, c: Vec2) {
  return (c[0] - a[0]) * (b[1] - a[1]) - (b[0] - a[0]) * (c[1] - a[1]);
}

function distToSegment(p: Vec2, a: Vec2, b: Vec2) {
  const dx = b[0] - a[0];
  const dz = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / (dx * dx + dz * dz || 1)));
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz);
}

function distToTriangle(p: Vec2, c: Vec2[]) {
  const s0 = triArea2(c[0], c[1], p);
  const s1 = triArea2(c[1], c[2], p);
  const s2 = triArea2(c[2], c[0], p);
  const inside = (s0 >= 0 && s1 >= 0 && s2 >= 0) || (s0 <= 0 && s1 <= 0 && s2 <= 0);
  if (inside) return 0;
  return Math.min(distToSegment(p, c[0], c[1]), distToSegment(p, c[1], c[2]), distToSegment(p, c[2], c[0]));
}

/** Triangle on `level` containing p, or the closest one when p is just off the mesh (exits sit on the wall line). */
export function locate(nav: Nav, p: Vec2, level: string): number {
  let best = -1;
  let bestDistance = Infinity;
  nav.corners.forEach((c, index) => {
    if (nav.mesh.triangle_level[index] !== level) return;
    const distance = distToTriangle(p, c);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = index;
    }
  });
  return best;
}

const dist = (a: Vec2, b: Vec2) => Math.hypot(a[0] - b[0], a[1] - b[1]);

// ponytail: linear scan open set, O(n^2) in triangles; fine below ~5k triangles, use a binary heap above that.
function corridor(nav: Nav, start: number, goal: number): number[] | null {
  const g = new Map<number, number>([[start, 0]]);
  const previous = new Map<number, number>();
  const open = new Set([start]);
  const closed = new Set<number>();
  const h = (tri: number) => dist(nav.centroids[tri], nav.centroids[goal]);
  while (open.size) {
    let current = -1;
    let bestF = Infinity;
    for (const tri of open) {
      const f = g.get(tri)! + h(tri);
      if (f < bestF) {
        bestF = f;
        current = tri;
      }
    }
    if (current === goal) break;
    open.delete(current);
    closed.add(current);
    for (const { tri } of nav.neighbours[current]) {
      if (closed.has(tri)) continue;
      const cost = g.get(current)! + dist(nav.centroids[current], nav.centroids[tri]);
      if (cost < (g.get(tri) ?? Infinity)) {
        g.set(tri, cost);
        previous.set(tri, current);
        open.add(tri);
      }
    }
  }
  if (!g.has(goal)) return null;
  const path = [goal];
  while (path[0] !== start) path.unshift(previous.get(path[0])!);
  return path;
}

/** Portals (left, right) crossed along the corridor, oriented for stringPull. */
function portals(nav: Nav, tris: number[], start: Vec2, end: Vec2): [Vec2, Vec2][] {
  const result: [Vec2, Vec2][] = [[start, start]];
  for (let i = 0; i + 1 < tris.length; i += 1) {
    const link = nav.neighbours[tris[i]].find((item) => item.tri === tris[i + 1])!;
    const u: Vec2 = [nav.mesh.vertices[link.edge[0]][0], nav.mesh.vertices[link.edge[0]][2]];
    const v: Vec2 = [nav.mesh.vertices[link.edge[1]][0], nav.mesh.vertices[link.edge[1]][2]];
    // Seen from inside the current triangle, the left end is u when cuv has positive area.
    const c = nav.centroids[tris[i]];
    result.push(triArea2(c, u, v) > 0 ? [u, v] : [v, u]);
  }
  result.push([end, end]);
  return result;
}

const same = (a: Vec2, b: Vec2) => Math.abs(a[0] - b[0]) < 1e-9 && Math.abs(a[1] - b[1]) < 1e-9;

/** Simple stupid funnel algorithm: http://digestingduck.blogspot.com/2010/03/simple-stupid-funnel-algorithm.html */
export function stringPull(gates: [Vec2, Vec2][]): Vec2[] {
  const points: Vec2[] = [gates[0][0]];
  let apex = gates[0][0];
  let left = gates[0][0];
  let right = gates[0][1];
  let apexIndex = 0;
  let leftIndex = 0;
  let rightIndex = 0;
  for (let i = 1; i < gates.length; i += 1) {
    const [newLeft, newRight] = gates[i];
    if (triArea2(apex, right, newRight) <= 0) {
      if (same(apex, right) || triArea2(apex, left, newRight) > 0) {
        right = newRight;
        rightIndex = i;
      } else {
        // Right crossed over left: left becomes a corner of the path.
        points.push(left);
        apex = left;
        apexIndex = leftIndex;
        right = apex;
        rightIndex = apexIndex;
        i = apexIndex;
        continue;
      }
    }
    if (triArea2(apex, left, newLeft) >= 0) {
      if (same(apex, left) || triArea2(apex, right, newLeft) < 0) {
        left = newLeft;
        leftIndex = i;
      } else {
        points.push(right);
        apex = right;
        apexIndex = rightIndex;
        left = apex;
        leftIndex = apexIndex;
        i = apexIndex;
        continue;
      }
    }
  }
  const end = gates[gates.length - 1][0];
  if (!same(points[points.length - 1], end)) points.push(end);
  return points;
}

export function findPath(nav: Nav, start: Vec2, end: Vec2, level: string): Vec2[] | null {
  const from = locate(nav, start, level);
  const to = locate(nav, end, level);
  if (from < 0 || to < 0) return null;
  const tris = corridor(nav, from, to);
  return tris ? stringPull(portals(nav, tris, start, end)) : null;
}

export function pathLength(points: Vec2[]): number {
  let total = 0;
  for (let i = 1; i < points.length; i += 1) total += dist(points[i - 1], points[i]);
  return total;
}

export interface ExitLike {
  id: string;
  level: string;
  position: number[];
}

/** Shortest walking route to any exit on `level`, or null when none is reachable. */
export function nearestExit<T extends ExitLike>(nav: Nav, start: Vec2, level: string, exits: T[]) {
  let best: { exit: T; points: Vec2[]; meters: number } | null = null;
  for (const exit of exits.filter((item) => item.level === level)) {
    const points = findPath(nav, start, [exit.position[0], exit.position[1]], level);
    if (!points) continue;
    const meters = pathLength(points);
    if (!best || meters < best.meters) best = { exit, points, meters };
  }
  return best;
}

/** True when p is on the walkable mesh (within `slack` meters). */
export function onMesh(nav: Nav, p: Vec2, level: string, slack = 0.02): boolean {
  const tri = locate(nav, p, level);
  return tri >= 0 && distToTriangle(p, nav.corners[tri]) <= slack;
}
