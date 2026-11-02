import { createTikTokStyleCaptions, type Caption } from "@remotion/captions";
import { useMemo } from "react";
import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { color, font, safe } from "./theme";

// shortcut: word times are spread across the line by character count, not measured.
// Good enough for one-voice TTS; switch to word timestamps from a transcription if lips or beats must sync.
const estimateCaptions = (text: string, seconds: number): Caption[] => {
  const words = text.split(/\s+/).filter(Boolean);
  const weights = words.map((w) => w.length + 2);
  const total = weights.reduce((a, b) => a + b, 0);
  const speechMs = Math.max(500, seconds * 1000 - 150);
  let t = 100;
  return words.map((w, i) => {
    const dur = (weights[i] / total) * speechMs;
    const caption: Caption = {
      text: (i === 0 ? "" : " ") + w,
      startMs: t,
      endMs: t + dur,
      timestampMs: t + dur / 2,
      confidence: null,
    };
    t += dur;
    return caption;
  });
};

export const SceneCaptions: React.FC<{ text: string; seconds: number }> = ({ text, seconds }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pages = useMemo(
    () => createTikTokStyleCaptions({ captions: estimateCaptions(text, seconds), combineTokensWithinMilliseconds: 1100 }).pages,
    [text, seconds],
  );
  const ms = (frame / fps) * 1000;
  const page = pages.find((p) => ms >= p.startMs && ms < p.startMs + p.durationMs) ?? (ms >= seconds * 1000 ? null : pages[0]);
  if (!page) return null;

  const pageFrame = Math.round((page.startMs / 1000) * fps);
  const pop = spring({ frame: frame - pageFrame, fps, config: { damping: 18, stiffness: 220 } });

  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: safe.bottom + 20 }}>
      <div
        style={{
          maxWidth: 1080 - safe.side * 2 - 60,
          textAlign: "center",
          fontFamily: font.display,
          fontWeight: 700,
          fontSize: 76,
          lineHeight: 1.08,
          color: color.text,
          textShadow: "0 4px 18px rgba(0,0,0,0.65)",
          transform: `scale(${0.92 + 0.08 * pop})`,
          whiteSpace: "pre-wrap",
        }}
      >
        {page.tokens.map((tok) => {
          const active = ms >= tok.fromMs && ms < tok.toMs;
          return (
            <span key={tok.fromMs} style={{ color: active ? color.court : color.text }}>
              {tok.text}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
