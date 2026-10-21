const STORAGE_KEY = 'walletwise-sounds-enabled';
let audioContext = null;

export function soundsEnabled() {
  const value = localStorage.getItem(STORAGE_KEY);
  return value === null ? true : value === 'true';
}

export function setSoundsEnabled(enabled) {
  localStorage.setItem(STORAGE_KEY, String(Boolean(enabled)));
}

function getContext() {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return null;
  if (!audioContext) audioContext = new AudioContext();
  if (audioContext.state === 'suspended') audioContext.resume().catch(() => {});
  return audioContext;
}

function tone(ctx, { start, end = start, duration = 0.11, delay = 0, volume = 0.018, type = 'sine' }) {
  const now = ctx.currentTime + delay;
  const oscillator = ctx.createOscillator();
  const gain = ctx.createGain();
  oscillator.type = type;
  oscillator.frequency.setValueAtTime(start, now);
  oscillator.frequency.exponentialRampToValueAtTime(Math.max(20, end), now + duration);
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(volume, now + 0.018);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
  oscillator.connect(gain);
  gain.connect(ctx.destination);
  oscillator.start(now);
  oscillator.stop(now + duration + 0.02);
}

export function playUiSound(kind = 'navigate') {
  if (!soundsEnabled()) return;
  const ctx = getContext();
  if (!ctx) return;

  try {
    if (kind === 'send') {
      tone(ctx, { start: 430, end: 650, duration: 0.12, volume: 0.016, type: 'sine' });
      tone(ctx, { start: 640, end: 780, duration: 0.10, delay: 0.045, volume: 0.009, type: 'sine' });
      return;
    }
    if (kind === 'receive') {
      tone(ctx, { start: 620, end: 760, duration: 0.12, volume: 0.014, type: 'sine' });
      tone(ctx, { start: 820, end: 920, duration: 0.13, delay: 0.075, volume: 0.010, type: 'sine' });
      return;
    }
    if (kind === 'delete') {
      tone(ctx, { start: 330, end: 230, duration: 0.12, volume: 0.012, type: 'triangle' });
      return;
    }
    if (kind === 'new') {
      tone(ctx, { start: 500, end: 700, duration: 0.11, volume: 0.012, type: 'sine' });
      return;
    }
    tone(ctx, { start: 390, end: 470, duration: 0.075, volume: 0.009, type: 'sine' });
  } catch {
    // Sound is decorative. Never let an audio issue block the app.
  }
}
