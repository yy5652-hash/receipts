// Injected into every page the film records. Adds the film's own layer on top of the page: captions, a drawn cursor,
// highlight rings, a fade, and the small "recorded run" tag. It never changes what the app computes or draws.
//
// Geometry: the page is shown through a camera, a rectangle {x, y, w} in the page's own viewport (16:9, so the height
// is w * 9 / 16). The director captures exactly that rectangle at 1920x1080; this file only has to keep its own layer
// (designed on a 1920x1080 sheet) glued to it. Rings and rects are in viewport pixels of the page.
(() => {
  if (window.film) return true;
  const css = `
    #film-ui { position: fixed; left: 0; top: 0; width: 1920px; height: 1080px; transform-origin: 0 0; z-index: 2147483000; pointer-events: none; font-family: Inter, -apple-system, sans-serif; overflow: hidden; }
    #film-ui > div { position: absolute; }
    #film-fade { inset: 0; background: #f6f3ec; opacity: 0; }
    #film-cursor { width: 38px; height: 38px; left: 0; top: 0; opacity: 0; transform-origin: 4px 4px; filter: drop-shadow(0 5px 8px rgba(31,29,25,.35)); }
    #film-ripple { width: 76px; height: 76px; margin: -38px 0 0 -38px; border-radius: 50%; border: 4px solid #1f1d19; opacity: 0; }
    #film-caption { left: 50%; bottom: 54px; transform: translateX(-50%); max-width: 1760px; width: max-content; font-weight: 600; font-size: 36px; line-height: 1.36; color: #fbf9f3; text-align: center;
      background: rgba(31, 29, 25, 0.9); padding: 14px 32px 15px; border-radius: 18px; opacity: 0; box-shadow: 0 14px 34px rgba(31,29,25,.28); border: 1px solid rgba(251, 249, 243, 0.16); letter-spacing: -0.003em; }
    #film-key { left: 0; top: 0; font-weight: 600; font-size: 28px; color: #fbf9f3; background: #1f1d19; padding: 8px 18px; border-radius: 12px; opacity: 0; box-shadow: 0 10px 24px rgba(31,29,25,.3); letter-spacing: .02em; }
    #film-stat { left: 48px; top: 40px; font-weight: 600; font-size: 26px; color: #fbf9f3; background: rgba(31, 29, 25, 0.92); padding: 10px 22px 11px; border-radius: 14px; opacity: 0; box-shadow: 0 10px 26px rgba(31,29,25,.25); }
    #film-stat b { color: #8ee0ae; font-weight: 700; }
    #film-photo { inset: 0; overflow: hidden; opacity: 0; background: #f6f3ec; }
    #film-photo img { position: absolute; max-width: none; display: block; }
    #film-hud { right: 48px; top: 40px; display: flex; align-items: center; gap: 12px; font-weight: 600; font-size: 25px; color: #1f1d19; background: rgba(255, 254, 251, 0.94); border: 2px solid #1f1d19; padding: 9px 20px 9px 16px; border-radius: 999px; opacity: 0; }
    #film-hud i { width: 14px; height: 14px; border-radius: 50%; background: #d6434a; display: block; }
    .film-ring { position: fixed; z-index: 2147482000; pointer-events: none; border-style: solid; }`;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
  const ui = document.createElement('div');
  ui.id = 'film-ui';
  ui.innerHTML = `<div id="film-ripple"></div>
    <div id="film-cursor"><svg viewBox="0 0 38 38" width="38" height="38"><path d="M4 3 L4 29 L11 22.5 L16 33.5 L21 31.3 L16 20.5 L26 20.5 Z" fill="#fff" stroke="#1f1d19" stroke-width="2.2" stroke-linejoin="round"/></svg></div>
    <div id="film-photo"><img alt=""></div><div id="film-hud"><i></i><span></span></div><div id="film-key"></div><div id="film-stat"></div>
    <div id="film-caption"></div><div id="film-fade"></div>`;
  document.documentElement.appendChild(ui);
  const $ = id => document.getElementById(id);
  let cam = { x: 0, y: 0, w: 1920 };
  let cur = { x: 0, y: 0, show: false };
  let press = 0;
  const rings = new Map();           // key -> { sel, i, color, a, el }
  const k = () => cam.w / 1920;      // viewport pixels per sheet pixel

  const layout = () => {
    ui.style.left = `${cam.x}px`; ui.style.top = `${cam.y}px`; ui.style.transform = `scale(${k()})`;
    const c = $('film-cursor'), r = $('film-ripple');
    const sx = (cur.x - cam.x) / k(), sy = (cur.y - cam.y) / k();
    c.style.opacity = cur.show ? 1 : 0;
    c.style.transform = `translate(${sx - 4}px, ${sy - 3}px) scale(${1 - 0.15 * Math.sin(Math.PI * Math.min(1, press * 2.5))})`;
    r.style.left = `${sx}px`; r.style.top = `${sy}px`;
    r.style.opacity = press > 0 && press < 1 ? 0.8 * (1 - press) : 0;
    r.style.transform = `scale(${0.4 + press * 1.1})`;
  };
  // A target is a selector string, or {sel, i, text, line}: the i-th match, optionally only the given text inside it
  // (its bounding box), or only its `line`-th line box (for an inline element that wraps).
  const rectOf = (spec, i = 0) => {
    const t = typeof spec === 'string' ? { sel: spec, i } : spec;
    const el = document.querySelectorAll(t.sel)[t.i || 0];
    if (!el) return null;
    let r;
    if (t.text) {
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let node, found = null;
      while ((node = walker.nextNode())) { const at = node.nodeValue.indexOf(t.text); if (at >= 0) { found = { node, at }; break; } }
      if (!found) return null;
      const range = document.createRange();
      range.setStart(found.node, found.at); range.setEnd(found.node, found.at + t.text.length);
      r = range.getBoundingClientRect();
    } else if (t.line !== undefined) {
      r = el.getClientRects()[t.line] || el.getClientRects()[0];
    } else r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  };
  const placeRings = () => {
    for (const [key, g] of rings) {
      if (g.a <= 0) { g.el?.remove(); rings.delete(key); continue; }
      const r = rectOf(g.spec);
      if (!r) continue;
      if (!g.el) { g.el = document.createElement('div'); g.el.className = 'film-ring'; document.body.appendChild(g.el); }
      const s = k(), bw = Math.max(1.5, 4 * s), pad = 5 * s;
      Object.assign(g.el.style, {
        left: `${r.x - pad - bw}px`, top: `${r.y - pad - bw}px`, width: `${r.w + 2 * (pad + bw)}px`, height: `${r.h + 2 * (pad + bw)}px`,
        borderWidth: `${bw}px`, borderColor: g.color, borderRadius: `${(g.radius ?? 12) * s + pad}px`, opacity: g.a,
        boxShadow: `0 0 0 ${6 * s}px ${g.color}2e, 0 0 ${30 * s}px ${g.color}66`,
      });
    }
  };
  // Web animations (CSS transitions and keyframes) are stepped by the film clock, not by wall time.
  const born = new WeakMap();
  const stepAnimations = t => {
    for (const a of document.getAnimations()) {
      let b = born.get(a);
      if (b === undefined) { b = t; born.set(a, b); }
      a.pause();
      a.currentTime = Math.max(0, (t - b) * 1000);
    }
  };

  window.film = {
    setCam(x, y, w) { cam = { x, y, w }; layout(); placeRings(); return true; },
    getCam() { return cam; },
    frame(t) { layout(); placeRings(); stepAnimations(t); layout(); placeRings(); return window.scrollY; },
    point(spec, dx = 0.5, dy = 0.5) { const r = rectOf(spec); return r && { x: r.x + r.w * dx, y: r.y + r.h * dy }; },
    caption(text, a) { const c = $('film-caption'); if (c.textContent !== text) c.textContent = text; c.style.opacity = text ? a : 0; return true; },
    hud(text, a) { const h = $('film-hud'); const s = h.querySelector('span'); if (s.textContent !== text) s.textContent = text; h.style.opacity = text ? a : 0; return true; },
    // A photograph across the whole sheet (Ken Burns by the director): s = how many sheet widths wide it is drawn, (fx, fy) the point of the
    // picture, as fractions of it, that sits at (0.5, 0.5) of the frame.
    photoLoad(url) { const img = $('film-photo').firstChild; img.dataset.src = url; img.src = url; return img.decode().then(() => true); },
    photo(a, s, fx, fy) {
      const box = $('film-photo'), img = box.firstChild;
      box.style.opacity = a;
      if (a <= 0 || !img.naturalWidth) return true;
      const W = 1920 * s, H = W * (img.naturalHeight / img.naturalWidth);
      img.style.width = `${W}px`;
      img.style.left = `${Math.min(0, Math.max(1920 - W, 960 - fx * W))}px`;
      img.style.top = `${Math.min(0, Math.max(1080 - H, 540 - fy * H))}px`;
      return true;
    },
    stat(html, a) { const e = $('film-stat'); if (e.innerHTML !== html) e.innerHTML = html; e.style.opacity = html ? a : 0; return true; },
    cursor(x, y, show) { cur = { x, y, show }; layout(); return true; },
    cursorShow(show) { cur.show = show; layout(); return true; },
    getCursor() { return cur; },
    press(p) { press = p; layout(); return true; },
    fade(a) { $('film-fade').style.opacity = a; return true; },
    rect: rectOf,
    ring(key, spec, color, a, radius) { const g = rings.get(key); if (g) Object.assign(g, { spec, color, a, radius }); else if (a > 0) rings.set(key, { spec, color, a, radius }); placeRings(); return true; },
    key(text, a) { const k2 = $('film-key'); if (k2.textContent !== text) k2.textContent = text; const sx = (cur.x - cam.x) / k() + 36, sy = (cur.y - cam.y) / k() + 30; k2.style.transform = `translate(${sx}px, ${sy}px)`; k2.style.opacity = text ? a : 0; return true; },
    scrollY() { return window.scrollY; },
    scrollTo(y) { window.scrollTo(0, y); return window.scrollY; },
    maxScroll() { return document.documentElement.scrollHeight - innerHeight; },
  };
  return true;
})();
