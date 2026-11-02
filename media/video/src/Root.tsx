import { Composition, staticFile, type CalculateMetadataFunction } from "remotion";
import { Explainer, type ExplainerProps } from "./Explainer";
import { sceneTiming, totalFrames, type Storyboard } from "./storyboard";
import trueProbOdds from "../storyboards/true-prob-odds.json";

// Add a storyboard JSON here to register it as a composition.
const STORYBOARDS = [trueProbOdds] as Storyboard[];

const FPS = 30;

const calculateMetadata: CalculateMetadataFunction<ExplainerProps> = async ({ props }) => {
  // narrate.mjs writes {sceneId: seconds}; without it, scenes use their fallback lengths.
  const res = await fetch(staticFile(`narration/${props.storyboard.id}/manifest.json`)).catch(() => null);
  const manifest = res && res.ok ? ((await res.json()) as Record<string, number>) : null;
  const timing = sceneTiming(props.storyboard.scenes, FPS, manifest);
  return { durationInFrames: totalFrames(timing.sceneFrames), props: { ...props, timing } };
};

export const RemotionRoot: React.FC = () => (
  <>
    {STORYBOARDS.map((sb) => (
      <Composition
        key={sb.id}
        id={sb.id}
        component={Explainer}
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
