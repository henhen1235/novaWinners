import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// Live runs are written under the repo's out/live by sim.serve; serving the repo root lets the viewer
// open them as replays through /@fs/ without copying them into public/.
const repoRoot = fileURLToPath(new URL("../..", import.meta.url)).replace(/\/$/, "");

export default defineConfig({
  define: { __REPO_ROOT__: JSON.stringify(repoRoot) },
  server: {
    port: 5173,
    fs: { allow: [repoRoot] },
    // The interview server (python -m cognition.serve) runs on 8771 (macOS sharingd owns 8770);
    // proxying avoids CORS. Override with INTERVIEW_PORT.
    proxy: {
      "/interview": `http://127.0.0.1:${process.env.INTERVIEW_PORT ?? "8771"}`,
      // Scenario API (python -m scenario.serve): /scenario/parse -> /parse, /scenario/chat -> /chat.
      "/scenario": {
        target: `http://127.0.0.1:${process.env.SCENARIO_PORT ?? "8792"}`,
        rewrite: (path) => path.replace(/^\/scenario/, ""),
        // When the server is not running, answer {offline: true} so the editing UI falls back to its
        // local parser without a red network error in the console.
        configure: (proxy) => {
          proxy.on("error", (_error, _request, response) => {
            if (!("writeHead" in response) || response.headersSent) return;
            response.writeHead(200, { "Content-Type": "application/json" });
            response.end(JSON.stringify({ offline: true }));
          });
        },
      },
    },
  },
});
