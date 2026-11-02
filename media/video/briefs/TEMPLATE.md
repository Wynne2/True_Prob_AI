# Video production brief

Copy this file, fill it in, and give it to Claude Code in the `media/video/` folder:
"Make this video from briefs/<file>.md."

**Topic:** <the one question this video answers>

**Format:** 30 to 40 seconds, 9:16, 1080 x 1920, 30 fps

**Audience:** <who is watching and what they already know>

**Hook (first 3 seconds):** <the line that stops the scroll>

**Key points (two at most):**
1. <point>
2. <point>

**Payoff:** <what the viewer should remember>

**Visual direction:** True Prob AI brand (navy, court orange, mint for positive edge), Barlow type,
3D basketball hook, animated numbers and meters. Reference clips: <links or screenshots, and what to copy from each>

**Facts to verify:** <every number or claim, with its source>

**Production requirements:**
- Fact-check every claim, then write the storyboard JSON (spoken `say` and on-screen `caption`).
- Generate narration first (`npm run narrate -- <id>`) and let it set the timing.
- Build or reuse scenes, review each scene in Studio, fix one scene at a time.
- Render the MP4 (`npm run render -- <id>`) and check frames from every scene.

**Quality constraints:** no clipped text, awkward transitions, wrong numbers or unreadable captions.
Keep key content in the safe area. Lead with strengths, no profit claims, 21+ / 1-800-GAMBLER on screen.
