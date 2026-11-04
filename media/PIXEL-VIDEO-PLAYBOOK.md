# Pixel-art short video playbook

How the True Prob AI pixel video (`media/marketing/house-edge-9x16.mp4`) was made, and how to set up
the same pipeline in a new session for **pixel-art car explainer shorts**.

The engine lives in `media/video/`. Everything below refers to files there unless noted.

---

## 1. The idea in one paragraph

The video is **code, not a timeline edit**. Claude writes a JSON storyboard (what the narrator says,
what the captions show, which shot plays) and React components that draw every frame. Remotion renders
those components frame by frame into an MP4. The voice is generated **first**, then transcribed to get
the exact time of every spoken word, and the visuals are timed to those words. Music and sound effects
are synthesized from oscillators, so all audio is original and needs no licence.

```
brief.md → storyboard.json → narrate (TTS + Whisper word times) → shots (React/SVG) → preview → render → MP4
```

---

## 2. Tools

| Tool | What it did | Where |
|------|-------------|-------|
| **Claude Code** (desktop app, CLI 2.1.x) | Wrote the storyboard, all scene code, scripts; ran narration, renders and checks | — |
| **Remotion 4.0.539** | React → video renderer. Every frame is a function of `useCurrentFrame()`, so output is exact and repeatable | `package.json` |
| **Remotion Claude Code plugin** (`remotion@remotion`) | Gives Claude Remotion commands and docs | installed at user scope |
| **Remotion agent skills** (13) | Best-practice instructions Claude loads when writing Remotion code: `remotion-best-practices`, `-captions`, `-markup`, `-render`, `-studio`, `-docs`, `-upgrade`, `-license`, etc. | `.claude/skills/` |
| **@remotion/captions** | `createTikTokStyleCaptions` groups words into caption pages | `src/Captions.tsx` |
| **@remotion/transitions** | `TransitionSeries` between scenes, with a custom block-dissolve | `src/pixel/PixelTransition.tsx` |
| **@remotion/media** | `<Audio>` for narration, looping music and SFX | `src/pixel/PixelVideo.tsx` |
| **@remotion/google-fonts** | Press Start 2P (captions and digits) | `src/pixel/art.tsx` |
| **OpenAI `gpt-4o-mini-tts`** | Narration, one WAV per scene, voice `ash` with a "brisk, upbeat" instruction | `scripts/narrate.mjs` |
| **OpenAI `whisper-1`** | Word-level timestamps for each WAV (`<scene>.words.json`) | `scripts/narrate.mjs` |
| **Node.js 24 x64** | Runs Remotion. Windows on ARM needs the **x64** build: Remotion ships no ARM64 Windows renderer | `%LOCALAPPDATA%\nodejs-portable` |
| **Headless Chrome + ffmpeg** | Bundled by Remotion; renders frames and encodes H.264 (CRF 18) | `scripts/render.mjs` |
| **Remotion Studio** | Live preview and scrubbing in the browser (`npm run dev`) | — |
| **Python + numpy** | Checked the final mix: peak −0.7 dBFS, RMS −20.6 dBFS, no clipping | one-off check |

Not used in the final pipeline but available: the **vidIQ connector** (voiceover, music, motion
graphics) was tried for the first clip, and the 17 **social skills** in `media/.claude/skills/`
(`hook-generator`, `reels-scripting`, `youtube-thumbnail`, …) help with hooks, titles and posts.

---

## 3. Methods

1. **Narration first.** `npm run narrate -- <id>` writes `public/narration/<id>/<scene>.wav`, a
   `manifest.json` of lengths, and word timings. Each scene lasts until its **last audible sample +
   0.12 s**, so silence never pads a scene. Scene length follows the voice, never the reverse. If a
   video runs long, cut words; do not speed up the voice.
2. **Hash caching.** Each scene's text + voice + instructions is hashed (`hashes.json`). Re-running
   narration only pays for scenes you edited.
3. **Storyboard as data.** `storyboards/<id>.json` holds `say` (spoken, numbers as words: "zero to
   sixty in four point two") and `caption` (on screen, digits: "0–60 in 4.2 s"). `Root.tsx`'s
   `calculateMetadata` reads the manifest so the composition length is computed, not typed.
4. **Word cues.** Inside a shot, `useCue(/turbo/, 1.2)` returns the frame where the narrator says
   "turbo" (or 1.2 s as a fallback). Coins drop, needles swing and text pops exactly on the word.
5. **Pixel grid.** Everything is drawn on a **270 × 480** grid scaled **4×** to 1080 × 1920 inside an
   SVG with `shapeRendering="crispEdges"`. Sprites are character maps (`"..KK..", ".KYYK."`) merged
   into runs of `<rect>`s. Motion uses stepped easing (`steps()`) so it moves like a 16-bit game, not
   a smooth tween.
6. **Captions.** Caption characters are mapped onto Whisper's spoken timeline, so digits on screen
   stay in sync with words in the audio. The current page is held through pauses. Key terms in
   `highlight` turn gold; the active word turns green.
7. **Original audio.** `npm run sfx` synthesizes 12 effects (coin, jackpot, whoosh, warning, thud,
   powerup, …) and a 16 s 120 bpm chiptune loop (C–Am–F–G) from square/triangle waves and seeded noise.
   Music sits at 0.16 volume under the voice with fades.
8. **Numbers live in code.** Every figure on screen is computed from one `math` block (or from
   `storyboard.ts`), so a typo cannot put a wrong number in the video.
9. **Review loop.** Preview in Studio, render stills of suspect frames, look at them, fix **one scene
   at a time**, re-render. Most fixes were layout: overlaps, text too small, content outside the
   safe area (`safe = {top: 270, side: 90, bottom: 420}`) that TikTok/Reels UI covers.

---

## 4. Set it up again for pixel car videos

### 4.1 Prerequisites (once per machine)

- Git, Claude Code desktop app.
- **Node.js 24 x64** (on Windows ARM; on x64/Mac any Node 20+). Check with `node -p process.arch` → `x64`.
- An **OpenAI API key** with access to `gpt-4o-mini-tts` and `whisper-1`.

### 4.2 Make the project (copy the engine, leave True Prob content behind)

```powershell
robocopy C:\git\True_Prob_AI\media\video C:\git\pixel-cars /E /XD node_modules out narration
```

```powershell
cd C:\git\pixel-cars; git init; npm install
```

### 4.3 Install the Remotion plugin and skills

The plugin is per user, so it is already installed on this machine. On a new machine:

```powershell
claude plugin marketplace add remotion-dev/claude-code-plugin
```

```powershell
claude plugin install remotion@remotion
```

The 13 skills come along in `.claude/skills/` from the copy. To refresh them later run
`npx remotion skills update`, then move anything it puts in `.agents/skills` into `.claude/skills`
(git here has `core.symlinks=false`, so they must be real folders).

### 4.4 Add the key

Create `C:\git\pixel-cars\.env` (never commit it; add `.env` to `.gitignore`):

```
OPENAI_API_KEY=sk-...
```

### 4.5 Smoke test

```powershell
npm run sfx; npm run typecheck; npm run dev
```

Studio should open with the old house-edge video. That proves rendering works before anything changes.

### 4.6 Open a new Claude Code session in `C:\git\pixel-cars` and paste this

> This folder is a Remotion 4.0.539 video studio copied from another project. Read `CLAUDE.md`, then
> turn it into a studio for **pixel-art car explainer shorts** (9:16, 1080×1920, 30 fps, 30–50 s).
> Keep the whole pixel pipeline: narration-first timing, `scripts/narrate.mjs`, Whisper word cues
> (`useCue`), the 270×480 grid at 4×, `Captions.tsx`, `PixelTransition`, `scripts/sfx.mjs`, hash caching.
>
> 1. Remove the old product: `Explainer.tsx`, `scenes.tsx` (Three.js basketball), the `true-prob-odds`
>    and `house-edge` storyboards and narration, the betting `math` block and shots, the 21+ chrome,
>    and `three` / `@react-three/fiber` / `@remotion/three` from `package.json`. Rename the package
>    and on-screen brand to **<MY CHANNEL NAME>**.
> 2. Rewrite `CLAUDE.md` for car videos (rules below).
> 3. Build a car art kit in `src/pixel/art.tsx`: `PixelCar` (side view, body colour, spinning wheels,
>    exhaust puffs, speed lines), `Road` (scrolling lane lines, parallax skyline, day/night), `Garage`,
>    `EngineCutaway` (moving pistons), `Gauge` (speedo or tach needle), `Stopwatch`, `SpecCard`,
>    `Turbo`, `Tire`, `Sparks`, `FuelDrop`. Keep `PixelText`, `MoneyCounter`, `BlockWipe`, `Sparkles`.
> 4. Put each video's shots in its own file, `src/pixel/videos/<id>.tsx`, with its own `facts` block
>    for every number shown.
> 5. Add to `sfx.mjs`: engine rev (pitch ramp), turbo spool and blow-off, tire screech, gear-shift
>    clunk, horn, and a ~140 bpm driving chiptune loop.
> 6. Make one test video, "How a turbocharger works", end to end: brief, storyboard, narrate, shots
>    cued to words, review stills, render to `out/`. Then show me the MP4.

**Rules for the new `CLAUDE.md`:**

- Facts: every spec (hp, torque, 0–60, weight, years) comes from a manufacturer or reputable source,
  with model year and trim named; estimates are labelled as estimates.
- No real manufacturer logos, badges or exact trademarked designs. Draw generic silhouettes
  ("a 90s rear-wheel-drive coupe"); naming a car in narration for factual content is fine.
- Don't present street racing or unsafe driving as something to copy.
- Same safe area, same "cut words, don't speed up the voice" rule, same one-focal-point-per-scene rule.

---

## 5. Making each new video (the loop)

1. Tell Claude the topic ("How ABS works", "Why diesels have more torque").
2. Claude writes `briefs/<id>.md` and `storyboards/<id>.json`, 5–7 scenes, hook in the first 2 s.
3. `npm run narrate -- <id>`: voice and word timings. Check the total length.
4. Claude builds the shots and cues them to words.
5. `npm run dev` to preview; Claude renders stills of each scene and fixes layout.
6. `npm run render -- <id>` → `out/<id>.mp4`. (`npm run make -- <id>` does steps 3 and 6 together.)
7. You post it yourself.

Cost per video is a few cents of OpenAI TTS and Whisper. Rendering is local and free.

---

## 6. Gotchas we hit

- `npm i remotion` without a version installs an **old alpha** (the `latest` tag is wrong). Always pin
  versions; upgrade all `@remotion/*` packages together.
- Windows ARM: Node must be x64 or rendering fails.
- `remotion.config.ts` sets `setChromiumOpenGlRenderer("angle")` for WebGL. Harmless without 3D.
- Pixelify Sans renders "5" like "S". Use **Press Start 2P** for anything with digits.
- Animate only with `useCurrentFrame()`. CSS animations and `useFrame()` don't render frame-exact.
- Remotion licence: free for individuals and companies of up to 3 people; larger companies need a
  company licence (`remotion-license` skill).
