import { createTikTokStyleCaptions, type Caption } from "@remotion/captions";
import { useMemo } from "react";
import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { pal, pixelFont } from "./pixel/art";
import type { Word } from "./storyboard";
import { color, font, safe } from "./theme";

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9.$%−/-]/g, "");

/**
 * Times for each caption word. With transcribed `words`, caption progress (by characters) is
 * mapped onto the real spoken timeline, so pauses and pace carry over even when captions use
 * digits where the voice says words. Without them, words are spread evenly by length.
 */
const captionTimes = (text: string, seconds: number, words: Word[] | null): Caption[] => {
  const tokens = text.split(/\s+/).filter(Boolean);
  const weights = tokens.map((w) => w.length + 1);
  const total = weights.reduce((a, b) => a + b, 0);

  let timeAt = (f: number) => 100 + f * Math.max(500, seconds * 1000 - 150);
  if (words && words.length) {
    const spans = words.map((w) => ({ chars: w.word.length + 1, start: w.start * 1000, end: w.end * 1000 }));
    const spokenTotal = spans.reduce((a, b) => a + b.chars, 0);
    timeAt = (f: number) => {
      let pos = f * spokenTotal;
      for (const s of spans) {
        if (pos <= s.chars) return s.start + (s.end - s.start) * (pos / s.chars);
        pos -= s.chars;
      }
      return spans[spans.length - 1].end;
    };
  }

  let acc = 0;
  return tokens.map((w, i) => {
    const startMs = timeAt(acc / total);
    acc += weights[i];
    const endMs = timeAt(acc / total);
    return { text: (i === 0 ? "" : " ") + w, startMs, endMs, timestampMs: (startMs + endMs) / 2, confidence: null };
  });
};

/** Indices of caption tokens that belong to a highlighted phrase. */
const highlighted = (tokens: string[], phrases: string[]) => {
  const marks = new Set<number>();
  const clean = tokens.map(norm);
  for (const phrase of phrases) {
    const parts = phrase.split(/\s+/).map(norm);
    for (let i = 0; i + parts.length <= clean.length; i++) {
      if (parts.every((p, k) => clean[i + k].replace(/[.,!?]$/, "") === p.replace(/[.,!?]$/, ""))) {
        parts.forEach((_, k) => marks.add(i + k));
      }
    }
  }
  return marks;
};

const PIXEL_OUTLINE = [
  [-4, 0], [4, 0], [0, -4], [0, 4], [-4, -4], [4, -4], [-4, 4], [4, 4], [4, 8], [0, 8],
]
  .map(([x, y]) => `${x}px ${y}px 0 ${pal.ink}`)
  .join(", ");

export const SceneCaptions: React.FC<{
  text: string;
  seconds: number;
  words?: Word[] | null;
  highlight?: string[];
  variant?: "explainer" | "pixel";
}> = ({ text, seconds, words = null, highlight = [], variant = "explainer" }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const captions = useMemo(() => captionTimes(text, seconds, words), [text, seconds, words]);
  const marks = useMemo(() => highlighted(captions.map((c) => c.text.trim()), highlight), [captions, highlight]);
  const pages = useMemo(() => createTikTokStyleCaptions({ captions, combineTokensWithinMilliseconds: 1100 }).pages, [captions]);
  const ms = (frame / fps) * 1000;
  // Hold the most recent page through pauses; nothing before the first word or after the line ends.
  const started = pages.filter((p) => p.startMs <= ms);
  const page = ms > seconds * 1000 ? null : (started[started.length - 1] ?? null);
  if (!page) return null;

  const pageFrame = Math.round((page.startMs / 1000) * fps);
  const pixel = variant === "pixel";
  const popIn = pixel
    ? Math.min(1, Math.floor((frame - pageFrame) / 2) * 0.5)
    : spring({ frame: frame - pageFrame, fps, config: { damping: 18, stiffness: 220 } });
  const firstIndex = captions.findIndex((c) => c.startMs === page.tokens[0]?.fromMs);

  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: safe.bottom + 20 }}>
      <div
        style={{
          maxWidth: 1080 - safe.side * 2 - (pixel ? 90 : 60),
          textAlign: "center",
          fontFamily: pixel ? pixelFont : font.display,
          fontWeight: pixel ? 400 : 700,
          fontSize: pixel ? 46 : 76,
          lineHeight: pixel ? 1.5 : 1.08,
          color: pixel ? pal.white : color.text,
          textShadow: pixel ? PIXEL_OUTLINE : "0 4px 18px rgba(0,0,0,0.65)",
          transform: `scale(${0.92 + 0.08 * popIn})`,
          whiteSpace: "pre-wrap",
        }}
      >
        {page.tokens.map((tok, k) => {
          const active = ms >= tok.fromMs && ms < tok.toMs;
          const key = marks.has(firstIndex + k);
          const fill = key ? (pixel ? pal.gold : color.court) : active ? (pixel ? pal.green : color.court) : pixel ? pal.white : color.text;
          return (
            <span key={tok.fromMs} style={{ color: fill }}>
              {tok.text}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
