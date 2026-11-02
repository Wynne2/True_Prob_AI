/**
 * The six shots of "How the house edge works". Every beat is cued to a spoken word from the
 * narration's transcript (with a fallback time), and every number comes from `math` below.
 */
import { useCurrentFrame, useVideoConfig } from "remotion";
import type { PixelShot, Word } from "../storyboard";
import {
  AnimatedOdds,
  BettingSlip,
  BlockWipe,
  CashPile,
  CoinBurst,
  Court,
  formatMoney,
  GAME_W,
  MoneyCounter,
  pal,
  PixelCharacter,
  PixelCoin,
  PixelScoreboard,
  PixelSportsbook,
  PixelText,
  pop,
  ProbabilityMeter,
  prog,
  Sfx,
  shake,
  Sparkles,
  Stage,
  steps,
  WarningIcon,
} from "./art";

// ---------------------------------------------------------------------------
// The math, computed once. A $100 bet at -110 and at a fair even-money price.
// ---------------------------------------------------------------------------
const STAKE = 100;
const ODDS = -110;
const profitAt = (american: number, stake: number) => (american < 0 ? (stake * 100) / -american : (stake * american) / 100);
export const math = (() => {
  const fairProfit = profitAt(100, STAKE); // 100
  const viggedProfit = profitAt(ODDS, STAKE); // 90.909...
  const breakEven = -ODDS / (-ODDS + 100); // 110 / 210
  const bets = 100;
  const wins = bets / 2;
  const winTotal = wins * viggedProfit; // 4545.45
  const lossTotal = (bets - wins) * STAKE; // 5000
  return { fairProfit, viggedProfit, cut: fairProfit - viggedProfit, breakEven, bets, wins, winTotal, lossTotal, net: winTotal - lossTotal };
})();
const pctText = (p: number) => `${(p * 100).toFixed(2)}%`;

type ShotProps = { words: Word[] | null; frames: number };

/** Frame of the first spoken word matching `re` (after `after` seconds), or the fallback. */
const useCue = (words: Word[] | null) => {
  const { fps } = useVideoConfig();
  return (re: RegExp, fallbackSeconds: number, after = 0) => {
    const w = words?.find((x) => x.start >= after && re.test(x.word.toLowerCase()));
    return Math.round((w ? w.start : fallbackSeconds) * fps);
  };
};

const ease = (frame: number, start: number, length: number, n = 8) => steps(prog(frame, start, length), n);

// ---------------------------------------------------------------------------
// 1. Hook: a winner celebrates, then the camera whips to the sportsbook's money mountain.
// ---------------------------------------------------------------------------
const Hook: React.FC<ShotProps> = ({ words }) => {
  const frame = useCurrentFrame();
  const cue = useCue(words);
  const pan = cue(/sportsbook/, 1.1);
  const money = cue(/money/, 2.4);
  const question = cue(/even/, 3.3);
  const camX = 300 * ease(frame, pan, 14, 14);
  const s = shake(frame, money, 14, 4);
  const shook = shake(frame, question, 10, 2);
  return (
    <>
      <Stage camera={{ x: camX + s.x + shook.x, y: s.y + shook.y, zoom: 1 }}>
        <Court floorY={300} width={600} />
        {/* the winner */}
        <PixelCharacter x={102} y={196} scale={5} jersey={pal.gold} accent={pal.goldDark} mood="happy" armsUp={frame % 16 < 8} bob={false} />
        <BettingSlip x={44} y={228} odds="WIN!" label="TICKET" result="win" />
        <CoinBurst x={135} y={200} at={2} count={22} power={3.2} seed="hook" />
        <PixelText x={135} y={120} size={20} color={pal.green} at={4}>
          WINNER!
        </PixelText>
        {/* the house */}
        <CashPile x={286} y={66} width={300} height={232} seed="housepile" />
        <g transform="translate(345 150) scale(1.5) translate(-345 -150)">
          <PixelSportsbook x={345} y={150} mood={frame > money ? "grin" : "smug"} look={-1} />
        </g>
      </Stage>
      <Stage>
        {frame >= question ? (
          <>
            <rect x={14} y={74} width={242} height={48} fill={pal.ink} opacity={0.88} />
            <PixelText x={GAME_W / 2} y={92} size={14} color={pal.gold} at={question}>
              THE HOUSE ALWAYS
            </PixelText>
            <PixelText x={GAME_W / 2} y={114} size={14} color={pal.gold} at={question + 3}>
              GETS PAID?
            </PixelText>
          </>
        ) : null}
      </Stage>
      <Sfx name="jackpot" at={0} />
      <Sfx name="coin" at={6} volume={0.5} />
      <Sfx name="scratch" at={pan} />
      <Sfx name="whoosh" at={pan + 2} volume={0.6} />
      <Sfx name="thud" at={money} />
      <Sfx name="blip" at={question} />
    </>
  );
};

// ---------------------------------------------------------------------------
// 2. The fair bet: 50/50, a $100 coin on the table, $200 back.
// ---------------------------------------------------------------------------
const Fair: React.FC<ShotProps> = ({ words }) => {
  const frame = useCurrentFrame();
  const cue = useCue(words);
  const meter = cue(/^50/, 2.1);
  const bet = cue(/fair/, 4.0);
  const pays = cue(/pays/, 4.4);
  const profit = cue(/profit/, 5.5);
  const wager = cue(/wager/, 7.5);
  const coinT = ease(frame, pays, 10, 10);
  const coinX = 80 + (127 - 80) * coinT;
  const coinY = 246 - Math.sin(coinT * Math.PI) * 36 + (220 - 246) * coinT;
  return (
    <>
      <Stage>
        <Court floorY={280} />
        {frame < wager - 6 ? (
          <PixelText x={GAME_W / 2} y={84} size={11} color={pal.white} at={0}>
            A FAIR GAME
          </PixelText>
        ) : (
          <PixelText x={GAME_W / 2} y={84} size={7} color={pal.gold} at={wager - 6}>
            $100 BACK + $100 WON = $200
          </PixelText>
        )}
        <PixelCharacter x={30} y={94} scale={4} jersey={pal.green} accent={pal.greenDark} mood="neutral" />
        <PixelCharacter x={184} y={94} scale={4} jersey={pal.blue} accent={pal.blueDark} mood="neutral" flip />
        <PixelText x={GAME_W / 2} y={142} size={16} color={pal.gold} at={6}>
          VS
        </PixelText>
        <PixelText x={58} y={188} size={7} color={pal.green}>
          TEAM A
        </PixelText>
        <PixelText x={212} y={188} size={7} color={pal.blue}>
          TEAM B
        </PixelText>
        {frame >= meter - 8 ? (
          <ProbabilityMeter x={35} y={194} width={200} left={0.5} leftLabel="50%" rightLabel="50%" fill={ease(frame, meter - 8, 16, 10)} />
        ) : null}
        {/* the table */}
        <rect x={95} y={236} width={80} height={8} fill={pal.goldDark} />
        <rect x={99} y={244} width={4} height={30} fill={pal.goldDark} />
        <rect x={167} y={244} width={4} height={30} fill={pal.goldDark} />
        {frame >= bet ? <PixelCharacter x={58} y={236} scale={2} jersey={pal.gold} accent={pal.goldDark} mood={frame >= profit ? "happy" : "neutral"} armsUp={frame >= profit} /> : null}
        {frame >= bet ? <PixelCoin x={coinX} y={coinY} scale={2} spin={coinT < 1 ? 1 : 0} /> : null}
        {frame >= pays + 10 ? (
          <>
            <PixelText x={135} y={268} size={6} color={pal.white} at={pays + 10}>
              $100 BET
            </PixelText>
          </>
        ) : null}
      </Stage>
      <Stage>
        {frame >= profit ? (
          <g>
            <rect x={30} y={290} width={210} height={34} fill={pal.ink} opacity={0.85} />
            <PixelText x={GAME_W / 2} y={316} size={18} color={pal.green} at={profit}>
              +$100 PROFIT
            </PixelText>
          </g>
        ) : null}
        <Sparkles x={135} y={306} at={profit} />
      </Stage>
      <Sfx name="blip" at={0} volume={0.5} />
      <Sfx name="blip" at={meter} />
      <Sfx name="coin" at={pays + 10} />
      <Sfx name="powerup" at={profit} />
      <Sfx name="coin" at={profit + 3} volume={0.5} />
    </>
  );
};

// ---------------------------------------------------------------------------
// 3. The trick: the book rolls in, posts -110, and the $100 profit shrinks to $90.91.
// ---------------------------------------------------------------------------
const Trick: React.FC<ShotProps> = ({ words }) => {
  const frame = useCurrentFrame();
  const cue = useCue(words);
  const hidden = cue(/hidden/, 2.3);
  const odds = cue(/odds/, 3.7);
  const winning = cue(/winning/, 5.3);
  const only = cue(/only/, 6.7);
  const cents = cue(/^91|cents|ninety-one/, 7.9, 7.4);
  const enter = 1 - ease(frame, 0, 14, 14);
  const s = shake(frame, cents, 12, 3);
  const shrink = ease(frame, only, 22, 22);
  const stack = Math.round(10 - shrink * 1); // ten coins become nine
  return (
    <>
      <Stage camera={{ x: s.x, y: s.y, zoom: 1 }}>
        <Court floorY={300} />
        <g transform={`translate(${Math.round(enter * 220)} 0)`}>
          <PixelSportsbook x={75} y={86} mood={frame >= hidden ? "grin" : "smug"} look={frame >= only ? -1 : 0} />
        </g>
        {frame >= odds ? <AnimatedOdds odds="-110" x={GAME_W / 2} y={232} at={odds} size={22} /> : null}
        {frame >= winning ? (
          <PixelScoreboard x={40} y={244} width={190} height={52} title="YOUR PROFIT IF YOU WIN">
            <MoneyCounter from={math.fairProfit} to={math.viggedProfit} start={only} length={22} x={102} y={284} size={16} color={pal.gold} />
            {Array.from({ length: stack }, (_, i) => (
              <PixelCoin key={i} x={196} y={286 - i * 3} />
            ))}
          </PixelScoreboard>
        ) : null}
      </Stage>
      <Stage>
        {frame >= cents ? (
          <>
            <g transform={`translate(0 ${Math.round(-6 * (1 - ease(frame, cents, 6)))})`}>
              <rect x={186} y={196} width={64} height={24} fill={pal.ink} />
              <rect x={188} y={198} width={60} height={20} fill={pal.redDark} />
              <PixelText x={218} y={213} size={9} color={pal.white} at={cents}>
                -$9.09
              </PixelText>
            </g>
            <PixelText x={GAME_W / 2} y={312} size={6} color={pal.white} at={cents + 10}>
              LESS PROFIT WHEN YOU WIN,
            </PixelText>
            <PixelText x={GAME_W / 2} y={324} size={6} color={pal.white} at={cents + 12}>
              NOT A FEE ON EVERY BET.
            </PixelText>
          </>
        ) : null}
      </Stage>
      <Sfx name="whoosh" at={0} />
      <Sfx name="thud" at={13} volume={0.6} />
      <Sfx name="blip" at={hidden} />
      <Sfx name="pixel" at={odds} />
      <Sfx name="coin" at={winning} volume={0.5} />
      <Sfx name="shrink" at={only} />
      <Sfx name="sad" at={cents} />
    </>
  );
};

// ---------------------------------------------------------------------------
// 4. The math: both sides at -110 need 52.38% to break even; a fair game gives 50%.
// ---------------------------------------------------------------------------
const MathShot: React.FC<ShotProps> = ({ words }) => {
  const frame = useCurrentFrame();
  const cue = useCue(words);
  const each = cue(/each/, 3.0);
  const about = cue(/about/, 4.1);
  const breakEven = cue(/break/, 7.5);
  const truth = cue(/true/, 8.9);
  const neither = cue(/neither/, 10.8);
  const counter = 0.5 + (math.breakEven - 0.5) * ease(frame, about, 24, 24);
  const glow = frame >= each && Math.floor((frame - each) / 6) % 2 === 0;
  return (
    <>
      <Stage>
        <Court floorY={330} />
        <rect x={GAME_W / 2 - 1} y={64} width={2} height={100} fill={pal.line} />
        {/* Team A side */}
        <PixelCharacter x={18} y={78} scale={3} jersey={pal.green} accent={pal.greenDark} mood={frame >= neither ? "sad" : "neutral"} />
        <g transform={`translate(0 ${Math.round(-12 * (1 - ease(frame, 4, 8)))})`}>
          <BettingSlip x={66} y={80} odds="-110" label="TEAM A" />
        </g>
        {/* Team B side */}
        <PixelCharacter x={210} y={78} scale={3} jersey={pal.blue} accent={pal.blueDark} mood={frame >= neither ? "sad" : "neutral"} flip />
        <g transform={`translate(0 ${Math.round(-12 * (1 - ease(frame, 8, 8)))})`}>
          <BettingSlip x={160} y={80} odds="-110" label="TEAM B" />
        </g>
        {glow ? (
          <>
            <rect x={64} y={78} width={48} height={60} fill="none" stroke={pal.gold} strokeWidth={2} />
            <rect x={158} y={78} width={48} height={60} fill="none" stroke={pal.gold} strokeWidth={2} />
          </>
        ) : null}
        {/* break-even counter */}
        {frame >= about ? (
          <>
            <PixelText x={GAME_W / 2} y={170} size={7} color={pal.white}>
              EACH SIDE MUST WIN
            </PixelText>
            <PixelText x={GAME_W / 2} y={196} size={22} color={pal.gold}>
              {pctText(counter)}
            </PixelText>
          </>
        ) : null}
        {frame >= breakEven - 10 ? (
          <PixelScoreboard x={34} y={208} width={202} height={44} title={`BREAK-EVEN: ${pctText(math.breakEven)}`}>
            <PixelText x={135} y={232} size={7} color={pal.white} at={breakEven - 6}>
              RISK $110 TO WIN $100
            </PixelText>
            <PixelText x={135} y={246} size={7} color={pal.gold} at={breakEven}>
              110 / 210 = {pctText(math.breakEven)}
            </PixelText>
          </PixelScoreboard>
        ) : null}
        {frame >= truth ? (
          <g>
            <rect x={33} y={266} width={204} height={16} fill={pal.ink} />
            {/* axis runs 40% to 60%, so 1 point = 10 px */}
            <rect x={35} y={268} width={100} height={12} fill={pal.green} />
            <rect x={135} y={268} width={Math.round(1000 * (math.breakEven - 0.5) * ease(frame, truth + 6, 10))} height={12} fill={pal.red} />
            <rect x={35 + Math.round(1000 * (math.breakEven - 0.4)) - 1} y={262} width={2} height={24} fill={pal.gold} />
            <PixelText x={35} y={259} size={5} anchor="start" color={pal.white}>
              40%
            </PixelText>
            <PixelText x={235} y={259} size={5} anchor="end" color={pal.white}>
              60%
            </PixelText>
            <PixelText x={36} y={298} size={6} anchor="start" color={pal.green}>
              TRUE ODDS 50%
            </PixelText>
            <PixelText x={236} y={298} size={6} anchor="end" color={pal.gold}>
              NEED 52.38%
            </PixelText>
          </g>
        ) : null}
        <WarningIcon x={121} y={300} at={neither} />
      </Stage>
      <Sfx name="pixel" at={4} volume={0.5} />
      <Sfx name="pixel" at={8} volume={0.5} />
      {Array.from({ length: 8 }, (_, i) => (
        <Sfx key={i} name="tick" at={about + i * 3} volume={0.5} />
      ))}
      <Sfx name="blip" at={breakEven} />
      <Sfx name="blip" at={truth + 6} />
      <Sfx name="warning" at={neither} />
    </>
  );
};

// ---------------------------------------------------------------------------
// 5. The long run: 100 slips flip to 50 wins and 50 losses; the bankroll shrinks.
// ---------------------------------------------------------------------------
const outcomes = Array.from({ length: 100 }, (_, i) => i).sort((a, b) => ((a * 37) % 101) - ((b * 37) % 101)); // fixed shuffle
const LongRun: React.FC<ShotProps> = ({ words }) => {
  const frame = useCurrentFrame();
  const cue = useCue(words);
  const lose = cue(/lose/, 4.1);
  const total = cue(/^455|four/, 4.6, 4.2);
  const average = cue(/average/, 6.0);
  const shown = Math.floor(100 * ease(frame, 2, 52, 50));
  const bank = 10000 + math.net * ease(frame, total, 20, 20);
  return (
    <>
      <Stage>
        <Court floorY={340} />
        <PixelText x={GAME_W / 2} y={76} size={8} color={pal.white} at={0}>
          100 BETS x $100 AT -110
        </PixelText>
        {outcomes.map((rank, i) => {
          if (rank >= shown) return null;
          const col = i % 10;
          const row = Math.floor(i / 10);
          const win = i % 2 === 0;
          return (
            <g key={i}>
              <rect x={30 + col * 21} y={84 + row * 12} width={18} height={10} fill={win ? pal.greenDark : pal.redDark} />
              <rect x={31 + col * 21} y={85 + row * 12} width={16} height={8} fill={win ? pal.green : pal.red} />
              <rect x={34 + col * 21} y={88 + row * 12} width={10} height={2} fill={pal.ink} />
            </g>
          );
        })}
        {frame >= lose - 12 ? (
          <PixelScoreboard x={30} y={212} width={210} height={58} title="50 WINS, 50 LOSSES">
            <PixelText x={36} y={236} size={7} anchor="start" color={pal.green}>
              WINS
            </PixelText>
            <MoneyCounter from={0} to={math.winTotal} start={lose - 12} length={14} x={234} y={236} size={8} color={pal.green} anchor="end" prefix="+" />
            <PixelText x={36} y={250} size={7} anchor="start" color={pal.red}>
              LOSSES
            </PixelText>
            <MoneyCounter from={0} to={-math.lossTotal} start={lose - 6} length={14} x={234} y={250} size={8} color={pal.red} anchor="end" />
            <rect x={36} y={254} width={198} height={1} fill={pal.line} />
            {frame >= total ? (
              <>
                <PixelText x={36} y={266} size={8} anchor="start" color={pal.white}>
                  NET
                </PixelText>
                <PixelText x={234} y={266} size={9} anchor="end" color={pal.red} at={total}>
                  {formatMoney(math.net)}
                </PixelText>
              </>
            ) : null}
          </PixelScoreboard>
        ) : null}
        {frame >= total ? (
          <g>
            <PixelText x={30} y={288} size={6} anchor="start" color={pal.gold}>
              BANKROLL
            </PixelText>
            <PixelText x={240} y={288} size={6} anchor="end" color={pal.white}>
              {formatMoney(bank)}
            </PixelText>
            <rect x={28} y={292} width={214} height={12} fill={pal.ink} />
            <rect x={30} y={294} width={Math.round(210 * (bank / 10000))} height={8} fill={pal.gold} />
            <rect x={30 + Math.round(210 * (bank / 10000))} y={294} width={210 - Math.round(210 * (bank / 10000))} height={8} fill={pal.red} />
          </g>
        ) : null}
        {frame >= average ? (
          <>
            <PixelText x={GAME_W / 2} y={316} size={6} color={pal.white} at={average}>
              ILLUSTRATION: AVERAGE RESULT.
            </PixelText>
            <PixelText x={GAME_W / 2} y={328} size={6} color={pal.white} at={average + 2}>
              REAL RESULTS VARY.
            </PixelText>
          </>
        ) : null}
      </Stage>
      {Array.from({ length: 14 }, (_, i) => (
        <Sfx key={i} name="tick" at={2 + i * 4} volume={0.45} />
      ))}
      <Sfx name="coin" at={lose - 12} volume={0.5} />
      <Sfx name="shrink" at={total} />
      <Sfx name="thud" at={total + 4} volume={0.5} />
    </>
  );
};

// ---------------------------------------------------------------------------
// 6. Takeaway: zoom out on the book and its odds board, the last coin lands, block wipe out.
// ---------------------------------------------------------------------------
const Takeaway: React.FC<ShotProps> = ({ words, frames }) => {
  const frame = useCurrentFrame();
  const cue = useCue(words);
  const edge = cue(/edge/, 1.0);
  const line = cue(/odds/, 2.2);
  const coin = cue(/before/, 4.0);
  const zoom = 1.6 - 0.6 * ease(frame, 0, 24, 12);
  const fall = ease(frame, coin, 10, 10);
  const wipe = steps(prog(frame, frames - 12, 12), 6);
  return (
    <>
      <Stage camera={{ x: 0, y: 40 * (1 - ease(frame, 0, 24, 12)), zoom }}>
        <Court floorY={316} />
        <CashPile x={24} y={120} width={222} height={152} seed="endpile" />
        <PixelSportsbook x={75} y={172} mood="grin" look={frame % 60 < 30 ? -1 : 1} />
        {/* odds board above the sign */}
        <rect x={68} y={114} width={134} height={30} fill={pal.ink} />
        <rect x={70} y={116} width={130} height={26} fill={pal.navy} />
        <PixelText x={102} y={135} size={10} color={frame >= edge && Math.floor(frame / 8) % 2 ? pal.white : pal.gold}>
          -110
        </PixelText>
        <PixelText x={168} y={135} size={10} color={frame >= edge && Math.floor(frame / 8) % 2 ? pal.gold : pal.white}>
          -110
        </PixelText>
        <rect x={134} y={118} width={2} height={22} fill={pal.line} />
        {/* the counter */}
        <rect x={40} y={284} width={190} height={8} fill={pal.gold} />
        <rect x={44} y={292} width={182} height={24} fill={pal.goldDark} />
        <PixelCharacter x={16} y={262} scale={2} jersey={pal.green} accent={pal.greenDark} mood="shock" />
        <PixelCharacter x={226} y={262} scale={2} jersey={pal.blue} accent={pal.blueDark} mood="shock" flip />
        {frame >= coin ? <PixelCoin x={127} y={150 + 118 * fall} scale={2} spin={fall < 1 ? 1 : 0} /> : null}
      </Stage>
      <Stage>
        {frame >= line ? (
          <>
            <PixelText x={GAME_W / 2} y={76} size={12} color={pal.gold} at={line}>
              THE EDGE IS
            </PixelText>
            <PixelText x={GAME_W / 2} y={92} size={12} color={pal.gold} at={line + 4}>
              BUILT INTO
            </PixelText>
            <PixelText x={GAME_W / 2} y={108} size={12} color={pal.gold} at={line + 8}>
              THE ODDS.
            </PixelText>
          </>
        ) : null}
        <Sparkles x={135} y={276} at={coin + 10} color={pal.gold} />
        {frame >= coin + 16 ? (
          <PixelText x={GAME_W / 2} y={330} size={6} color={pal.white} at={coin + 16}>
            KNEW THIS? TELL ME BELOW.
          </PixelText>
        ) : null}
        <BlockWipe progress={wipe} seed="end" />
      </Stage>
      <Sfx name="whoosh" at={0} volume={0.5} />
      <Sfx name="powerup" at={edge} volume={0.6} />
      <Sfx name="blip" at={line} />
      <Sfx name="coin" at={coin + 10} />
      <Sfx name="thud" at={coin + 10} volume={0.6} />
      <Sfx name="pixel" at={frames - 12} />
    </>
  );
};

export const PixelShotView: React.FC<{ shot: PixelShot } & ShotProps> = ({ shot, ...props }) => {
  switch (shot) {
    case "hook":
      return <Hook {...props} />;
    case "fair":
      return <Fair {...props} />;
    case "trick":
      return <Trick {...props} />;
    case "math":
      return <MathShot {...props} />;
    case "longrun":
      return <LongRun {...props} />;
    case "takeaway":
      return <Takeaway {...props} />;
  }
};
