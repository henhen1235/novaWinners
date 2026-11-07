// A red "!" over a patch of floor where people are bunched together.
import type { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import type { ViewerContext } from "./context";

const MIN_PEOPLE = 10;
const SECTION_M = 8;

export type CrowdPerson = { level: string; x: number; z: number };
export type CrowdSpot = { level: string; x: number; z: number; n: number };

/** One mark per bunch of `min` people who fit inside `radius` meters. */
export function crowdSpots(people: CrowdPerson[], min = MIN_PEOPLE, radius = SECTION_M): CrowdSpot[] {
  const r2 = radius * radius;
  const cell = radius;
  const pts = people.map((p) => ({ ...p, cx: Math.floor(p.x / cell), cz: Math.floor(p.z / cell) }));
  const buckets = new Map<string, number[]>();
  const cellCount = new Map<string, number>();
  const key = (level: string, cx: number, cz: number) => `${level}|${cx}|${cz}`;
  pts.forEach((p, i) => {
    const id = key(p.level, p.cx, p.cz);
    const bucket = buckets.get(id);
    if (bucket) bucket.push(i);
    else buckets.set(id, [i]);
    cellCount.set(id, (cellCount.get(id) ?? 0) + 1);
  });
  const alive = new Uint8Array(pts.length).fill(1);
  const near: number[] = [];
  const spots: CrowdSpot[] = [];

  const gather = (i: number) => {
    near.length = 0;
    const p = pts[i];
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dz = -1; dz <= 1; dz += 1) {
        const bucket = buckets.get(key(p.level, p.cx + dx, p.cz + dz));
        if (!bucket) continue;
        for (const j of bucket) {
          if (!alive[j]) continue;
          const q = pts[j];
          const ex = q.x - p.x;
          const ez = q.z - p.z;
          if (ex * ex + ez * ez <= r2) near.push(j);
        }
      }
    }
  };

  const around = (p: (typeof pts)[number]) => {
    let n = 0;
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dz = -1; dz <= 1; dz += 1) n += cellCount.get(key(p.level, p.cx + dx, p.cz + dz)) ?? 0;
    }
    return n;
  };

  for (;;) {
    let best = -1;
    let bestN = 0;
    for (let i = 0; i < pts.length; i += 1) {
      if (!alive[i] || around(pts[i]) < min) continue;
      gather(i);
      if (near.length > bestN) {
        best = i;
        bestN = near.length;
      }
    }
    if (best < 0 || bestN < min) break;
    gather(best);
    let x = 0;
    let z = 0;
    for (const j of near) {
      x += pts[j].x;
      z += pts[j].z;
      alive[j] = 0;
      const id = key(pts[j].level, pts[j].cx, pts[j].cz);
      cellCount.set(id, (cellCount.get(id) ?? 1) - 1);
    }
    spots.push({ level: pts[best].level, x: x / near.length, z: z / near.length, n: near.length });
  }
  return spots;
}

type Mark = { tag: CSS2DObject; level: string; n: number; tip: HTMLElement };

export class CrowdMarks {
  private readonly marks: Mark[] = [];
  private readonly elevation = new Map<string, number>();
  private last = 0;
  private readonly context: ViewerContext;

  constructor(context: ViewerContext) {
    this.context = context;
    for (const level of context.doc.levels) this.elevation.set(level.id, level.elevation_m);
  }

  /** Drop the throttle so the next update follows a view change immediately. */
  poke() {
    this.last = 0;
  }

  update(people: CrowdPerson[], now: number) {
    if (now - this.last < 450) return;
    this.last = now;
    const spots = crowdSpots(people);
    while (this.marks.length < spots.length) {
      const tip = document.createElement("span");
      tip.textContent = "!";
      const tag = this.context.label("crowd-mark", (element) => {
        element.append(tip);
      });
      // center y = 1 keeps the badge above its anchor. The anchor is zero-size; the span does the offset.
      tag.center.set(0.5, 1);
      this.marks.push({ tag, level: "", n: 0, tip });
    }
    spots.forEach((spot, i) => {
      const mark = this.marks[i];
      if (mark.level !== spot.level) {
        mark.tag.removeFromParent();
        this.context.levelRoot(spot.level).add(mark.tag);
        mark.level = spot.level;
      }
      mark.tag.position.set(spot.x, (this.elevation.get(spot.level) ?? 0) + 2.4, spot.z);
      mark.tag.visible = true;
      if (mark.n !== spot.n) {
        const text = `${spot.n} people bunched here. High danger.`;
        mark.tip.title = text;
        mark.tip.setAttribute("aria-label", text);
        mark.n = spot.n;
      }
    });
    for (let i = spots.length; i < this.marks.length; i += 1) {
      const mark = this.marks[i];
      if (!mark.tag.visible) continue;
      mark.tag.visible = false;
      mark.tag.removeFromParent();
      mark.level = "";
    }
  }
}
