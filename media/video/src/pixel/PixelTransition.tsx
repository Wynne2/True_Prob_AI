import type { TransitionPresentation, TransitionPresentationComponentProps } from "@remotion/transitions";
import { AbsoluteFill } from "remotion";
import { BlockWipe, GAME_H, GAME_W, PX } from "./art";

type Props = Record<string, never>;

/**
 * Arcade cut: the outgoing scene fills with blocks, then the incoming scene clears them.
 * Exactly one scene is visible at any moment, so the blocks never show two shots at once.
 */
const PixelBlocks: React.FC<TransitionPresentationComponentProps<Props>> = ({ children, presentationDirection, presentationProgress }) => {
  const p = presentationProgress;
  const exiting = presentationDirection === "exiting";
  if (exiting ? p >= 0.5 : p < 0.5) return null;
  const cover = exiting ? p * 2 : 1 - (p - 0.5) * 2;
  return (
    <AbsoluteFill>
      {children}
      <svg width={GAME_W * PX} height={GAME_H * PX} viewBox={`0 0 ${GAME_W} ${GAME_H}`} shapeRendering="crispEdges" style={{ position: "absolute", inset: 0 }}>
        <BlockWipe progress={cover} seed="cut" />
      </svg>
    </AbsoluteFill>
  );
};

export const pixelBlocks = (): TransitionPresentation<Props> => ({ component: PixelBlocks, props: {} });
