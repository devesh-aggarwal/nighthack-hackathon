/**
 * Pupper says a line out loud with the browser's own speech, no Gemini needed (collapse watch's
 * "Are you okay?"). App.tsx calls it once when an event's display carries `say`.
 */
export function speak(text: string) {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;  // the screen still shows it
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.9;  // a little slow: someone on the floor may be dazed
  utterance.volume = 1;
  window.speechSynthesis.speak(utterance);
}
