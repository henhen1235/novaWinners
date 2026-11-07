// Run: node src/nav.test.ts  (Node 23.6+ strips the types)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildNav, evacuationExits, findPath, locate, nearestExit, nearestExitAcross, onMesh, pathLength, type Vec2 } from "./nav.ts";

const scene = JSON.parse(readFileSync(new URL("../../../fixtures/tiny_office.scene.json", import.meta.url), "utf8"));
const nav = buildNav(scene.navmesh);

// Straight shot down the corridor: the funnel must collapse it to one segment.
const straight = findPath(nav, [3, 5], [13, 5], "L1")!;
assert.equal(straight.length, 2, `corridor path should be straight, got ${JSON.stringify(straight)}`);

// From each room through its door to an exit: every sample on the mesh, no zigzag.
// (Door jambs are rounded by the agent radius, so hugging one adds several close corners.)
for (const room of scene.rooms) {
  // Room centroids can sit on furniture holes; start from the nearest walkable triangle.
  const start = nav.centroids[locate(nav, [room.centroid[0], room.centroid[1]], "L1")];
  const best = nearestExit(nav, start, "L1", scene.exits)!;
  assert.ok(best, `no route from ${room.id}`);
  for (let i = 1; i < best.points.length; i += 1) {
    const [a, b] = [best.points[i - 1], best.points[i]];
    for (let t = 0.05; t < 1; t += 0.05) {
      const p: Vec2 = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      // The exit point itself sits on the wall line, so allow the last few cm off mesh.
      if (i === best.points.length - 1 && t > 0.9) continue;
      assert.ok(onMesh(nav, p, "L1", 0.05), `${room.id}: segment ${i} leaves the mesh at ${p}`);
    }
  }
  // Straight-line distance is a lower bound; a route within 1.6x of it is not zigzagging.
  const crow = Math.hypot(start[0] - best.exit.position[0], start[1] - best.exit.position[1]);
  assert.ok(pathLength(best.points) <= crow * 1.6 + 1, `${room.id}: ${pathLength(best.points)} m vs ${crow} m`);
}
// Two floors: a start upstairs must route down a stair link to a ground-floor exit.
const school = JSON.parse(readFileSync(new URL("../../../fixtures/two_floor_school.scene.json", import.meta.url), "utf8"));
const schoolNav = buildNav(school.navmesh);
const upstairs = school.rooms.find((room: { level: string }) => room.level === "L2");
const upStart = schoolNav.centroids[locate(schoolNav, [upstairs.centroid[0], upstairs.centroid[1]], "L2")];
const across = nearestExitAcross(schoolNav, upStart, "L2", school.exits, school.links, school.levels)!;
assert.ok(across, "no cross-floor route from L2");
assert.deepEqual(across.legs.map((leg) => leg.level), ["L2", "L1"], `legs ${JSON.stringify(across.legs.map((leg) => leg.level))}`);
assert.equal(across.exit.level, "L1");
assert.ok(across.legs[0].via, "first leg must end at a stair link");

// Mirrors the sim (665fafa): no route climbs toward a floor without an evacuation exit, and no
// route ends at a roof or top-floor exit. Tepper is sampled (every 7th room) to keep this fast.
type Scene = typeof school;
function checkNoRoof(name: string, data: Scene, every: number) {
  const meshNav = buildNav(data.navmesh);
  const allowed = new Set(evacuationExits(data.exits, data.levels).map((exit: { id: string }) => exit.id));
  const exitLevels = new Set(evacuationExits(data.exits, data.levels).map((exit: { level: string }) => exit.level));
  const elevation = new Map(data.levels.map((level: { id: string; elevation_m: number }) => [level.id, level.elevation_m]));
  let routes = 0;
  data.rooms.forEach((room: { id: string; level: string; centroid: number[] }, i: number) => {
    if (i % every) return;
    const tri = locate(meshNav, [room.centroid[0], room.centroid[1]], room.level);
    if (tri < 0) return;
    const route = nearestExitAcross(meshNav, meshNav.centroids[tri], room.level, data.exits, data.links ?? [], data.levels);
    if (!route) return;
    routes += 1;
    assert.ok(allowed.has(route.exit.id), `${name} ${room.id}: ends at non-evacuation exit ${route.exit.id}`);
    route.legs.forEach((leg, k) => {
      const next = route.legs[k + 1];
      if (!next) return;
      const climbs = (elevation.get(next.level) as number) > (elevation.get(leg.level) as number);
      assert.equal(Boolean(leg.climb), climbs, `${name} ${room.id}: climb flag wrong on leg ${k}`);
      if (climbs) assert.ok(exitLevels.has(next.level), `${name} ${room.id}: climbs to ${next.level}, which has no exit`);
    });
  });
  assert.ok(routes > 0, `${name}: no routes checked`);
}
checkNoRoof("school", school, 1);
const tepperUrl = new URL("../../../samples/cmu/tepper/scene.json", import.meta.url);
checkNoRoof("tepper", JSON.parse(readFileSync(tepperUrl, "utf8")), 7);
// A roof exit on the top floor is never a target.
const roofy = evacuationExits(
  [{ id: "r", level: "L2", position: [0, 0], label: "Roof hatch" }, { id: "g", level: "L1", position: [0, 0], label: "Main" }, { id: "t", level: "L2", position: [0, 0], label: "Door" }],
  school.levels,
);
assert.deepEqual(roofy.map((exit) => exit.id), ["g"]);
console.log("nav ok");
