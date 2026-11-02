import { Audio } from "@remotion/media";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { Fragment } from "react";
import { AbsoluteFill, interpolate, staticFile, useVideoConfig } from "remotion";
import { SceneCaptions } from "../Captions";
import type { ExplainerProps } from "../Explainer";
import { pacing } from "../storyboard";
import { safe } from "../theme";
import { pal, pixelFont } from "./art";
import { pixelBlocks } from "./PixelTransition";
import { PixelShotView } from "./shots";

/** Brand and responsible-gambling line in the arcade style, on screen the whole video. */
const PixelChrome: React.FC = () => (
  <AbsoluteFill style={{ padding: `${safe.top - 110}px ${safe.side}px 0`, fontFamily: pixelFont }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ fontSize: 26, color: pal.white, textShadow: `4px 4px 0 ${pal.ink}` }}>
        TRUE PROB <span style={{ color: pal.gold }}>AI</span>
      </span>
      <span style={{ fontSize: 18, color: pal.white, background: pal.ink, padding: "10px 14px", border: `4px solid ${pal.line}` }}>
        21+ | 1-800-GAMBLER
      </span>
    </div>
  </AbsoluteFill>
);

export const PixelVideo: React.FC<ExplainerProps> = ({ storyboard, timing }) => {
  const { fps, durationInFrames } = useVideoConfig();
  const { transitionFrames } = pacing(storyboard.style);
  return (
    <AbsoluteFill style={{ backgroundColor: pal.night }}>
      <TransitionSeries>
        {storyboard.scenes.map((scene, i) => {
          if (scene.type !== "pixel") return null;
          const audio = timing.audioSeconds[i];
          return (
            <Fragment key={scene.id}>
              {i > 0 ? <TransitionSeries.Transition presentation={pixelBlocks()} timing={linearTiming({ durationInFrames: transitionFrames })} /> : null}
              <TransitionSeries.Sequence name={scene.id} durationInFrames={timing.sceneFrames[i]}>
                <PixelShotView shot={scene.shot} words={timing.words[i]} frames={timing.sceneFrames[i]} />
                {audio !== null ? <Audio src={staticFile(`narration/${storyboard.id}/${scene.id}.wav`)} /> : null}
                <SceneCaptions
                  text={scene.caption ?? scene.say}
                  seconds={audio ?? timing.sceneFrames[i] / fps}
                  words={timing.words[i]}
                  highlight={storyboard.highlight}
                  variant="pixel"
                />
              </TransitionSeries.Sequence>
            </Fragment>
          );
        })}
      </TransitionSeries>
      <Audio
        src={staticFile("audio/music/chiptune.wav")}
        loop
        loopVolumeCurveBehavior="extend"
        volume={(f) =>
          interpolate(f, [0, 10, durationInFrames - 30, durationInFrames], [0, 0.16, 0.16, 0], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          })
        }
      />
      <PixelChrome />
    </AbsoluteFill>
  );
};
