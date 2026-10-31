/**
 * Pupper's emergency alarm (collapse watch): a two-tone siren made with Web Audio, no sound file.
 * App.tsx starts it while the screen shows an emergency with siren: true, and stops it when that ends.
 */
let ctx: AudioContext | null = null;
let osc: OscillatorNode | null = null;
let gain: GainNode | null = null;
let timer: number | null = null;

const HIGH_HZ = 960;
const LOW_HZ = 770;
const SWITCH_MS = 500;
// Kept low, about as loud as blind mode's beep, for demos in a crowded room. Raise it for real use.
const SIREN_GAIN = 0.08;

export function startSiren() {
  if (timer !== null) return;
  try {
    ctx = ctx ?? new (window.AudioContext || (window as any).webkitAudioContext)();
    ctx.resume().catch(() => {});
    gain = ctx.createGain();
    gain.gain.value = SIREN_GAIN;
    gain.connect(ctx.destination);
    osc = ctx.createOscillator();
    osc.type = 'square';
    osc.connect(gain);
    osc.start();
  } catch {
    return;  // no audio: the flashing screen still shows
  }
  let high = false;
  const flip = () => {
    high = !high;
    if (osc && ctx) osc.frequency.setValueAtTime(high ? HIGH_HZ : LOW_HZ, ctx.currentTime);
  };
  flip();
  timer = window.setInterval(flip, SWITCH_MS);
}

export function stopSiren() {
  if (timer !== null) clearInterval(timer);
  timer = null;
  try { osc?.stop(); } catch { /* already stopped */ }
  osc?.disconnect();
  gain?.disconnect();
  osc = null;
  gain = null;
}
