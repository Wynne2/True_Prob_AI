# True Prob AI video studio

Narrated 9:16 explainer videos (1080 x 1920, 30 fps) built as code with Remotion.
Claude writes the storyboard and scene code; Remotion renders every frame exactly.

## Pipeline

1. **Brief**: copy `briefs/TEMPLATE.md`, fill it in.
2. **Storyboard**: write `storyboards/<id>.json` from the brief (format below) and add it to `STORYBOARDS` in `src/Root.tsx`.
3. **Narration first**: `npm run narrate -- <id>` makes one WAV per scene in `public/narration/<id>/` and a `manifest.json` of lengths. Scene timing follows the voice, never the other way round.
4. **Preview**: `npm run dev` opens Remotion Studio. Review each scene, then fix one scene at a time.
5. **Render**: `npm run render -- <id>` writes `out/<id>.mp4`. `npm run make -- <id>` does narrate and render together.

## Storyboard format

```json
{
  "id": "kebab-case-id",
  "title": "Working title",
  "voice": "ash",
  "voiceInstructions": "How the narrator should sound.",
  "scenes": [
    { "id": "hook", "type": "hook", "title": "...", "kicker": "...", "say": "...", "caption": "" }
  ]
}
```

Every scene has `id`, `type`, `say` (spoken text: write numbers as words, "minus one fifty"),
optional `caption` (on-screen text: write numbers as digits, "−150"; `""` hides captions), and
optional `seconds` (used only before narration exists).

| type    | Extra fields | Shows |
|---------|--------------|-------|
| `hook`  | `title`, `kicker?` | 3D basketball (Three.js) and the hook line |
| `odds`  | `title`, `odds` | American odds, the conversion formula, a ring filling to the implied chance |
| `vig`   | `title`, `sides: [{label, odds}, {label, odds}]` | Both sides' implied chances adding past 100%, and the cut |
| `model` | `title`, `factors[]`, `model` (0 to 1), `odds`, `exampleLabel` | Model inputs, then the model-vs-market meter and edge |
| `cta`   | `title`, `points[]` | Payoff line and what the product does |

Probabilities, percentages and edges are computed in `src/storyboard.ts` from the odds,
so a storyboard cannot carry a wrong number. New scene types go in `src/scenes.tsx`.

## Pixel-art style

A storyboard with `"style": "pixel"` renders as a 16-bit arcade short (`src/pixel/`), for example
`storyboards/house-edge.json`. Its scenes are `{ "type": "pixel", "shot": "<name>" }`, where each
shot is a hand-built scene in `src/pixel/shots.tsx`; the dollar and percentage figures come from
the `math` block at the top of that file. Add `"highlight": [...]` to colour key caption terms.

- **Art**: `src/pixel/art.tsx` draws everything on a 270 x 480 grid scaled 4x with crisp edges:
  `PixelCharacter` (moods: happy, shock, sad, neutral, smug), `PixelSportsbook` (a building with a
  face), `PixelCoin`, `CoinBurst`, `CashPile`, `BettingSlip`, `ProbabilityMeter`, `PixelScoreboard`,
  `MoneyCounter`, `AnimatedOdds`, `PixelText`, `WarningIcon`, `BlockWipe`, and the `Court` set.
- **Cues**: shots time their beats to spoken words (`useCue(/word/, fallbackSeconds)`), using the
  transcribed word timings, so visuals land on the narration.
- **Audio**: `npm run sfx` synthesizes the chiptune loop and 12 arcade sound effects into
  `public/audio/` from oscillators and seeded noise, so all audio is original.
- **Transitions**: `src/pixel/PixelTransition.tsx` cuts with a block dissolve.

## Rules for every video

- **Lead with strengths and build trust.** Show what the product does well. Never claim or imply
  profit, "locks", or beating the books. Any model number that is not from a real backtest is
  labelled as an example.
- **Responsible gambling.** The `21+ | Gambling problem? 1-800-GAMBLER` line is on screen the whole
  video (`Chrome` in `src/Explainer.tsx`). Do not remove it.
- **Facts.** Check every claim before it goes in a storyboard. A wrong number with a good font is still wrong.
- **Safe area.** Key content stays inside `safe` in `src/theme.ts`: TikTok and Reels cover the top
  tabs, the bottom 380 px and a right-hand column.
- **Motion.** Animate only with `useCurrentFrame()` (never `useFrame()` in Three.js scenes).
  One clear focal point per scene.
- **Length.** 20 to 45 seconds. If narration runs long, cut words, do not speed up the voice.

## Voice

`scripts/narrate.mjs` uses OpenAI text-to-speech (`gpt-4o-mini-tts`) with `OPENAI_API_KEY`
from the environment or the repo's `.env`, then transcribes each clip with `whisper-1` for word
timings (`<scene>.words.json`) that drive caption sync. Scene length is the time to the last
spoken word, so trailing silence never pads a scene. Without a key it falls back to the offline
Windows voice and evenly spaced captions. Unchanged lines are reused, so re-running only pays
for edited scenes.
Voices: alloy, ash, ballad, coral, echo, fable, onyx, nova, sage, shimmer, verse.

## Tooling

- Remotion 4.0.539, pinned. The npm `latest` tag for `remotion` points at an old alpha, so
  upgrade with explicit versions (see the `remotion-upgrade` skill).
- Remotion agent skills live in `.claude/skills` (refresh with `npx remotion skills update`, then move
  anything it adds under `.agents/skills` into `.claude/skills`); the Remotion
  Claude Code plugin is installed at user scope.
- Rendering on Windows needs x64 Node (Remotion ships no ARM64 Windows renderer).
