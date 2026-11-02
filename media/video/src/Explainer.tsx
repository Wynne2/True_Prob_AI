import { Audio } from "@remotion/media";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { Fragment } from "react";
import { AbsoluteFill, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneCaptions } from "./Captions";
import { SceneView } from "./scenes";
import { pacing, type Storyboard, type Timing } from "./storyboard";
import { color, font, safe } from "./theme";

export type ExplainerProps = { storyboard: Storyboard; timing: Timing };

/** Navy floor with faint court markings that drift slowly behind every scene. */
const Court: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(900px 620px at 50% 8%, rgba(255,122,26,0.16), transparent 70%), ${color.bg}`,
      }}
    >
      <svg width="1080" height="1920" viewBox="0 0 1080 1920" style={{ opacity: 0.09, transform: `translateY(${-frame * 0.25}px)` }}>
        <g fill="none" stroke={color.text} strokeWidth="6">
          <circle cx="540" cy="960" r="190" />
          <line x1="0" y1="960" x2="1080" y2="960" />
          <path d="M120 2200 V1700 A420 420 0 0 1 960 1700 V2200" />
          <rect x="350" y="1500" width="380" height="700" />
          <path d="M120 -280 V220 A420 420 0 0 0 960 220 V-280" />
        </g>
      </svg>
    </AbsoluteFill>
  );
};

/** Brand and responsible-gambling line, on screen the whole video. */
const Chrome: React.FC = () => (
  <AbsoluteFill style={{ padding: `${safe.top - 105}px ${safe.side}px 0`, fontFamily: font.body }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <span style={{ fontFamily: font.display, fontWeight: 700, fontSize: 46, color: color.text }}>
        True Prob <span style={{ color: color.court }}>AI</span>
      </span>
      <span
        style={{
          fontSize: 30,
          fontWeight: 600,
          color: color.text,
          padding: "8px 18px",
          borderRadius: 999,
          background: "rgba(14,26,43,0.75)",
          border: `2px solid ${color.line}`,
        }}
      >
        21+ | Gambling problem? 1-800-GAMBLER
      </span>
    </div>
  </AbsoluteFill>
);

export const Explainer: React.FC<ExplainerProps> = ({ storyboard, timing }) => {
  const { fps } = useVideoConfig();
  return (
  <AbsoluteFill style={{ backgroundColor: color.bg }}>
    <Court />
    <TransitionSeries>
      {storyboard.scenes.map((scene, i) => {
        const audio = timing.audioSeconds[i];
        const caption = scene.caption ?? scene.say;
        return (
          <Fragment key={scene.id}>
            {i > 0 ? (
              <TransitionSeries.Transition presentation={fade()} timing={linearTiming({ durationInFrames: pacing(storyboard.style).transitionFrames })} />
            ) : null}
            <TransitionSeries.Sequence name={scene.id} durationInFrames={timing.sceneFrames[i]}>
              <SceneView scene={scene} />
              {audio !== null ? <Audio src={staticFile(`narration/${storyboard.id}/${scene.id}.wav`)} /> : null}
              {caption ? <SceneCaptions text={caption} seconds={audio ?? timing.sceneFrames[i] / fps} /> : null}
            </TransitionSeries.Sequence>
          </Fragment>
        );
      })}
    </TransitionSeries>
    <Chrome />
  </AbsoluteFill>
  );
};
