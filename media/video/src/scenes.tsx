import { ThreeCanvas } from "@remotion/three";
import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig, Easing } from "remotion";
import { formatOdds, impliedProbability, pct, type Scene } from "./storyboard";
import { color, font, safe } from "./theme";

// ---------------------------------------------------------------------------
// Motion helpers: everything is driven by useCurrentFrame() so renders are exact.
// ---------------------------------------------------------------------------

const useEnter = (delay = 0, damping = 16) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - delay, fps, config: { damping, stiffness: 160 } });
};

const Rise: React.FC<{ delay?: number; children: ReactNode; style?: CSSProperties }> = ({ delay = 0, children, style }) => {
  const p = useEnter(delay);
  return (
    <div style={{ opacity: p, transform: `translateY(${(1 - p) * 60}px)`, ...style }}>{children}</div>
  );
};

const ramp = (frame: number, start: number, length: number) =>
  interpolate(frame, [start, start + length], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

const Stage: React.FC<{ children: ReactNode }> = ({ children }) => (
  <AbsoluteFill
    style={{
      padding: `${safe.top}px ${safe.side}px ${safe.bottom + 230}px`,
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      gap: 50,
      fontFamily: font.body,
      color: color.text,
    }}
  >
    {children}
  </AbsoluteFill>
);

const Title: React.FC<{ children: ReactNode; delay?: number; size?: number }> = ({ children, delay = 0, size = 96 }) => (
  <Rise delay={delay} style={{ width: "100%" }}>
    <h1
      style={{
        margin: 0,
        fontFamily: font.display,
        fontWeight: 700,
        fontSize: size,
        lineHeight: 1.02,
        textAlign: "center",
        textWrap: "balance",
      }}
    >
      {children}
    </h1>
  </Rise>
);

// ---------------------------------------------------------------------------
// Hook: a 3D basketball spinning in, with the question underneath.
// ---------------------------------------------------------------------------

const Seam: React.FC<{ radius: number; position?: [number, number, number]; rotation?: [number, number, number] }> = ({
  radius,
  position = [0, 0, 0],
  rotation = [0, 0, 0],
}) => (
  <mesh position={position} rotation={rotation}>
    <torusGeometry args={[radius, 0.028, 16, 160]} />
    <meshStandardMaterial color="#1A0C02" roughness={0.9} />
  </mesh>
);

const Basketball: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const drop = spring({ frame, fps, config: { damping: 11, stiffness: 90 } });
  const R = 1.6;
  const d = 0.72;
  const r = Math.sqrt(R * R - d * d);
  return (
    <ThreeCanvas width={width} height={height} camera={{ position: [0, 0, 11], fov: 40 }}>
      <ambientLight intensity={0.55} />
      <directionalLight position={[4, 6, 6]} intensity={1.6} />
      <pointLight position={[-5, -2, 3]} intensity={30} color={color.court} />
      <group position={[0, 1.55 + (1 - drop) * 4, 0]} rotation={[0.35, frame * 0.045, 0.2]} scale={0.5 + 0.35 * drop}>
        <mesh>
          <sphereGeometry args={[R, 96, 96]} />
          <meshStandardMaterial color="#E8681A" roughness={0.72} />
        </mesh>
        <Seam radius={R * 1.003} rotation={[Math.PI / 2, 0, 0]} />
        <Seam radius={R * 1.003} />
        <Seam radius={r * 1.004} position={[d, 0, 0]} rotation={[0, Math.PI / 2, 0]} />
        <Seam radius={r * 1.004} position={[-d, 0, 0]} rotation={[0, Math.PI / 2, 0]} />
      </group>
    </ThreeCanvas>
  );
};

const HookScene: React.FC<{ scene: Extract<Scene, { type: "hook" }> }> = ({ scene }) => (
  <AbsoluteFill>
    <Basketball />
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        padding: `0 ${safe.side}px ${safe.bottom + 120}px`,
        alignItems: "center",
        gap: 28,
        color: color.text,
      }}
    >
      <Title delay={10} size={124}>
        {scene.title}
      </Title>
      {scene.kicker ? (
        <Rise delay={22}>
          <p style={{ margin: 0, fontFamily: font.body, fontWeight: 500, fontSize: 48, color: color.muted, textAlign: "center" }}>
            {scene.kicker}
          </p>
        </Rise>
      ) : null}
    </AbsoluteFill>
  </AbsoluteFill>
);

// ---------------------------------------------------------------------------
// Odds: the price, the division that turns it into a chance, and a ring gauge.
// ---------------------------------------------------------------------------

const Ring: React.FC<{ value: number; progress: number; size: number; label: string }> = ({ value, progress, size, label }) => {
  const stroke = 34;
  const radius = (size - stroke) / 2;
  const circ = 2 * Math.PI * radius;
  const shown = value * progress;
  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={size / 2} cy={size / 2} r={radius} stroke={color.raised} strokeWidth={stroke} fill="none" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke={color.court}
          strokeWidth={stroke}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={`${circ * shown} ${circ}`}
        />
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", textAlign: "center" }}>
        <div>
          <div style={{ fontFamily: font.display, fontWeight: 700, fontSize: 150, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>
            {pct(shown)}
          </div>
          <div style={{ fontSize: 40, color: color.muted, marginTop: 8 }}>{label}</div>
        </div>
      </div>
    </div>
  );
};

const OddsScene: React.FC<{ scene: Extract<Scene, { type: "odds" }> }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const p = impliedProbability(scene.odds);
  const n = Math.abs(scene.odds);
  const formula = scene.odds < 0 ? `${n} ÷ (${n} + 100)` : `100 ÷ (${n} + 100)`;
  return (
    <Stage>
      <Title>{scene.title}</Title>
      <Rise delay={12}>
        <div style={{ fontFamily: font.display, fontWeight: 700, fontSize: 230, lineHeight: 0.9, color: color.court }}>
          {formatOdds(scene.odds)}
        </div>
      </Rise>
      <Rise delay={34}>
        <div style={{ fontSize: 64, fontWeight: 600, color: color.muted, fontVariantNumeric: "tabular-nums" }}>{formula}</div>
      </Rise>
      <Rise delay={50}>
        <Ring value={p} progress={ramp(frame, 52, 40)} size={470} label="implied chance" />
      </Rise>
    </Stage>
  );
};

// ---------------------------------------------------------------------------
// Vig: both sides of the same bet add up to more than 100%.
// ---------------------------------------------------------------------------

const SideRow: React.FC<{ label: string; odds: number; delay: number; tint: string }> = ({ label, odds, delay, tint }) => (
  <Rise delay={delay} style={{ width: "100%" }}>
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "26px 36px",
        background: color.surface,
        border: `2px solid ${color.line}`,
        borderLeft: `10px solid ${tint}`,
        borderRadius: 22,
        fontSize: 58,
        fontWeight: 600,
      }}
    >
      <span>
        {label} <span style={{ fontFamily: font.display, fontWeight: 700 }}>{formatOdds(odds)}</span>
      </span>
      <span style={{ fontFamily: font.display, fontWeight: 700, fontSize: 76, fontVariantNumeric: "tabular-nums" }}>
        {pct(impliedProbability(odds))}
      </span>
    </div>
  </Rise>
);

const VigScene: React.FC<{ scene: Extract<Scene, { type: "vig" }> }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const [a, b] = scene.sides;
  const pa = impliedProbability(a.odds);
  const pb = impliedProbability(b.odds);
  const total = pa + pb;
  const fill = ramp(frame, 62, 36);
  const scale = 1 / 1.12; // the bar shows 0 to 112% so the overflow has room
  const over = Math.max(0, total - 1);
  return (
    <Stage>
      <Title>{scene.title}</Title>
      <SideRow label={a.label} odds={a.odds} delay={14} tint={color.market} />
      <SideRow label={b.label} odds={b.odds} delay={30} tint={color.edge} />
      <Rise delay={56} style={{ width: "100%" }}>
        <div style={{ position: "relative", height: 74, borderRadius: 999, background: color.raised, overflow: "hidden" }}>
          <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${pa * scale * 100 * fill}%`, background: color.market }} />
          <div
            style={{
              position: "absolute",
              left: `${pa * scale * 100 * fill}%`,
              top: 0,
              bottom: 0,
              width: `${(1 - pa) * scale * 100 * fill}%`,
              background: color.edge,
            }}
          />
          <div
            style={{
              position: "absolute",
              left: `${scale * 100}%`,
              top: 0,
              bottom: 0,
              width: `${over * scale * 100 * ramp(frame, 96, 18)}%`,
              background: color.court,
            }}
          />
        </div>
        <div style={{ position: "relative", height: 70 }}>
          <div style={{ position: "absolute", left: `${scale * 100}%`, top: -88, height: 104, width: 5, marginLeft: -2, background: color.text }} />
          <div style={{ position: "absolute", left: `${scale * 100}%`, transform: "translateX(-50%)", top: 22, fontSize: 38, color: color.muted }}>
            100%
          </div>
        </div>
      </Rise>
      <Rise delay={100}>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontFamily: font.display, fontWeight: 700, fontSize: 170, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>
            {pct(total)}
          </div>
          <div style={{ fontSize: 50, color: color.court, fontWeight: 600 }}>{pct(over)} is the book&rsquo;s cut</div>
        </div>
      </Rise>
    </Stage>
  );
};

// ---------------------------------------------------------------------------
// Model: the inputs, then the same probability meter as the dashboard.
// ---------------------------------------------------------------------------

const ModelScene: React.FC<{ scene: Extract<Scene, { type: "model" }> }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const market = impliedProbability(scene.odds);
  const model = scene.model;
  const grow = ramp(frame, 66, 34);
  const shownModel = Math.min(model, market) * grow + Math.max(0, model - market) * ramp(frame, 96, 20);
  return (
    <Stage>
      <Title>{scene.title}</Title>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 22, justifyContent: "center" }}>
        {scene.factors.map((f, i) => (
          <Rise key={f} delay={16 + i * 10}>
            <span
              style={{
                display: "inline-block",
                padding: "18px 34px",
                borderRadius: 999,
                background: color.surface,
                border: `2px solid ${color.line}`,
                fontSize: 50,
                fontWeight: 600,
              }}
            >
              {f}
            </span>
          </Rise>
        ))}
      </div>
      <Rise delay={58} style={{ width: "100%" }}>
        <div
          style={{
            padding: "44px 44px 40px",
            background: color.surface,
            border: `2px solid ${color.line}`,
            borderRadius: 30,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 30 }}>
            <span style={{ fontSize: 40, color: color.muted }}>{scene.exampleLabel}</span>
            <span style={{ fontFamily: font.display, fontWeight: 700, fontSize: 64 }}>{formatOdds(scene.odds)}</span>
          </div>
          <div style={{ position: "relative", height: 44, borderRadius: 999, background: color.raised }}>
            <div
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                bottom: 0,
                width: `${Math.min(model, market) * grow * 100}%`,
                borderRadius: "999px 0 0 999px",
                background: "linear-gradient(90deg, #1E5E52, #2BA57E)",
              }}
            />
            <div
              style={{
                position: "absolute",
                left: `${market * 100}%`,
                top: 0,
                bottom: 0,
                width: `${(model - market) * 100 * ramp(frame, 96, 20)}%`,
                background: color.edge,
              }}
            />
            <div
              style={{
                position: "absolute",
                left: `${market * 100}%`,
                top: -14,
                bottom: -14,
                width: 9,
                marginLeft: -4,
                borderRadius: 4,
                background: color.market,
                opacity: ramp(frame, 80, 10),
              }}
            />
          </div>
          <div style={{ display: "flex", gap: 44, marginTop: 34, fontSize: 46, color: color.muted, fontVariantNumeric: "tabular-nums" }}>
            <span>
              <b style={{ color: color.edge }}>Model</b> {pct(shownModel)}
            </span>
            <span style={{ opacity: ramp(frame, 80, 10) }}>
              <b style={{ color: color.market }}>Market</b> {pct(market)}
            </span>
          </div>
        </div>
      </Rise>
      <Rise delay={112}>
        <span
          style={{
            display: "inline-block",
            padding: "18px 40px",
            borderRadius: 999,
            background: "rgba(52, 211, 153, 0.16)",
            color: color.edge,
            fontFamily: font.display,
            fontWeight: 700,
            fontSize: 74,
          }}
        >
          {(model - market) * 100 >= 0 ? "+" : ""}
          {((model - market) * 100).toFixed(1)}% edge
        </span>
      </Rise>
    </Stage>
  );
};

// ---------------------------------------------------------------------------
// CTA: the payoff line, what the product does, the follow.
// ---------------------------------------------------------------------------

const CtaScene: React.FC<{ scene: Extract<Scene, { type: "cta" }> }> = ({ scene }) => (
  <Stage>
    <Title size={112}>{scene.title}</Title>
    <div style={{ display: "flex", flexDirection: "column", gap: 26, width: "100%" }}>
      {scene.points.map((point, i) => (
        <Rise key={point} delay={14 + i * 9}>
          <div style={{ display: "flex", alignItems: "center", gap: 26, fontSize: 54, fontWeight: 600 }}>
            <svg width="54" height="54" viewBox="0 0 24 24" aria-hidden>
              <circle cx="12" cy="12" r="11" fill={color.court} />
              <path d="M7 12.5l3.2 3.2L17 9" stroke={color.courtInk} strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {point}
          </div>
        </Rise>
      ))}
    </div>
  </Stage>
);

export const SceneView: React.FC<{ scene: Scene }> = ({ scene }) => {
  switch (scene.type) {
    case "hook":
      return <HookScene scene={scene} />;
    case "odds":
      return <OddsScene scene={scene} />;
    case "vig":
      return <VigScene scene={scene} />;
    case "model":
      return <ModelScene scene={scene} />;
    case "cta":
      return <CtaScene scene={scene} />;
  }
};
