// Writes public/replays/index.json: every replay with its building and a friendly title, for the
// viewer's Scenario select. Usage: node scripts/replay_index.ts   (run from blueprint/viewer)
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { scenarioName } from "../src/names.ts";

const root = "public/replays";
const runs = readdirSync(root, { withFileTypes: true })
  // Folders starting with "_" are scratch copies for testing, not runs to offer.
  .filter((entry) => entry.isDirectory() && !entry.name.startsWith("_") && existsSync(join(root, entry.name, "replay.json")))
  .map((entry) => {
    // Only the head of the file is needed for meta.scene; replays can be tens of MB.
    const head = readFileSync(join(root, entry.name, "replay.json"), "utf8").slice(0, 4000);
    const field = (name: string, text: string) => text.match(new RegExp(`"${name}"\\s*:\\s*"([^"]*)"`))?.[1];
    const scene = field("scene", head) ?? "";
    // Title from the run's own words: replay meta label/name/description, then the scenario file's, else the folder.
    let title = field("label", head) ?? field("name", head) ?? field("description", head);
    const scenarioPath = field("scenario", head);
    if (!title && scenarioPath && existsSync(join("../..", scenarioPath))) {
      const scenario = JSON.parse(readFileSync(join("../..", scenarioPath), "utf8"));
      title = scenario.name ?? scenario.title ?? scenario.description;
    }
    return { dir: entry.name, title: scenarioName(entry.name, title), scene, url: `/replays/${entry.name}/replay.json` };
  });
writeFileSync(join(root, "index.json"), JSON.stringify({ runs }, null, 1));
console.log(`${runs.length} replays -> ${join(root, "index.json")}`);
