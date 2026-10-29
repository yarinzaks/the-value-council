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
        muted: "rgb(var(--muted) / <alpha-value>)",
        gain: "rgb(var(--gain) / <alpha-value>)",
        loss: "rgb(var(--loss) / <alpha-value>)",
        watch: "#ca8a04",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "Menlo", "Consolas", "monospace"],
      },

      /* Type is drawn at a size, not in a size: the same face wants
       * tighter tracking as it grows and looser as it shrinks, so a
       * single letter-spacing is wrong somewhere by definition.
       *
       * Attaching the tracking to the size here means every existing
       * `text-3xl` in the tree picks up display tracking with no edit,
       * and no component has to carry a `tracking-` class that could
       * drift from its size later. Line heights are Tailwind's own
       * below text-4xl — the dense tables are built on them — and
       * tighten only at the display sizes, where the default leading
       * is visibly loose. The values are variables so RTL can switch
       * the whole ramp off; Hebrew is not a tracked script. */
      fontSize: {
        xs: ["0.75rem", { lineHeight: "1rem", letterSpacing: "var(--tk-xs)" }],
        sm: [
          "0.875rem",
          { lineHeight: "1.25rem", letterSpacing: "var(--tk-sm)" },
        ],
        base: [
          "1rem",
          { lineHeight: "1.5rem", letterSpacing: "var(--tk-base)" },
        ],
        lg: [
          "1.125rem",
          { lineHeight: "1.75rem", letterSpacing: "var(--tk-lg)" },
        ],
        xl: [
          "1.25rem",
          { lineHeight: "1.75rem", letterSpacing: "var(--tk-xl)" },
        ],
        "2xl": [
          "1.5rem",
          { lineHeight: "2rem", letterSpacing: "var(--tk-2xl)" },
        ],
        "3xl": [
          "1.875rem",
          { lineHeight: "2.25rem", letterSpacing: "var(--tk-3xl)" },
        ],
        "4xl": [
          "2.25rem",
          { lineHeight: "2.5rem", letterSpacing: "var(--tk-4xl)" },
        ],
        "5xl": ["3rem", { lineHeight: "1.06", letterSpacing: "var(--tk-5xl)" }],
        "6xl": [
          "3.75rem",
          { lineHeight: "1.04", letterSpacing: "var(--tk-6xl)" },
        ],
      },

      /* Three steps, and no more: a resting surface, one the pointer
       * is over, and one that floats free of the page. */
      boxShadow: {
        "elev-1": "var(--elev-1)",
        "elev-2": "var(--elev-2)",
        "elev-3": "var(--elev-3)",
      },

      transitionTimingFunction: {
        standard: "var(--ease-standard)",
        momentum: "var(--ease-momentum)",
        exit: "var(--ease-exit)",
      },

      transitionDuration: {
        press: "100ms",
        quick: "180ms",
        base: "320ms",
      },

      keyframes: {
        "material-in": {
          from: { opacity: "0", transform: "scale(0.96) translateY(4px)" },
          to: { opacity: "1", transform: "scale(1) translateY(0)" },
        },
        "rise-in": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },

      animation: {
        "material-in": "material-in 220ms var(--ease-standard) both",
        "rise-in": "rise-in 320ms var(--ease-standard) both",
      },
    },
  },
  plugins: [],
};
export default config;
