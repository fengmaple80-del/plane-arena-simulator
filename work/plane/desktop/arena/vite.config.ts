import { fileURLToPath } from "node:url"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from "vite"

export default defineConfig({
  base: "./",
  plugins: [tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  clearScreen: false,
  build: {
    target: "es2022",
    rolldownOptions: {
      onLog(level, log, handler) {
        if (log.code === "MODULE_LEVEL_DIRECTIVE" && log.message.includes('"use client"')) return
        handler(level, log)
      },
    },
  },
})
