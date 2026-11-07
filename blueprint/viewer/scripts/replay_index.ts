// Writes public/replays/index.json: every replay with its building and a friendly title, for the
// viewer's Scenario select. Usage: node scripts/replay_index.ts   (run from blueprint/viewer)
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { scenarioName } from "../src/names.ts";

const root = "public/replays";
const runs = readdirSync(root, { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && existsSync(join(root, entry.name, "replay.json")))
  .map((entry) => {
    // Only the head of the file is needed for meta.scene; replays can be tens of MB.
    const head = readFileSync(join(root, entry.name, "replay.json"), "utf8").slice(0, 4000);
    const scene = head.match(/"scene"\s*:\s*"([^"]*)"/)?.[1] ?? "";
    return { dir: entry.name, title: scenarioName(entry.name), scene, url: `/replays/${entry.name}/replay.json` };
  });
writeFileSync(join(root, "index.json"), JSON.stringify({ runs }, null, 1));
console.log(`${runs.length} replays -> ${join(root, "index.json")}`);
