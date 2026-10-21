// Installed before the app's own scripts run (app pages only). Two things, both about the film clock:
//  1. A virtual clock: performance.now() and setInterval follow window.__vclock.set(ms), so the app's "Elapsed" counter
//     shows the recorded run's own seconds instead of the seconds it takes to capture a frame.
//  2. POST /api/audit is answered from the recorded run (window.__replay = {document, mode, events:[{t, event}]}),
//     as a real streaming Response, chunk by chunk as the film clock reaches each event's recorded arrival time.
//     The request must be exactly the one that was recorded, or the replay refuses. Nothing else is touched.
(() => {
  if (window.__vclock) return;
  let vms = 0, nid = 1;
  const intervals = new Map();
  Object.defineProperty(Performance.prototype, 'now', { configurable: true, value() { return vms; } });
  window.setInterval = (fn, ms) => { const id = nid++; intervals.set(id, { fn, ms, next: vms + ms }); return id; };
  window.clearInterval = id => { intervals.delete(id); };
  window.__vclock = {
    set(ms) { vms = ms; for (const iv of intervals.values()) { while (iv.next <= vms) { iv.next += iv.ms; iv.fn(); } } return vms; },
    get: () => vms,
  };
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.pathname === '/api/audit') {
      const R = window.__replay;
      if (!R) return Promise.reject(new Error('film: no recorded run is loaded'));
      let body;
      try { body = JSON.parse(init.body); } catch { return Promise.reject(new Error('film: unreadable request')); }
      if (body.document !== R.document || body.mode !== R.mode) return Promise.reject(new Error('film: this is not the recorded request'));
      const enc = new TextEncoder();
      const stream = new ReadableStream({ start(c) { R.ctl = c; } });
      R.enc = enc; R.sent = 0; R.closed = false; R.requested = true;
      return Promise.resolve(new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
    }
    return realFetch(input, init);
  };
  // push every recorded event whose arrival time is <= r (seconds since the request); returns how many were pushed
  window.__replayTo = r => {
    const R = window.__replay;
    if (!R || !R.ctl) return 0;
    let n = 0;
    while (R.sent < R.events.length && R.events[R.sent].t <= r) {
      R.ctl.enqueue(R.enc.encode('data: ' + JSON.stringify(R.events[R.sent].event) + '\n\n'));
      R.sent++; n++;
    }
    if (R.sent >= R.events.length && !R.closed) { R.closed = true; R.ctl.close(); }
    return n;
  };
})();
