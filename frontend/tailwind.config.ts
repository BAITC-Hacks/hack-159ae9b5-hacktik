import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#161B22",
        graphite: "#2A313C",
        steel: "#4A5568",
        line: "#DDE2E8",
        canvas: "#F7F8FA",
        copper: "#B4622E",
        accent: "#2C5F73",
        urgent: "#B4472E",
        warn: "#B4842E",
        ok: "#2E7D5B",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
