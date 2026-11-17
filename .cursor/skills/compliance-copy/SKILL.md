---
name: compliance-copy
description: Use when writing or changing any customer-facing words (pages, templates, emails, pricing, Advisor output, SEO meta, social posts) or the legal pages.
---
# Compliance copy

True Prob AI is a research and information tool for adults. It takes no wagers, awards no
prizes, places no orders and does not claim a proven edge.

## Must appear

- **21+** and **1-800-GAMBLER** on every page and every email. They live in
  `web/templates/base.html` (footer) and `web/mailer.py` (`FOOTER`). Do not remove or weaken
  them; new layouts and emails must extend those.
- "Research and information, not betting or investment advice" (or the same meaning) wherever
  probabilities are sold or promoted.
- When the model weight is 0, the line shows the market consensus and says so.

## Never write

- "Lock", "guaranteed", "can't lose", "free money", "sure thing", "beat the books", "sharp edge".
- Win rates, ROI or profit claims as selling points. Performance numbers go only on
  `/performance`, in full, losses included.
- Personalised betting or trading advice ("you should bet X"). Analysis stays general.
- Urgency or pressure aimed at chasing losses.
- Anything that targets or appeals to under-21s.

## Legal pages

- The text lives in `web/content.py`. **They are drafts.** The "draft" notice stays until an admin
  sets the `legal_reviewed` flag after a lawyer reviews them. Agents never set that flag and never
  remove the notice.
- Keep the placeholders (`[LEGAL ENTITY NAME]`, `[BUSINESS ADDRESS]`, `[GOVERNING STATE]`) until
  Michael supplies the real values.
- Which states are blocked (`BLOCKED_REGIONS`) is decided by counsel, not by an agent.
- Do not add legal conclusions. Write "needs counsel" in "Needs owner" instead.

## Check before shipping copy

- [ ] 21+ and 1-800-GAMBLER still visible (tests in `tests/test_web.py` cover the footer and emails).
- [ ] No banned phrases; no numbers that are not on `/performance`.
- [ ] Claims match `docs/LAUNCH.md` ("transparency and speed, not a proven edge").
