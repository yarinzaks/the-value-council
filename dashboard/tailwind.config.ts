import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Council brand palette — sober, finance-forward.
        council: {
          50: "#f5f7fa",
          100: "#e4e9f0",
          200: "#c8d3e0",
          300: "#9fb1c7",
          400: "#7089a9",
          500: "#516c8e",
          600: "#3f5673",
          700: "#33455d",
          800: "#1f2a3a",
          900: "#11182a",
          950: "#0a1020",
        },
        gain: "#16a34a",
        loss: "#dc2626",
        watch: "#ca8a04",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
