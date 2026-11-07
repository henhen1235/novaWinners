// Parses a replay off the main thread: a 900 s, 170-person run is tens of MB of JSON.
import { parseFrames, type ExitRef, type FrameRow } from "./frames";

type Request = { url: string; levelIds: string[]; exits: ExitRef[] };

self.onmessage = async ({ data }: MessageEvent<Request>) => {
  try {
    const response = await fetch(data.url);
    if (!response.ok) throw new Error(`${data.url}: HTTP ${response.status}`);
    const doc = await response.json();
    const parsed = parseFrames(doc.frames as { t: number; a: FrameRow[] }[], doc.agents.map((agent: { id: string }) => agent.id), data.levelIds, data.exits);
    const frames = doc.frames.map((frame: { t: number }) => ({ t: frame.t, a: [] }));
    const transfer = [...parsed.xs, ...parsed.zs, ...parsed.levels].map((array) => array.buffer);
    (self as unknown as Worker).postMessage({ doc: { ...doc, frames }, parsed }, transfer);
  } catch (error) {
    (self as unknown as Worker).postMessage({ error: error instanceof Error ? error.message : String(error) });
  }
};
