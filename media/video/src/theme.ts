import { loadFont as loadBarlow } from "@remotion/google-fonts/Barlow";
import { loadFont as loadCondensed } from "@remotion/google-fonts/BarlowCondensed";

// Same tokens as the dashboard (app/ui.py), so videos and app read as one brand.
export const color = {
  bg: "#0E1A2B",
  surface: "#14233A",
  raised: "#1B2E4A",
  line: "#2A3F5F",
  text: "#EAF0F8",
  muted: "#9AABC4",
  court: "#FF7A1A",
  courtInk: "#1F1003",
  edge: "#34D399",
  risk: "#F87171",
  market: "#8FB3E8",
};

export const font = {
  body: loadBarlow("normal", { weights: ["500", "600"], subsets: ["latin"] }).fontFamily,
  display: loadCondensed("normal", { weights: ["600", "700"], subsets: ["latin"] }).fontFamily,
};

// 1080x1920 layout: TikTok/Reels chrome covers roughly the bottom 380px and a
// right-hand column, so key content stays in this box.
export const safe = { top: 270, side: 90, bottom: 420 };
