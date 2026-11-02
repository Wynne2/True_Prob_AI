/**
 * Pixel-art component kit. Everything draws in "game pixels" on a 270 x 480 grid that the
 * stage scales 4x to 1080 x 1920 with crisp edges. Sprites are character maps, so all art is
 * original and every frame is deterministic.
 */
import { loadFont as loadPixelify } from "@remotion/google-fonts/PixelifySans";
import { loadFont as loadPressStart } from "@remotion/google-fonts/PressStart2P";
import { Audio } from "@remotion/media";
import { useMemo, type ReactNode } from "react";
import { interpolate, random, Sequence, staticFile, useCurrentFrame } from "remotion";

export const GAME_W = 270;
export const GAME_H = 480;
export const PX = 4;

export const pixelFont = loadPressStart("normal", { weights: ["400"], subsets: ["latin"] }).fontFamily;
export const pixelBody = loadPixelify("normal", { weights: ["500", "700"], subsets: ["latin"] }).fontFamily;

export const pal = {
  night: "#0A1230",
  navy: "#111C44",
  navy2: "#18275C",
  line: "#2C3E86",
  ink: "#070B1C",
  white: "#F4F7FF",
  green: "#39FF7A",
  greenDark: "#14A84A",
  gold: "#FFD23F",
  goldDark: "#C98A00",
  red: "#FF4D5E",
  redDark: "#A3162A",
  blue: "#3DA9FF",
  blueDark: "#1B5FC0",
  purple: "#4A2A8A",
  purpleDark: "#2C1758",
};

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

/** The whole shot is one SVG on the game grid. */
export const Stage: React.FC<{ children: ReactNode; camera?: { x: number; y: number; zoom: number } }> = ({
  children,
  camera = { x: 0, y: 0, zoom: 1 },
}) => (
  <svg
    width={GAME_W * PX}
    height={GAME_H * PX}
    viewBox={`0 0 ${GAME_W} ${GAME_H}`}
    shapeRendering="crispEdges"
    style={{ position: "absolute", inset: 0 }}
  >
    <g
      transform={`translate(${GAME_W / 2} ${GAME_H / 2}) scale(${camera.zoom}) translate(${-GAME_W / 2 - Math.round(camera.x)} ${
        -GAME_H / 2 - Math.round(camera.y)
      })`}
    >
      {children}
    </g>
  </svg>
);

export type Palette = Record<string, string>;

/** Draws a character map; "." is transparent. Horizontal runs merge into one rect. */
export const Sprite: React.FC<{ art: string[]; palette: Palette; x?: number; y?: number; scale?: number; flip?: boolean }> = ({
  art,
  palette,
  x = 0,
  y = 0,
  scale = 1,
  flip = false,
}) => {
  const rects = useMemo(() => {
    const out: ReactNode[] = [];
    art.forEach((row, ry) => {
      let start = 0;
      for (let cx = 1; cx <= row.length; cx++) {
        if (cx === row.length || row[cx] !== row[start]) {
          const ch = row[start];
          if (ch !== "." && palette[ch]) {
            out.push(<rect key={`${ry}-${start}`} x={start} y={ry} width={cx - start} height={1} fill={palette[ch]} />);
          }
          start = cx;
        }
      }
    });
    return out;
  }, [art, palette]);
  const w = art[0].length;
  const t = flip
    ? `translate(${Math.round(x)} ${Math.round(y)}) scale(${-scale} ${scale}) translate(${-w} 0)`
    : `translate(${Math.round(x)} ${Math.round(y)}) scale(${scale})`;
  return <g transform={t}>{rects}</g>;
};

/** Snap a 0..1 progress to whole steps, so motion reads as frame-by-frame pixel animation. */
export const steps = (p: number, n: number) => Math.round(Math.max(0, Math.min(1, p)) * n) / n;

export const prog = (frame: number, start: number, length: number) =>
  interpolate(frame, [start, start + length], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

/** Overshooting pop (0 -> 1.2 -> 1), quantized. */
export const pop = (frame: number, start: number, length = 8) => {
  const p = prog(frame, start, length);
  return steps(p < 0.6 ? (p / 0.6) * 1.2 : 1.2 - ((p - 0.6) / 0.4) * 0.2, 10);
};

/** Integer screen shake that decays after `start`. */
export const shake = (frame: number, start: number, length = 12, amp = 3) => {
  const t = frame - start;
  if (t < 0 || t > length) return { x: 0, y: 0 };
  const k = amp * (1 - t / length);
  return { x: Math.round((random(`sx${start}-${t}`) - 0.5) * 2 * k), y: Math.round((random(`sy${start}-${t}`) - 0.5) * 2 * k) };
};

export const Sfx: React.FC<{ name: string; at: number; volume?: number }> = ({ name, at, volume = 0.8 }) => (
  <Sequence from={Math.max(0, Math.round(at))} layout="none" name={`sfx ${name}`}>
    <Audio src={staticFile(`audio/sfx/${name}.wav`)} volume={volume} />
  </Sequence>
);

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

/** Pixel font text with a hard 1px drop shadow. Pops in from `at` when given. */
export const PixelText: React.FC<{
  x: number;
  y: number;
  size: number;
  color?: string;
  children: ReactNode;
  anchor?: "start" | "middle" | "end";
  at?: number;
  shadow?: string;
  font?: string;
}> = ({ x, y, size, color = pal.white, children, anchor = "middle", at, shadow = pal.ink, font = pixelFont }) => {
  const frame = useCurrentFrame();
  const s = at === undefined ? 1 : pop(frame, at);
  if (s <= 0) return null;
  const common = { fontFamily: font, fontSize: size, textAnchor: anchor, dominantBaseline: "alphabetic" } as const;
  return (
    <g transform={`translate(${x} ${y}) scale(${s}) translate(${-x} ${-y})`}>
      <text x={x + 1} y={y + 1} fill={shadow} {...common}>
        {children}
      </text>
      <text x={x} y={y} fill={color} {...common}>
        {children}
      </text>
    </g>
  );
};

const money = (v: number) =>
  `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const formatMoney = money;

/** Counts from `from` to `to` between frames `start` and `start + length`. */
export const MoneyCounter: React.FC<{
  from: number;
  to: number;
  start: number;
  length: number;
  x: number;
  y: number;
  size: number;
  color?: string;
  prefix?: string;
  format?: (v: number) => string;
  anchor?: "start" | "middle" | "end";
}> = ({ from, to, start, length, x, y, size, color, prefix = "", format = money, anchor }) => {
  const frame = useCurrentFrame();
  const v = from + (to - from) * steps(prog(frame, start, length), 24);
  return (
    <PixelText x={x} y={y} size={size} color={color} anchor={anchor}>
      {prefix}
      {format(v)}
    </PixelText>
  );
};

/** American odds on a flip board: digits drop in one by one with a flip. */
export const AnimatedOdds: React.FC<{ odds: string; x: number; y: number; at: number; size?: number; color?: string }> = ({
  odds,
  x,
  y,
  at,
  size = 20,
  color = pal.gold,
}) => {
  const frame = useCurrentFrame();
  const w = odds.length * size + 8;
  const h = size + 10;
  return (
    <g>
      <rect x={x - w / 2} y={y - h + 4} width={w} height={h} fill={pal.ink} />
      <rect x={x - w / 2 + 1} y={y - h + 5} width={w - 2} height={h - 2} fill="none" stroke={pal.line} strokeWidth={1} />
      {odds.split("").map((ch, i) => {
        const p = steps(prog(frame, at + i * 3, 5), 4);
        if (p <= 0) return null;
        const cx = x - w / 2 + 4 + i * size + size / 2;
        return (
          <g key={i} transform={`translate(0 ${y - size / 2}) scale(1 ${p}) translate(0 ${-(y - size / 2)})`}>
            <PixelText x={cx} y={y} size={size} color={color}>
              {ch}
            </PixelText>
          </g>
        );
      })}
    </g>
  );
};

// ---------------------------------------------------------------------------
// Characters
// ---------------------------------------------------------------------------

export type Mood = "happy" | "shock" | "sad" | "neutral" | "smug";

const HEAD = [
  "....KKKKKK....",
  "...KHHHHHHK...",
  "..KHHHHHHHHK..",
  "..KSSSSSSSSK..",
  "..KSWESSWESK..",
  "..KSSSSSSSSK..",
  "..KSSSSSSSSK..",
  "...KSSSSSSK...",
  "....KKSSKK....",
];
const MOUTH: Record<Mood, [string, string]> = {
  happy: ["MMMMMM", "SMMMMS"],
  shock: ["SSMMSS", "SSMMSS"],
  sad: ["SSSSSS", "SMMMMS"],
  neutral: ["SMMMMS", "SSSSSS"],
  smug: ["SSSSMM", "SMMMMS"],
};
const ARMS_DOWN = [
  "...KKJJJJKK...",
  "..KJJJJJJJJK..",
  ".KSKJJjjJJKSK.",
  ".KSKJJjjJJKSK.",
  ".KKKJJJJJJKKK.",
];
const ARMS_UP = [
  "KSKKJJJJJJKKSK",
  ".KKJJJJJJJJKK.",
  "...KJJjjJJK...",
  "...KJJjjJJK...",
  "...KJJJJJJK...",
];
const LEGS = ["...KPPPPPPK...", "...KPPKKPPK...", "...KPPKKPPK...", "...KPPKKPPK...", "..KLLLKKLLLK..", "..KKKKKKKKKK.."];

const setRange = (row: string, from: number, text: string) => row.slice(0, from) + text + row.slice(from + text.length);

const characterArt = (mood: Mood, armsUp: boolean) => {
  const head = HEAD.map((row, i) => {
    let r = row;
    if (i === 6) r = setRange(r, 4, MOUTH[mood][0]);
    if (i === 7) r = setRange(r, 4, MOUTH[mood][1]);
    if (mood === "shock" && i === 4) r = "..KWEWSSWEWK..";
    if (mood === "sad" && i === 3) r = "..KSKSSSSKSK..";
    if (armsUp && i === 3) r = setRange(setRange(r, 0, ".K"), 12, "K.");
    if (armsUp && i >= 4) r = setRange(setRange(r, 0, "KS"), 12, "SK");
    return r;
  });
  return [...head, ...(armsUp ? ARMS_UP : ARMS_DOWN), ...LEGS];
};

export const CHARACTER_W = 14;
export const CHARACTER_H = 20;

export const PixelCharacter: React.FC<{
  x: number;
  y: number;
  scale?: number;
  jersey: string;
  accent?: string;
  skin?: string;
  hair?: string;
  mood?: Mood;
  armsUp?: boolean;
  flip?: boolean;
  bob?: boolean;
}> = ({ x, y, scale = 3, jersey, accent = pal.white, skin = "#F2B48C", hair = "#3B2414", mood = "neutral", armsUp = false, flip, bob = true }) => {
  const frame = useCurrentFrame();
  const palette = useMemo(
    () => ({ K: pal.ink, S: skin, H: hair, W: pal.white, E: pal.ink, M: "#7A1020", J: jersey, j: accent, P: "#26324F", L: "#E8ECF6" }),
    [skin, hair, jersey, accent],
  );
  const art = useMemo(() => characterArt(mood, armsUp), [mood, armsUp]);
  const dy = bob ? (Math.floor(frame / 8) % 2) * scale : 0;
  return <Sprite art={art} palette={palette} x={x} y={y - dy} scale={scale} flip={flip} />;
};

// ---------------------------------------------------------------------------
// Coins, cash, particles
// ---------------------------------------------------------------------------

const COIN = ["..KKKK..", ".KGGGGK.", "KGYGGGGK", "KGYGDGGK", "KGGDGGGK", "KGGGGGDK", ".KDDDDK.", "..KKKK.."];
const COIN_PAL = { K: "#5A3A00", G: pal.gold, Y: "#FFF3A6", D: pal.goldDark };

/** A spinning gold coin: width steps through 8, 6, 4, 2 pixels. */
export const PixelCoin: React.FC<{ x: number; y: number; scale?: number; spin?: number }> = ({ x, y, scale = 1, spin = 0 }) => {
  const frame = useCurrentFrame();
  const widths = [1, 0.75, 0.5, 0.25, 0.5, 0.75];
  const sx = spin ? widths[Math.floor((frame + spin) / 3) % widths.length] : 1;
  return (
    <g transform={`translate(${Math.round(x + 4 * scale)} ${Math.round(y)}) scale(${sx} 1) translate(${-4 * scale} 0)`}>
      <Sprite art={COIN} palette={COIN_PAL} scale={scale} />
    </g>
  );
};

/** Coins exploding outward from (x, y) with gravity, starting at frame `at`. */
export const CoinBurst: React.FC<{ x: number; y: number; at: number; count?: number; power?: number; seed?: string }> = ({
  x,
  y,
  at,
  count = 18,
  power = 4,
  seed = "burst",
}) => {
  const frame = useCurrentFrame();
  const t = frame - at;
  if (t < 0 || t > 60) return null;
  return (
    <g>
      {Array.from({ length: count }, (_, i) => {
        const a = random(`${seed}a${i}`) * Math.PI * 2;
        const v = power * (0.5 + random(`${seed}v${i}`));
        const px = x + Math.cos(a) * v * t;
        const py = y + Math.sin(a) * v * t - 2.2 * t + 0.16 * t * t;
        return <PixelCoin key={i} x={px} y={py} scale={1 + (i % 2)} spin={i * 2} />;
      })}
    </g>
  );
};

/** Square sparkle particles for small wins and reveals. */
export const Sparkles: React.FC<{ x: number; y: number; at: number; color?: string; count?: number; seed?: string }> = ({
  x,
  y,
  at,
  color = pal.green,
  count = 14,
  seed = "spark",
}) => {
  const frame = useCurrentFrame();
  const t = frame - at;
  if (t < 0 || t > 24) return null;
  return (
    <g>
      {Array.from({ length: count }, (_, i) => {
        const a = (i / count) * Math.PI * 2 + random(`${seed}${i}`);
        const r = 4 + t * (2 + random(`${seed}r${i}`) * 2);
        const size = t < 16 ? 3 : 2;
        return (
          <rect key={i} x={Math.round(x + Math.cos(a) * r)} y={Math.round(y + Math.sin(a) * r)} width={size} height={size} fill={color} />
        );
      })}
    </g>
  );
};

/** A heap of coins and cash, drawn back to front. */
export const CashPile: React.FC<{ x: number; y: number; width: number; height: number; seed?: string }> = ({
  x,
  y,
  width,
  height,
  seed = "pile",
}) => {
  const items = useMemo(() => {
    const out: { x: number; y: number; kind: number }[] = [];
    for (let i = 0; i < Math.round((width * height) / 26); i++) {
      const u = random(`${seed}x${i}`);
      const h = 1 - Math.abs(u - 0.5) * 2; // mound shape
      out.push({ x: x + u * width, y: y + height - random(`${seed}y${i}`) * height * h, kind: Math.floor(random(`${seed}k${i}`) * 3) });
    }
    return out.sort((a, b) => a.y - b.y);
  }, [x, y, width, height, seed]);
  return (
    <g>
      {items.map((it, i) =>
        it.kind === 0 ? (
          <g key={i}>
            <rect x={Math.round(it.x)} y={Math.round(it.y)} width={12} height={6} fill={pal.greenDark} />
            <rect x={Math.round(it.x) + 1} y={Math.round(it.y) + 1} width={10} height={4} fill="#5BE38C" />
            <rect x={Math.round(it.x) + 5} y={Math.round(it.y) + 2} width={2} height={2} fill={pal.greenDark} />
          </g>
        ) : (
          <PixelCoin key={i} x={it.x} y={it.y} />
        ),
      )}
    </g>
  );
};

// ---------------------------------------------------------------------------
// Sets and props
// ---------------------------------------------------------------------------

/** Night sky with a few twinkling stars, and a pixel basketball court floor from `floorY`. */
export const Court: React.FC<{ floorY?: number; width?: number }> = ({ floorY = 300, width = GAME_W }) => {
  const frame = useCurrentFrame();
  const stars = useMemo(
    () => Array.from({ length: 40 }, (_, i) => ({ x: Math.round(random(`st${i}`) * width), y: Math.round(random(`sy${i}`) * floorY * 0.8) })),
    [width, floorY],
  );
  return (
    <g>
      <rect x={-200} y={-200} width={width + 400} height={GAME_H + 400} fill={pal.night} />
      {stars.map((s, i) => (
        <rect key={i} x={s.x} y={s.y} width={1} height={1} fill={(Math.floor(frame / 10) + i) % 7 === 0 ? pal.gold : "#3A4C8F"} />
      ))}
      {Array.from({ length: 12 }, (_, i) => (
        <rect key={i} x={-200} y={floorY + i * 16} width={width + 400} height={16} fill={i % 2 ? "#1B2A63" : "#203272"} />
      ))}
      <rect x={-200} y={floorY} width={width + 400} height={2} fill={pal.blue} />
      <rect x={width / 2 - 1} y={floorY} width={2} height={200} fill={pal.blue} opacity={0.6} />
      <ellipse cx={width / 2} cy={floorY + 40} rx={36} ry={14} fill="none" stroke={pal.blue} strokeWidth={2} opacity={0.6} />
    </g>
  );
};

/** The sportsbook as a building with a face. `mood` drives its eyebrows and door-mouth. */
export const PixelSportsbook: React.FC<{ x: number; y: number; mood?: "smug" | "grin" | "neutral"; look?: number; sign?: string }> = ({
  x,
  y,
  mood = "smug",
  look = 0,
  sign = "SPORTSBOOK",
}) => {
  const frame = useCurrentFrame();
  const W = 120;
  const blink = frame % 70 > 66;
  const eye = (ex: number) => (
    <g>
      <rect x={ex} y={y + 40} width={22} height={18} fill={pal.ink} />
      <rect x={ex + 2} y={y + 42} width={18} height={14} fill={blink ? pal.purple : "#FFF1A8"} />
      {!blink ? <rect x={ex + 8 + look * 4} y={y + 46} width={6} height={6} fill={pal.ink} /> : null}
    </g>
  );
  const brow = (ex: number, inward: 1 | -1) =>
    [0, 1, 2, 3].map((i) => {
      const slope = mood === "neutral" ? 0 : i;
      const col = inward === 1 ? i : 3 - i;
      return <rect key={i} x={ex + col * 6} y={y + 32 + (mood === "neutral" ? 0 : inward === 1 ? slope : 3 - slope)} width={6} height={3} fill={pal.ink} />;
    });
  const doorY = y + 70;
  return (
    <g>
      {/* roof and sign */}
      <rect x={x - 6} y={y - 4} width={W + 12} height={8} fill={pal.goldDark} />
      <rect x={x - 2} y={y - 22} width={W + 4} height={18} fill={pal.ink} />
      <rect x={x} y={y - 20} width={W} height={14} fill={pal.purpleDark} />
      <PixelText x={x + W / 2} y={y - 9} size={8} color={(Math.floor(frame / 12) % 2 ? pal.gold : "#FFE88A")}>
        {sign}
      </PixelText>
      {/* body */}
      <rect x={x} y={y + 4} width={W} height={100} fill={pal.purple} />
      <rect x={x} y={y + 4} width={4} height={100} fill={pal.purpleDark} />
      <rect x={x + W - 4} y={y + 4} width={4} height={100} fill={pal.purpleDark} />
      {Array.from({ length: 6 }, (_, i) => (
        <rect key={i} x={x + 10 + i * 18} y={y + 10} width={8} height={4} fill={(Math.floor(frame / 6) + i) % 3 === 0 ? pal.gold : pal.goldDark} />
      ))}
      {eye(x + 26)}
      {eye(x + 72)}
      {brow(x + 24, 1)}
      {brow(x + 72, -1)}
      {/* door mouth */}
      {mood === "neutral" ? (
        <rect x={x + 44} y={doorY + 8} width={32} height={6} fill={pal.ink} />
      ) : (
        <g>
          <rect x={x + 30} y={doorY} width={60} height={4} fill={pal.ink} />
          <rect x={x + 34} y={doorY + 4} width={52} height={mood === "grin" ? 14 : 8} fill={pal.ink} />
          <rect x={x + 40} y={doorY + (mood === "grin" ? 18 : 12)} width={40} height={4} fill={pal.ink} />
          {[0, 1, 2, 3, 4].map((i) => (
            <rect key={i} x={x + 38 + i * 9} y={doorY + 4} width={6} height={5} fill={pal.white} />
          ))}
          {mood === "smug" ? <rect x={x + 86} y={doorY - 4} width={6} height={4} fill={pal.ink} /> : null}
        </g>
      )}
      <rect x={x - 6} y={y + 104} width={W + 12} height={6} fill={pal.goldDark} />
    </g>
  );
};

/** A paper betting slip. `result` stamps it WIN or LOSS. */
export const BettingSlip: React.FC<{
  x: number;
  y: number;
  odds: string;
  stake?: string;
  label?: string;
  result?: "win" | "loss";
  scale?: number;
}> = ({ x, y, odds, stake = "$100", label = "BET SLIP", result, scale = 1 }) => (
  <g transform={`translate(${Math.round(x)} ${Math.round(y)}) scale(${scale})`}>
    <rect x={0} y={0} width={44} height={56} fill={pal.ink} />
    <rect x={1} y={1} width={42} height={54} fill="#EEF1FA" />
    <rect x={1} y={1} width={42} height={10} fill={result === "loss" ? pal.red : result === "win" ? pal.greenDark : pal.blueDark} />
    <PixelText x={22} y={9} size={5} shadow="transparent">
      {label}
    </PixelText>
    <PixelText x={22} y={26} size={9} color={pal.ink} shadow="transparent">
      {odds}
    </PixelText>
    <rect x={6} y={31} width={32} height={1} fill="#9AA6C8" />
    <PixelText x={22} y={44} size={6} color={pal.ink} shadow="transparent">
      {stake}
    </PixelText>
    {[48, 51].map((ly) => (
      <rect key={ly} x={8} y={ly} width={28} height={1} fill="#C5CDE3" />
    ))}
  </g>
);

/** Two-sided bar with a label on each side. */
export const ProbabilityMeter: React.FC<{
  x: number;
  y: number;
  width: number;
  left: number;
  leftLabel: string;
  rightLabel: string;
  leftColor?: string;
  rightColor?: string;
  fill?: number;
}> = ({ x, y, width, left, leftLabel, rightLabel, leftColor = pal.green, rightColor = pal.blue, fill = 1 }) => {
  const lw = Math.round(width * left * fill);
  const rw = Math.round(width * (1 - left) * fill);
  return (
    <g>
      <rect x={x - 2} y={y - 2} width={width + 4} height={18} fill={pal.ink} />
      <rect x={x} y={y} width={width} height={14} fill={pal.navy2} />
      <rect x={x} y={y} width={lw} height={14} fill={leftColor} />
      <rect x={x + width - rw} y={y} width={rw} height={14} fill={rightColor} />
      <rect x={x} y={y} width={width} height={2} fill="#FFFFFF" opacity={0.25} />
      <rect x={x + width / 2 - 1} y={y - 4} width={2} height={22} fill={pal.white} />
      <PixelText x={x} y={y + 30} size={9} anchor="start" color={leftColor}>
        {leftLabel}
      </PixelText>
      <PixelText x={x + width} y={y + 30} size={9} anchor="end" color={rightColor}>
        {rightLabel}
      </PixelText>
    </g>
  );
};

/** Retro LED scoreboard panel with a title strip. */
export const PixelScoreboard: React.FC<{ x: number; y: number; width: number; height: number; title: string; children?: ReactNode }> = ({
  x,
  y,
  width,
  height,
  title,
  children,
}) => (
  <g>
    <rect x={x - 3} y={y - 3} width={width + 6} height={height + 6} fill={pal.goldDark} />
    <rect x={x} y={y} width={width} height={height} fill={pal.ink} />
    <rect x={x} y={y} width={width} height={12} fill={pal.navy2} />
    <PixelText x={x + width / 2} y={y + 9} size={6} color={pal.gold}>
      {title}
    </PixelText>
    {children}
  </g>
);

export const WarningIcon: React.FC<{ x: number; y: number; at: number }> = ({ x, y, at }) => {
  const frame = useCurrentFrame();
  if (frame < at) return null;
  const on = Math.floor((frame - at) / 6) % 2 === 0;
  const art = ["......KK......", ".....KYYK.....", "....KYYYYK....", "....KYKKYK....", "...KYYKKYYK...", "...KYYKKYYK...", "..KYYYKKYYYK..", "..KYYYYYYYYK..", ".KYYYYKKYYYYK.", ".KYYYYKKYYYYK.", "KYYYYYYYYYYYYK", "KKKKKKKKKKKKKK"];
  return <Sprite art={art} palette={{ K: pal.ink, Y: on ? pal.gold : pal.red }} x={x} y={y} scale={2} />;
};

/** Block-by-block screen wipe used as the arcade ending. */
export const BlockWipe: React.FC<{ progress: number; color?: string; seed?: string }> = ({ progress, color = pal.ink, seed = "wipe" }) => {
  if (progress <= 0) return null;
  const size = 18;
  const cols = Math.ceil(GAME_W / size);
  const rows = Math.ceil(GAME_H / size);
  const cells: ReactNode[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (random(`${seed}${r}-${c}`) < progress) cells.push(<rect key={`${r}-${c}`} x={c * size} y={r * size} width={size} height={size} fill={color} />);
    }
  }
  return <g>{cells}</g>;
};
