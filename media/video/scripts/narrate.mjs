// Generate one narration clip per scene and a manifest of clip lengths.
//
//   node scripts/narrate.mjs [storyboard-id ...]   (default: every storyboard)
//
// Voice: OpenAI text-to-speech when OPENAI_API_KEY is set (here or in the repo .env),
// otherwise the offline Windows voice (System.Speech). Unchanged lines are reused.
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const MODEL = process.env.TTS_MODEL || "gpt-4o-mini-tts";

/** Nearest .env at or above this project (the repo root's .env holds the API keys). */
const findEnv = () => {
  for (let dir = ROOT; ; dir = dirname(dir)) {
    if (existsSync(join(dir, ".env"))) return join(dir, ".env");
    if (dirname(dir) === dir) return "";
  }
};

const readEnvKey = (name) => {
  if (process.env[name]) return process.env[name];
  const envPath = findEnv();
  if (!envPath) return "";
  const line = readFileSync(envPath, "utf8").split(/\r?\n/).find((l) => l.startsWith(`${name}=`));
  return line ? line.slice(name.length + 1).trim().replace(/^["']|["']$/g, "") : "";
};

/** Seconds of audio in a PCM WAV file, read from its header. */
export const wavSeconds = (buf) => {
  let byteRate = 0;
  for (let off = 12; off + 8 <= buf.length; ) {
    const id = buf.toString("ascii", off, off + 4);
    const size = buf.readUInt32LE(off + 4);
    if (id === "fmt ") byteRate = buf.readUInt32LE(off + 16);
    if (id === "data") {
      // Streamed WAVs can carry a placeholder size; fall back to the bytes actually present.
      const bytes = size === 0 || size === 0xffffffff || off + 8 + size > buf.length ? buf.length - off - 8 : size;
      if (!byteRate) throw new Error("WAV has no fmt chunk");
      return bytes / byteRate;
    }
    off += 8 + size + (size % 2);
  }
  throw new Error("WAV has no data chunk");
};

const openaiSpeech = async (key, text, voice, instructions) => {
  const res = await fetch("https://api.openai.com/v1/audio/speech", {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model: MODEL, voice, input: text, instructions, response_format: "wav" }),
  });
  if (!res.ok) throw new Error(`OpenAI speech failed (${res.status}): ${(await res.text()).slice(0, 300)}`);
  return Buffer.from(await res.arrayBuffer());
};

const windowsSpeech = (text, outPath) => {
  if (process.platform !== "win32") throw new Error("No OPENAI_API_KEY and no offline voice on this OS.");
  const script =
    "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; " +
    "$s.Rate = 1; $s.SetOutputToWaveFile($env:TP_OUT); $s.Speak($env:TP_TEXT); $s.Dispose()";
  execFileSync("powershell.exe", ["-NoProfile", "-Command", script], { env: { ...process.env, TP_OUT: outPath, TP_TEXT: text } });
  return readFileSync(outPath);
};

const narrate = async (file) => {
  const sb = JSON.parse(readFileSync(join(ROOT, "storyboards", file), "utf8"));
  const key = readEnvKey("OPENAI_API_KEY");
  const provider = key ? `openai:${MODEL}:${sb.voice}` : "windows";
  const outDir = join(ROOT, "public", "narration", sb.id);
  mkdirSync(outDir, { recursive: true });
  const hashPath = join(outDir, "hashes.json");
  const hashes = existsSync(hashPath) ? JSON.parse(readFileSync(hashPath, "utf8")) : {};
  const manifest = {};

  for (const scene of sb.scenes) {
    const out = join(outDir, `${scene.id}.wav`);
    const hash = createHash("sha256").update([provider, sb.voiceInstructions ?? "", scene.say].join("\n")).digest("hex");
    let buf;
    if (hashes[scene.id] === hash && existsSync(out)) {
      buf = readFileSync(out);
    } else {
      buf = key ? await openaiSpeech(key, scene.say, sb.voice, sb.voiceInstructions) : windowsSpeech(scene.say, out);
      writeFileSync(out, buf);
      hashes[scene.id] = hash;
    }
    manifest[scene.id] = Math.round(wavSeconds(buf) * 1000) / 1000;
    console.log(`${sb.id}/${scene.id}: ${manifest[scene.id].toFixed(2)}s`);
  }

  writeFileSync(join(outDir, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  writeFileSync(hashPath, JSON.stringify(hashes, null, 2) + "\n");
  const total = Object.values(manifest).reduce((a, b) => a + b, 0);
  console.log(`${sb.id}: ${total.toFixed(1)}s of narration via ${provider}`);
};

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const ids = process.argv.slice(2);
  const files = readdirSync(join(ROOT, "storyboards")).filter((f) => f.endsWith(".json"));
  const chosen = ids.length ? ids.map((id) => `${id}.json`) : files;
  for (const f of chosen) {
    if (!files.includes(f)) throw new Error(`No storyboard storyboards/${f}`);
    await narrate(f);
  }
}
