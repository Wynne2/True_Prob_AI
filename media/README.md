# Media

Everything for promoting True Prob AI. Nothing in this folder is used by the app itself.

| Folder | What it holds |
|--------|---------------|
| `video/` | Video studio: narrated 9:16 explainers rendered from JSON storyboards with Remotion and Three.js. Start with `video/CLAUDE.md`. |
| `marketing/` | Content drafts and finished videos for TikTok and Instagram. |
| `.claude/skills/` | Social-media Claude skills (hooks, post writing, carousels, reels scripting, thumbnails and more). |

Run Claude Code from this folder (or `video/`) so these skills load:

```bash
cd media
claude
```

## Rules for all content

- Lead with strengths and build trust. No claims of profit, "locks" or beating the books.
- Label any model number that is not from a real backtest as an example.
- Every gambling-related post or video carries **21+. Gambling problem? Call 1-800-GAMBLER.**
- Nothing is posted without approval of the exact post.

## Make a video

```bash
cd media/video
npm install
npm run make -- true-prob-odds
```

The MP4 lands in `video/out/` (not committed; render it again from the storyboard).
