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
  | (SceneBase & { type: "cta"; title: string; points: string[] });

export type Storyboard = {
  id: string;
  title: string;
  voice: string;
  voiceInstructions?: string;
  scenes: Scene[];
};

export type Timing = {
  /** Frames per scene, including the tail that the next transition overlaps. */
  sceneFrames: number[];
  /** Narration length per scene in seconds, or null when no audio exists yet. */
  audioSeconds: (number | null)[];
};

export const TRANSITION_FRAMES = 15;
/** Silence kept after each line. Longer than a transition so voices never overlap. */
export const TAIL_SECONDS = 0.7;

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
): Timing => {
  const audioSeconds = scenes.map((s) => manifest?.[s.id] ?? null);
  const sceneFrames = scenes.map((s, i) => {
    const audio = audioSeconds[i];
    const seconds = audio === null ? (s.seconds ?? 5) : audio + TAIL_SECONDS;
    return Math.ceil(seconds * fps);
  });
  return { sceneFrames, audioSeconds };
};

export const totalFrames = (sceneFrames: number[]): number =>
  sceneFrames.reduce((a, b) => a + b, 0) - TRANSITION_FRAMES * Math.max(0, sceneFrames.length - 1);
