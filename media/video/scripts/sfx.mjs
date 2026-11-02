// Synthesize the original chiptune loop and arcade sound effects as WAV files.
// Everything here is generated from oscillators and seeded noise, so the audio is ours to use.
//
//   node scripts/sfx.mjs        writes public/audio/sfx/*.wav and public/audio/music/chiptune.wav
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SR = 22050;

const toWav = (samples) => {
  const data = Buffer.alloc(samples.length * 2);
  samples.forEach((s, i) => data.writeInt16LE(Math.round(Math.max(-1, Math.min(1, s)) * 32767), i * 2));
  const h = Buffer.alloc(44);
  h.write("RIFF", 0);
  h.writeUInt32LE(36 + data.length, 4);
  h.write("WAVEfmt ", 8);
  h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); // PCM
  h.writeUInt16LE(1, 22); // mono
  h.writeUInt32LE(SR, 24);
  h.writeUInt32LE(SR * 2, 28);
  h.writeUInt16LE(2, 32);
  h.writeUInt16LE(16, 34);
  h.write("data", 36);
  h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
};

// Seeded noise so every build produces identical files.
let seed = 1234567;
const noise = () => {
  seed = (seed * 1103515245 + 12345) & 0x7fffffff;
  return (seed / 0x3fffffff) - 1;
};

const square = (phase, duty = 0.5) => (phase % 1 < duty ? 1 : -1);
const triangle = (phase) => 1 - 4 * Math.abs((phase % 1) - 0.5);
const midi = (n) => 440 * 2 ** ((n - 69) / 12);

/** Render `seconds` of audio where voice(t, i) returns a sample; phase is tracked per call site. */
const render = (seconds, voice) => Array.from({ length: Math.round(seconds * SR) }, (_, i) => voice(i / SR, i));

/** A tone with a frequency function f(t), integrated so sweeps stay smooth. */
const tone = (seconds, f, wave, env, gain = 0.4) => {
  let phase = 0;
  return render(seconds, (t) => {
    phase += f(t) / SR;
    return wave(phase) * env(t, seconds) * gain;
  });
};

const decay = (rate) => (t) => Math.exp(-t * rate);
const ad = (attack, release) => (t, len) => Math.min(1, t / attack) * Math.min(1, (len - t) / release);
const cat = (...parts) => parts.flat();

const sfx = {
  coin: cat(tone(0.06, () => 988, (p) => square(p, 0.5), ad(0.002, 0.005), 0.3), tone(0.32, () => 1319, (p) => square(p, 0.5), decay(9), 0.3)),
  jackpot: cat(
    ...[72, 76, 79, 84, 88, 91, 96].map((n) => tone(0.055, () => midi(n), (p) => square(p, 0.25), ad(0.002, 0.01), 0.28)),
    render(0.7, (t) => [84, 88, 91].reduce((s, n) => s + square(t * midi(n), 0.25), 0) / 3 * Math.exp(-t * 3.2) * 0.32),
  ),
  scratch: (() => {
    let phase = 0;
    return render(0.5, (t) => {
      const f = 260 + 1100 * Math.abs(Math.sin(2 * Math.PI * 3.4 * t));
      phase += f / SR;
      const saw = 2 * (phase % 1) - 1;
      return (saw * 0.6 + noise() * 0.4) * Math.min(1, t / 0.01) * Math.exp(-t * 2.5) * 0.45;
    });
  })(),
  whoosh: (() => {
    let y = 0;
    return render(0.45, (t) => {
      const a = 0.04 + 0.5 * Math.sin((Math.PI * t) / 0.45);
      y += a * (noise() - y);
      return y * Math.sin((Math.PI * t) / 0.45) * 0.9;
    });
  })(),
  sad: cat(
    tone(0.28, () => midi(67), (p) => square(p, 0.5), ad(0.01, 0.04), 0.22),
    tone(0.28, () => midi(66), (p) => square(p, 0.5), ad(0.01, 0.04), 0.22),
    tone(0.28, () => midi(65), (p) => square(p, 0.5), ad(0.01, 0.04), 0.22),
    tone(0.9, (t) => midi(64) * (1 + 0.02 * Math.sin(2 * Math.PI * 6 * t)), (p) => square(p, 0.5), ad(0.01, 0.3), 0.22),
  ),
  warning: cat(...[0, 1, 2, 3].map((i) => tone(0.12, () => (i % 2 ? 660 : 880), (p) => square(p, 0.5), ad(0.003, 0.02), 0.22))),
  blip: tone(0.07, () => 1568, (p) => square(p, 0.25), decay(30), 0.25),
  tick: tone(0.025, () => 2093, (p) => square(p, 0.5), decay(90), 0.16),
  thud: render(0.3, (t) => (Math.sin(2 * Math.PI * (90 - 40 * t) * t) * 0.8 + noise() * 0.2 * Math.exp(-t * 60)) * Math.exp(-t * 11) * 0.6),
  powerup: tone(0.35, (t) => 400 + 1400 * (t / 0.35), (p) => square(p, 0.25), ad(0.005, 0.05), 0.24),
  shrink: tone(0.5, (t) => 1100 - 900 * (t / 0.5), (p) => square(p, 0.5), ad(0.005, 0.08), 0.2),
  pixel: (() => {
    let held = 0;
    return render(0.22, (t, i) => {
      if (i % 11 === 0) held = noise();
      return held * Math.exp(-t * 14) * 0.3;
    });
  })(),
};

// ---------------------------------------------------------------------------
// Music: 120 bpm, C - Am - F - G, two 4-bar passes (16 s), loops seamlessly.
// ---------------------------------------------------------------------------
const BPM = 120;
const EIGHTH = 60 / BPM / 2;
const chords = [48, 45, 41, 43]; // bass roots: C3 A2 F2 G2
const lead = [
  [76, 79, 84, 79, 76, 79, 84, 86],
  [84, 81, 76, 81, 84, 81, 76, 79],
  [81, 84, 89, 84, 81, 84, 89, 88],
  [86, 83, 79, 83, 86, 83, 79, 83],
];
const musicSeconds = 2 * chords.length * 8 * EIGHTH;
const music = render(musicSeconds, (t) => {
  const step = Math.floor(t / EIGHTH);
  const inStep = t - step * EIGHTH;
  const bar = Math.floor(step / 8) % chords.length;
  const pass = Math.floor(step / 32);
  const beat8 = step % 8;
  // Bass: root and octave bouncing on eighths.
  const bassNote = chords[bar] + (beat8 % 2 ? 12 : 0);
  const bass = triangle(t * midi(bassNote)) * Math.exp(-inStep * 5) * 0.32;
  // Lead: full eighths on pass one, quarters on pass two for variation.
  const playLead = pass === 0 || beat8 % 2 === 0;
  const leadNote = lead[bar][beat8];
  const leadSample = playLead ? square(t * midi(leadNote), 0.25) * Math.exp(-inStep * 7) * 0.12 : 0;
  // Drums: kick on 1 and 3, snare on 2 and 4, hats on every eighth.
  const quarter = beat8 / 2;
  const kick = beat8 % 4 === 0 ? Math.sin(2 * Math.PI * (110 - 300 * inStep) * inStep) * Math.exp(-inStep * 18) * 0.5 : 0;
  const snare = beat8 % 4 === 2 && Number.isInteger(quarter) ? noise() * Math.exp(-inStep * 22) * 0.22 : 0;
  const hat = noise() * Math.exp(-inStep * 120) * 0.07;
  return bass + leadSample + kick + snare + hat;
});

const sfxDir = join(ROOT, "public", "audio", "sfx");
const musicDir = join(ROOT, "public", "audio", "music");
mkdirSync(sfxDir, { recursive: true });
mkdirSync(musicDir, { recursive: true });
for (const [name, samples] of Object.entries(sfx)) writeFileSync(join(sfxDir, `${name}.wav`), toWav(samples));
writeFileSync(join(musicDir, "chiptune.wav"), toWav(music));
console.log(`Wrote ${Object.keys(sfx).length} sound effects and ${musicSeconds.toFixed(1)}s of music.`);
