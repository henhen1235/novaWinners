import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
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
