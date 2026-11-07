import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    // The interview server (python -m cognition.serve) runs on 8771 (macOS sharingd owns 8770);
    // proxying avoids CORS. Override with INTERVIEW_PORT.
    proxy: { "/interview": `http://127.0.0.1:${process.env.INTERVIEW_PORT ?? "8771"}` },
  },
});
