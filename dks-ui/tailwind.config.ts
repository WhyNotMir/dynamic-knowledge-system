import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ["var(--font-cormorant)", "Georgia", "serif"],
        sans: ["var(--font-dm-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-jetbrains)", "monospace"],
      },
      colors: {
        vault: {
          bg: "#09090B",
          surface: "#111113",
          "surface-2": "#161618",
          border: "#1C1C1F",
          "border-2": "#2A2A2F",
          text: "#F5F0E8",
          muted: "#6B6864",
          gold: "#D4A853",
          "gold-20": "rgba(212,168,83,0.20)",
          "gold-10": "rgba(212,168,83,0.10)",
          "gold-05": "rgba(212,168,83,0.05)",
          success: "#4ADE80",
          error: "#F87171",
          warning: "#FBBF24",
        },
      },
      borderRadius: {
        DEFAULT: "6px",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.3s ease forwards",
        shimmer: "shimmer 1.4s linear infinite",
      },
    },
  },
  plugins: [animate],
};

export default config;