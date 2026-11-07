// Writes public/replays/index.json: every replay with its building and a friendly title, for the
// viewer's Scenario select. Usage: node scripts/replay_index.ts   (run from blueprint/viewer)
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { buildingName, scenarioName } from "../src/names.ts";

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
// Buildings for the header's Building select: every public/<dir>/scene.json that has recorded runs,
// opened on its run with suggestions if there is one, else its first run.
const buildings = readdirSync("public", { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && existsSync(join("public", entry.name, "scene.json")))
  .map((entry) => {
    const own = runs.filter((run) => run.scene.split("/").includes(entry.name));
    const best = own.find((run) => existsSync(join(root, run.dir, "recommendations.json"))) ?? own[0];
    const meta = JSON.parse(readFileSync(join("public", entry.name, "scene.json"), "utf8")).meta ?? {};
    const glb = existsSync(join("public", entry.name, "display.glb")) ? `/${entry.name}/display.glb` : "";
    return { key: entry.name, title: buildingName(meta.name), scene: `/${entry.name}/scene.json`, glb, replay: best?.url ?? "" };
  })
  .filter((building) => building.replay);
writeFileSync(join(root, "index.json"), JSON.stringify({ runs, buildings }, null, 1));
console.log(`${runs.length} replays -> ${join(root, "index.json")}`);
