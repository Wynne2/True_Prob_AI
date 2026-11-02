import { Composition, staticFile, type CalculateMetadataFunction } from "remotion";
import { Explainer, type ExplainerProps } from "./Explainer";
import { PixelVideo } from "./pixel/PixelVideo";
import { pacing, sceneTiming, totalFrames, type Storyboard, type Word } from "./storyboard";
import houseEdge from "../storyboards/house-edge.json";
import trueProbOdds from "../storyboards/true-prob-odds.json";

// Add a storyboard JSON here to register it as a composition.
const STORYBOARDS = [trueProbOdds, houseEdge] as Storyboard[];

const FPS = 30;

const fetchJson = async <T,>(path: string): Promise<T | null> => {
  const res = await fetch(staticFile(path)).catch(() => null);
  return res && res.ok ? ((await res.json()) as T) : null;
};

const calculateMetadata: CalculateMetadataFunction<ExplainerProps> = async ({ props }) => {
  const { id, scenes, style } = props.storyboard;
  // narrate.mjs writes {sceneId: seconds} and per-scene word timings; without them, scenes use fallback lengths.
  const manifest = await fetchJson<Record<string, number>>(`narration/${id}/manifest.json`);
  const words = manifest ? await Promise.all(scenes.map((s) => fetchJson<Word[]>(`narration/${id}/${s.id}.words.json`))) : [];
  const { tailSeconds, transitionFrames } = pacing(style);
  const timing = sceneTiming(scenes, FPS, manifest, words, tailSeconds);
  return { durationInFrames: totalFrames(timing.sceneFrames, transitionFrames), props: { ...props, timing } };
};

export const RemotionRoot: React.FC = () => (
  <>
    {STORYBOARDS.map((sb) => (
      <Composition
        key={sb.id}
        id={sb.id}
        component={sb.style === "pixel" ? PixelVideo : Explainer}
        width={1080}
        height={1920}
        fps={FPS}
        durationInFrames={FPS * 30}
        defaultProps={{ storyboard: sb, timing: sceneTiming(sb.scenes, FPS, null) }}
        calculateMetadata={calculateMetadata}
      />
    ))}
  </>
);
