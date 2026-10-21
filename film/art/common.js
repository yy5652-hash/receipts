// Deterministic animation helpers: every page draws itself from `t` (seconds into the scene) in render(t).
export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const lerp = (a, b, p) => a + (b - a) * p;
export const easeInOut = p => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
export const easeOut = p => 1 - Math.pow(1 - p, 3);
export const easeBack = p => { const c1 = 1.5, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); };
export const prog = (t, a, b, ease = easeInOut) => ease(clamp((t - a) / (b - a)));
export const $ = s => document.querySelector(s);
export const $$ = s => [...document.querySelectorAll(s)];

// The film's line times (scene-relative seconds), injected by the director as window.LINES = {id: [start, end]},
// and the sentence times inside a line as window.PARTS = {id: [[start, end], ...]}.
export const line = (id, fallback) => (window.LINES && window.LINES[id]) || fallback;
export const parts = (id, fallback) => (window.PARTS && window.PARTS[id]) || fallback;

export const hash = (a, b) => { let h = (a * 374761393 + b * 668265263) | 0; h = (h ^ (h >>> 13)) * 1274126177; return Math.abs(h ^ (h >>> 16)); };

export const ICONS = {
  supported: '<path d="M3.5 8.5l3 3 6-7"/>',
  partial: '<circle cx="8" cy="8" r="5.5"/><path d="M8 2.5a5.5 5.5 0 0 1 0 11z" fill="currentColor" stroke="none"/>',
  contradicted: '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/>',
  not_in_source: '<path d="M6 6.1a2.1 2.1 0 1 1 3.2 1.8c-.7.4-1.2.9-1.2 1.7"/><circle cx="8" cy="12.3" r=".7" fill="currentColor" stroke="none"/>',
  source_unavailable: '<path d="M6.8 5.2H5.6a2.8 2.8 0 0 0 0 5.6h1.2M9.2 5.2h1.2a2.8 2.8 0 0 1 0 5.6H9.2M5.4 13l5.2-10"/>',
};
export const NAMES = { supported: "Supported", partial: "Partly supported", contradicted: "Contradicted", not_in_source: "Not in source", source_unavailable: "Source missing" };
export const badge = label => `<span class="badge" data-label="${label}"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICONS[label]}</svg>${NAMES[label]}</span>`;
export const LOGO = '<svg viewBox="0 0 24 24" class="logo"><path d="M5 3h14v18l-2.33-1.5L14.33 21 12 19.5 9.67 21 7.33 19.5 5 21z"/><path d="M8.5 8h7M8.5 11.5h7M8.5 15h4"/></svg>';
