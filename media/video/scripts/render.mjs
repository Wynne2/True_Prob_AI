// Render storyboards to out/<id>.mp4 (H.264, 1080x1920, 30 fps).
//
//   node scripts/render.mjs [storyboard-id ...]   (default: every storyboard)
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const ids = process.argv.slice(2);
const all = readdirSync(join(ROOT, "storyboards"))
  .filter((f) => f.endsWith(".json"))
  .map((f) => f.slice(0, -5));

for (const id of ids.length ? ids : all) {
  if (!all.includes(id)) throw new Error(`No storyboard storyboards/${id}.json`);
  const out = join("out", `${id}.mp4`);
  const cli = join(ROOT, "node_modules", "@remotion", "cli", "remotion-cli.js");
  const r = spawnSync(process.execPath, [cli, "render", id, out, "--codec=h264", "--crf=18"], {
    cwd: ROOT,
    stdio: "inherit",
  });
  if (r.status !== 0) process.exit(r.status ?? 1);
  console.log(`Rendered ${out}`);
}
