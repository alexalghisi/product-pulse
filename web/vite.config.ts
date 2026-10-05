import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const api = process.env.PULSE_API_URL ?? "http://127.0.0.1:8140";

export default defineConfig({
    plugins: [react()],
    server: {
        port: Number(process.env.PORT ?? 5280),
        strictPort: true,
        proxy: { "/api": { target: api, changeOrigin: false } },
    },
    test: {
        environment: "jsdom",
        globals: true,
        setupFiles: ["src/test/setup.ts"],
        include: ["src/**/*.test.{ts,tsx}"],
    },
});
