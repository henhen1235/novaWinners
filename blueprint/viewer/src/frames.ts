// Replay frame parsing shared by the main thread and replay.worker.ts (no DOM or three.js here).
export type FrameRow = [string, number, number, string, string?];
export type ExitRef = { id: string; level: string; position: number[] };

export interface ParsedFrames {
  xs: Float32Array[]; // per frame, per agent; NaN when absent
  zs: Float32Array[];
  states: string[][];
  levels: Uint8Array[]; // index into the scene's levels
  exitOf: (string | null)[]; // exit each agent left by (nearest exit on its level at its first "exited" frame)
  exitTime: number[];
}

export function parseFrames(frames: { t: number; a: FrameRow[] }[], agentIds: string[], levelIds: string[], exits: ExitRef[]): ParsedFrames {
  const index = new Map(agentIds.map((id, i) => [id, i]));
  const levelIndex = new Map(levelIds.map((id, i) => [id, i]));
  const count = agentIds.length;
  const parsed: ParsedFrames = { xs: [], zs: [], states: [], levels: [], exitOf: new Array(count).fill(null), exitTime: new Array(count).fill(Infinity) };
  for (const frame of frames) {
    const x = new Float32Array(count).fill(NaN);
    const z = new Float32Array(count).fill(NaN);
    const s: string[] = new Array(count).fill("");
    const l = new Uint8Array(count);
    for (const [id, ax, az, state, level] of frame.a) {
      const i = index.get(id);
      if (i === undefined) continue;
      x[i] = ax;
      z[i] = az;
      s[i] = state;
      l[i] = (level && levelIndex.get(level)) || 0;
      if (state !== "exited" || parsed.exitOf[i] !== null) continue;
      parsed.exitTime[i] = frame.t;
      let best = Infinity;
      for (const exit of exits) {
        if (level && exit.level !== level) continue;
        const d = Math.hypot(exit.position[0] - ax, exit.position[1] - az);
        if (d < best) [best, parsed.exitOf[i]] = [d, exit.id];
      }
    }
    parsed.xs.push(x);
    parsed.zs.push(z);
    parsed.states.push(s);
    parsed.levels.push(l);
  }
  return parsed;
}
