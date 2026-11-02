/**
 * Storyboard format. One JSON file in storyboards/ describes one video:
 * what is said (`say`), what is shown in captions (`caption`), and the
 * scene type plus its parameters. All math is done here, not in the JSON,
 * so a storyboard can never carry a wrong number.
 */

type SceneBase = {
  id: string;
  /** Text sent to text-to-speech. Spell numbers the way they should be spoken. */
  say: string;
  /** On-screen caption text. Defaults to `say`. */
  caption?: string;
  /** Length used before narration exists. Narration length wins once generated. */
  seconds?: number;
};

export type Scene =
  | (SceneBase & { type: "hook"; title: string; kicker?: string })
  | (SceneBase & { type: "odds"; odds: number; title: string })
  | (SceneBase & {
      type: "vig";
      title: string;
      sides: [{ label: string; odds: number }, { label: string; odds: number }];
    })
  | (SceneBase & {
      type: "model";
      title: string;
      factors: string[];
      model: number;
      odds: number;
      exampleLabel: string;
    })
  | (SceneBase & { type: "cta"; title: string; points: string[] })
  | (SceneBase & { type: "pixel"; shot: PixelShot });

/** Hand-built shots of the pixel-art style (src/pixel/shots.tsx). */
export type PixelShot = "hook" | "fair" | "trick" | "math" | "longrun" | "takeaway";

/** One spoken word with its time in the scene's narration clip (seconds). */
export type Word = { word: string; start: number; end: number };

export type Storyboard = {
  id: string;
  title: string;
  voice: string;
  voiceInstructions?: string;
  /** "explainer" (default): navy brand look. "pixel": 16-bit arcade look with music and SFX. */
  style?: "explainer" | "pixel";
  /** Caption words or phrases to colour as key terms. */
  highlight?: string[];
  scenes: Scene[];
};

export type Timing = {
  /** Frames per scene, including the tail that the next transition overlaps. */
  sceneFrames: number[];
  /** Narration length per scene in seconds, or null when no audio exists yet. */
  audioSeconds: (number | null)[];
  /** Spoken word timings per scene, when narration was transcribed. */
  words: (Word[] | null)[];
};

/**
 * Cut length between scenes, and the silence kept after each line. The tail is longer than
 * the transition so two voices never overlap.
 */
export const pacing = (style: Storyboard["style"]) =>
  style === "pixel" ? { transitionFrames: 10, tailSeconds: 0.45 } : { transitionFrames: 15, tailSeconds: 0.7 };

/** Break-even probability of an American price, vig included. */
export const impliedProbability = (american: number): number =>
  american < 0 ? -american / (-american + 100) : 100 / (american + 100);

export const formatOdds = (american: number): string =>
  american > 0 ? `+${american}` : `−${Math.abs(american)}`;

export const pct = (p: number, digits = 1): string => `${(p * 100).toFixed(digits)}%`;

export const sceneTiming = (
  scenes: Scene[],
  fps: number,
  manifest: Record<string, number> | null,
  words: (Word[] | null)[] = [],
  tailSeconds = 0.7,
): Timing => {
  const audioSeconds = scenes.map((s) => manifest?.[s.id] ?? null);
  const sceneFrames = scenes.map((s, i) => {
    const audio = audioSeconds[i];
    const seconds = audio === null ? (s.seconds ?? 5) : audio + tailSeconds;
    return Math.ceil(seconds * fps);
  });
  return { sceneFrames, audioSeconds, words: scenes.map((_, i) => words[i] ?? null) };
};

export const totalFrames = (sceneFrames: number[], transitionFrames: number): number =>
  sceneFrames.reduce((a, b) => a + b, 0) - transitionFrames * Math.max(0, sceneFrames.length - 1);
