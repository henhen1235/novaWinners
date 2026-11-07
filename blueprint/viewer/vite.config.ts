import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    // The interview server (python -m cognition.serve) runs on 8770; proxying avoids CORS.
    proxy: { "/interview": "http://127.0.0.1:8770" },
  },
});
