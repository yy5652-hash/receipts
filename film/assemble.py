"""Builds the soundtrack and the film: narration + a synthesised score + synthesised foley, mixed to -14 LUFS,
then encoded with the recorded frames. Adapted from the Takeable film pipeline.

    python assemble.py [--out out/receipts-demo.mp4]

Inputs: work/timeline.json (narrate.py), work/frames + work/events.json (director.mjs).
Outputs: the mp4, a smaller web copy, an .srt next to it, and a contact sheet with one frame every 5 seconds.

Every sound here is made from oscillators and noise (the media kit's build_music.py and the instruments below,
adapted from relay-home-film/assemble.py). No samples, no existing music.
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
KIT = Path("/Users/hudsonyu/Documents/Codex/2026-09-28/referenced-chatgpt-conversation-this-is-an-4/outputs/submission-assets")
sys.path.insert(0, str(KIT))
import build_music as B  # noqa: E402
import nw_media as M  # noqa: E402

SR = M.SR
FPS = 30
MOTIF = {0: [(0, 76), (2, 72)], 1: [(0, 69), (2.5, 72)], 2: [(0, 67), (2, 76)], 3: [(0, 74), (2, 71)]}


# ---------------------------------------------------------------- instruments
def felt(m, dur):
    """A soft piano-like note: decaying partials, slightly stretched, behind a small hammer noise."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = B.hz(m)
    y = np.zeros(n)
    for k, amp in enumerate([1.0, 0.5, 0.28, 0.14, 0.08, 0.04], start=1):
        fk = f * k * (1 + 0.0004 * k * k)
        if fk > 6000:
            break
        y += amp * np.sin(2 * np.pi * fk * t + B.rng.uniform(0, 6.28)) * np.exp(-t / (dur * 0.42 / (1 + 0.35 * (k - 1))))
    hammer = B.fft_filter(B.rng.standard_normal(n), lo=600, hi=3200) * np.exp(-t / 0.006) * 0.08
    return (y * 0.4 + hammer) * B.adsr(n, 0.004, 0.02, 1.0, 0.05)


def clack():
    """One split-flap leaf falling."""
    n = int(0.09 * SR)
    t = np.arange(n) / SR
    f = B.rng.uniform(1300, 2100)
    knock = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.006)
    click = B.fft_filter(B.rng.standard_normal(n), lo=2500, hi=9000) * np.exp(-t / 0.0025)
    thud = np.sin(2 * np.pi * 240 * t) * np.exp(-t / 0.018) * 0.35
    return knock * 0.35 + click * 0.55 + thud


def mouse():
    n = int(0.06 * SR)
    t = np.arange(n) / SR
    click = B.fft_filter(B.rng.standard_normal(n), lo=1800, hi=7000) * np.exp(-t / 0.0022)
    body = np.sin(2 * np.pi * 900 * t) * np.exp(-t / 0.004)
    return click * 0.6 + body * 0.25


def key():
    n = int(0.05 * SR)
    t = np.arange(n) / SR
    return B.fft_filter(B.rng.standard_normal(n), lo=1500, hi=6000) * np.exp(-t / 0.003) * 0.5 + np.sin(2 * np.pi * 420 * t) * np.exp(-t / 0.008) * 0.2


# ---------------------------------------------------------------- tracks
def narration(timeline, total):
    out = np.zeros(int((total + 1) * SR))
    for scene in timeline["scenes"]:
        for line in scene["lines"]:
            for part in line["parts"]:                      # one wav per sentence
                x = M.read_wav(HERE / "work/voice" / part["wav"])
                x = x / max(1e-9, np.sqrt(np.mean(x[np.abs(x) > 0.01] ** 2)))   # every sentence at the same loudness
                i = int(round(part["start"] * SR))
                out[i:i + len(x)] += x[: len(out) - i]
    return out


def rms_env(x, win):
    k = np.ones(max(1, int(win * SR))) / max(1, int(win * SR))
    return np.sqrt(np.convolve(x ** 2, k, mode="same") + 1e-12)


def follow(target, attack, release):
    """One-pole smoothing of a gain curve, worked out once a millisecond (fast down, slow up)."""
    blk = int(0.001 * SR)
    n = -(-len(target) // blk)
    t = np.pad(target, (0, n * blk - len(target)), mode="edge").reshape(n, blk).min(axis=1)
    a, r = 1 - np.exp(-0.001 / attack), 1 - np.exp(-0.001 / release)
    g = np.empty(n); g[0] = t[0]
    for i in range(1, n):
        g[i] = g[i - 1] + (t[i] - g[i - 1]) * (a if t[i] < g[i - 1] else r)
    return np.repeat(g, blk)[: len(target)]


def voice_chain(x):
    """Broadcast-style narration: rumble cut, sibilance tamed, levels evened, presence for clarity, a small room.
    Returns stereo."""
    x = B.fft_filter(x, lo=90)
    # de-esser: pull the 5.5–10 kHz band down only where it jumps above the voice around it
    band = B.fft_filter(x, lo=5500, hi=10000)
    es, ev = rms_env(band, 0.005), rms_env(x, 0.03)
    g = np.clip((0.32 * ev) / es, 0.35, 1.0)
    x = x - band * (1 - follow(g, 0.002, 0.06))
    # compressor, 3:1 above the speech's 60th-percentile level, so first and last syllables are not lost
    env = rms_env(x, 0.015)
    lvl = 20 * np.log10(env)
    speech = lvl > lvl.max() - 45
    thr = np.percentile(lvl[speech], 60)
    want = np.where(lvl > thr, thr + (lvl - thr) / 3 - lvl, 0.0)
    x = x * follow(10 ** (want / 20), 0.005, 0.12)
    # presence: about +3 dB from 2.5 to 8 kHz
    x = x + 0.4 * B.fft_filter(x, lo=2500, hi=8000)
    st = np.stack([x, x], axis=1) * 0.7071
    return st + B.reverb(st, seconds=0.55, predelay=0.012) * 0.09


def score(total, holds):
    """Slow chords and two felt-piano notes a bar; silent for a beat before each held moment, then a bell."""
    stem = B.Stem(total + 4)
    for b in range(int(total / B.BAR) + 2):
        t0 = b * B.BAR
        chord = [m - 12 for m in B.CHORDS[b % 4]]
        stem.add(t0, B.supersaw(chord, B.BAR * 1.12, 760, attack=0.9, release=1.4, voices=5, spread=9.0), 0.24)
        for k, (beat, note) in enumerate(MOTIF[b % 4]):
            stem.add(t0 + B.BEAT * beat, B.pan(felt(note, 2.8), 0.22 if k else -0.18), 0.2 if k else 0.26)
    bed = B.reverb(stem.buf, seconds=3.2)
    env = np.ones(len(bed))
    t = np.arange(len(bed)) / SR
    for h in holds:                      # music drops out a second before the held moment and comes back under it
        down = np.clip(((h - 1.0) - t) / 0.25, 0, 1)             # 1 until h - 1.25 s, 0 from h - 1.0 s
        up = np.clip((t - (h + 0.4)) / 2.5, 0, 1)                 # back over 2.5 s after the bell
        env *= np.maximum(down, up)
    bed *= env[:, None]
    for h in holds:
        hit = B.Stem(6)
        hit.add(0, B.pan(B.pluck(93, 2.4, 3200), -0.1), 0.42)
        hit.add(0.06, B.pan(B.pluck(81, 2.8, 2600), 0.15), 0.3)
        hit.add(0, B.pan(felt(45, 4.0), 0.0), 0.5)
        hit.add(0, B.impact(), 0.8)                        # a low boom under the number
        tail = B.reverb(hit.buf, seconds=2.6)
        i = int(h * SR)
        bed[i:i + len(tail)] += tail[: len(bed) - i] * 1.1
        rise = B.noise_sweep(1.6, rising=True)              # air rising into the silence before it
        j = int((h - 1.75) * SR)
        if rise.ndim == 1:
            rise = np.stack([rise, rise], axis=1) * 0.7071
        bed[j:j + len(rise)] += rise[: len(bed) - j] * 0.2
    return bed * (0.9 / max(1e-9, np.abs(bed).max()))


def arranged(timeline, holds):
    """The score, bar by bar, following the film's scenes: a sparse opening, a quiet pulse under the product, a build
    under the pipeline, a brighter benchmark, and a C-major landing. Silent for a beat before each held moment."""
    total = timeline["total"]
    scenes = timeline["scenes"]
    at = lambda t: next((s["id"] for s in scenes if s["start"] <= t < s["end"]), scenes[-1]["id"])
    end_start = next(s["start"] for s in scenes if s["id"] == "end")
    pads, keys, drums = B.Stem(total + 4), B.Stem(total + 4), B.Stem(total + 4)
    PULSE = {"paste": 1, "findings": 1, "papers": 1, "fix": 1, "how": 2, "bench": 1}
    BRIGHT = {"title", "bench", "end", "how"}
    C_MAJOR = [48, 52, 55, 60]
    b = 0
    while b * B.BAR < total + B.BAR:
        t0 = b * B.BAR
        sid = at(t0 + 0.01)
        chord = C_MAJOR if t0 >= end_start - 0.3 else B.CHORDS[b % 4]
        lift = 0 if sid in BRIGHT else -12
        pads.add(t0, B.supersaw([m + lift for m in chord], B.BAR * 1.12, 900 if sid in BRIGHT else 700,
                                attack=0.9, release=1.4, voices=5, spread=9.0), 0.22)
        motif = MOTIF[b % 4] if t0 < end_start - 0.3 else [(0, 72), (2, 76)]
        for k, (beat, note) in enumerate(motif):
            keys.add(t0 + B.BEAT * beat, B.pan(felt(note, 2.8), 0.22 if k else -0.18), 0.2 if k else 0.26)
        level = PULSE.get(sid, 0)
        if level:
            root = (C_MAJOR if t0 >= end_start - 0.3 else B.CHORDS[b % 4])[0] - 12
            drums.add(t0, B.pan(B.bass_note(root, B.BAR * 0.9), 0.0), 0.16)
            steps = 8 if level >= 2 else 4
            for i in range(steps):
                tt = t0 + i * B.BAR / steps
                drums.add(tt, B.pan(tick(i % (steps // 2) == 0), -0.2 if i % 2 else 0.2), 0.05)
            if level >= 2:
                for beat in (0, 2):
                    drums.add(t0 + beat * B.BEAT, B.kick(), 0.09)
        b += 1
    # the pulse steps back while the narrator speaks (transients mask short words), and returns between lines
    talk = np.zeros(len(drums.buf))
    for sc in scenes:
        for l in sc["lines"]:
            talk[int(max(0, l["start"] - 0.2) * SR):int((l["end"] + 0.15) * SR)] = 1
    k = np.hanning(int(0.25 * SR)); talk = np.convolve(talk, k / k.sum(), mode="same")
    drums.buf *= (1 - 0.75 * talk)[:, None]
    bed = B.reverb(pads.buf + keys.buf, seconds=3.2) + drums.buf * 0.9 + B.reverb(drums.buf, seconds=0.9) * 0.25
    t = np.arange(len(bed)) / SR
    env = np.ones(len(bed))
    for h in holds:
        env *= np.maximum(np.clip(((h - 1.0) - t) / 0.25, 0, 1), np.clip((t - (h + 0.4)) / 2.5, 0, 1))
    bed *= env[:, None]
    for h in holds:                      # the number lands: a soft bell and a low note, not a boom
        hit = B.Stem(6)
        hit.add(0, B.pan(B.pluck(93, 2.4, 3200), -0.1), 0.34)
        hit.add(0.06, B.pan(B.pluck(81, 2.8, 2600), 0.15), 0.24)
        hit.add(0, B.pan(felt(45, 4.0), 0.0), 0.5)
        hit.add(0, B.impact(), 0.4)
        tail = B.reverb(hit.buf, seconds=2.6)
        i = int(h * SR)
        bed[i:i + len(tail)] += tail[: len(bed) - i] * 1.1
        rise = B.noise_sweep(1.6, rising=True)
        j = int((h - 1.75) * SR)
        bed[j:j + len(rise)] += rise[: len(bed) - j] * 0.14
    # a bell when the wordmark appears (title), and when the name lands at the end
    for when in (next(s["start"] for s in scenes if s["id"] == "title") + 0.5, next(l["start"] for sc in scenes for l in sc["lines"] if l["id"] == "z1")):
        bell = B.Stem(8)
        for k, m in enumerate([72, 76, 79, 84]):
            bell.add(k * 0.09, B.pan(felt(m, 5.0), -0.3 + 0.2 * k), 0.3)
        ring = B.reverb(bell.buf, seconds=3.5)
        i = int((when - 0.3) * SR)
        bed[i:i + len(ring)] += ring[: len(bed) - i] * 0.9
    return bed * (0.9 / max(1e-9, np.abs(bed).max()))


def tick(high):
    """A watch tick: a short high knock."""
    n = int(0.05 * SR)
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * (2400 if high else 1900) * t) * np.exp(-t / 0.006)
    click = B.fft_filter(B.rng.standard_normal(n), lo=2500, hi=8000) * np.exp(-t / 0.002)
    return body * 0.45 + click * 0.5


def chime():
    """Two soft notes when the verdicts re-sort after a choice."""
    st = B.Stem(3)
    st.add(0, B.pan(felt(88, 1.6), -0.2), 0.35)
    st.add(0.07, B.pan(felt(91, 1.8), 0.2), 0.3)
    return B.reverb(st.buf, seconds=1.4) * 0.8 + st.buf


def shimmer(total, spans):
    """High felt-piano arpeggios over the wide globe shots, for a sense of scale."""
    stem = B.Stem(total + 4)
    notes = [81, 84, 88, 91, 88, 84]
    for a, z in spans:
        t, k = a, 0
        while t < z:
            stem.add(t, B.pan(felt(notes[k % len(notes)], 2.2), -0.35 + 0.7 * ((k * 0.37) % 1)), 0.12)
            t += B.BEAT / 2
            k += 1
    return B.reverb(stem.buf, seconds=3.6)


def whoosh(dur=1.6):
    """Filtered noise that swells and falls, for the Earth turning."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = B.fft_filter(B.rng.standard_normal(n), lo=180, hi=2400)
    env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 2
    return x * env * 0.5


VERDICT_NOTES = {"supported": [88, 91, 84], "partial": [79], "contradicted": [69, 64], "not_in_source": [76], "source_unavailable": [67]}


def foley(total, events):
    """Mouse clicks, a burst of keys for the paste, and a soft note for each verdict as it lands (C-major pentatonic)."""
    stem = B.Stem(total + 4)
    n = {}
    for e in events:
        if e["kind"] == "click":
            stem.add(e["t"], B.pan(mouse(), 0.05), 0.45)
        elif e["kind"] == "paste":
            for i in range(9):
                stem.add(e["t"] + i * 0.045, B.pan(key(), -0.1 + 0.02 * i), 0.3)
            stem.add(e["t"] + 0.45, B.pan(mouse(), 0.0), 0.3)
        elif e["kind"] == "verdict":
            notes = VERDICT_NOTES[e["label"]]
            k = n.get(e["label"], 0)
            n[e["label"]] = k + 1
            stem.add(e["t"], B.pan(felt(notes[k % len(notes)], 1.3), (-0.3 if e["label"] == "supported" else 0.3)), 0.2)
    return stem.buf


def limit(x, ceiling, look=0.004, release=0.09):
    """Look-ahead limiter (from relay-home-film/assemble.py)."""
    block = int(0.001 * SR)
    mono = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    n = -(-len(mono) // block)
    peak = np.pad(mono, (0, n * block - len(mono))).reshape(n, block).max(axis=1)
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-9))
    r = max(1, int(round(look / 0.001)))
    held = np.lib.stride_tricks.sliding_window_view(np.pad(need, r + 1, mode="edge"), 2 * r + 3).min(axis=1)
    k = 1 - np.exp(-0.001 / release)
    g = held.copy()
    for i in range(1, n):
        g[i] = min(held[i], g[i - 1] + (1 - g[i - 1]) * k)
    g = np.convolve(np.pad(g, r, mode="edge"), np.ones(2 * r + 1) / (2 * r + 1), mode="valid")
    g = np.repeat(g, block)[: len(mono)]
    return x * (g[:, None] if x.ndim == 2 else g)


def loudness(path):
    said = subprocess.run([M.FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
    said = said[said.rindex("Summary:"):]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", said).group(1)), float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", said).group(1))


def mix_down(voice, bed, fx, out, total, duck_db=-14.0, music_gain=0.55, fx_gain=0.6, target=-14.0, ceiling_db=-3.4):
    n = int(total * SR)
    fit = lambda x: np.concatenate([x, np.zeros((max(0, n - len(x)),) + x.shape[1:])])[:n]
    voice, bed, fx = fit(voice), fit(bed), fit(fx)
    mono = voice.mean(axis=1) if voice.ndim == 2 else voice
    win = int(0.05 * SR)
    env = np.sqrt(np.convolve(mono ** 2, np.ones(win) / win, mode="same"))
    on = (env > 0.08 * env.max() / 4).astype(np.float64)
    look = int(0.18 * SR)                                   # the music starts to dip 0.18 s before a word
    on = np.maximum(on, np.concatenate([on[look:], np.zeros(look)]))
    active = np.minimum(1, np.convolve(on, np.ones(int(0.5 * SR)), mode="same"))
    kk = np.hanning(int(0.35 * SR))
    active = np.convolve(active, kk / kk.sum(), mode="same")
    tail = np.ones(n)
    fade = int(2.5 * SR)
    tail[-fade:] = np.linspace(1, 0, fade) ** 2
    head = np.ones(n)
    head[: int(0.6 * SR)] = np.linspace(0, 1, int(0.6 * SR))
    v = voice / max(1e-9, np.abs(voice).max()) * 0.84
    if v.ndim == 1:
        v = np.stack([v, v], axis=1)
    spoken = v[np.abs(v).max(axis=1) > 0.02]
    v = limit(v, np.sqrt((spoken ** 2).mean()) * 10 ** (12 / 20)) if len(spoken) else v
    mix = bed * (10 ** (duck_db * active / 20) * tail * head * music_gain)[:, None] + fx * fx_gain + v
    ceiling = 10 ** (ceiling_db / 20)
    gain = ceiling / np.abs(mix).max()
    for _ in range(6):
        done = limit(mix * gain, ceiling)
        M.write_wav(out, done)
        lufs, tp = loudness(out)
        if abs(lufs - target) < 0.15:
            break
        gain *= 10 ** ((target - lufs) / 20)
    print(f"loudness {lufs:.1f} LUFS, true peak {tp:.1f} dBTP")
    return done


def srt(timeline, path):
    def ts(s):
        h, r = divmod(s, 3600)
        m, r = divmod(r, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(r):02d},{int(round((r - int(r)) * 1000)):03d}"
    rows = [l for s in timeline["scenes"] for l in s["lines"]]
    Path(path).write_text("".join(f"{i}\n{ts(l['start'])} --> {ts(l['end'] + 0.3)}\n{l['text']}\n\n" for i, l in enumerate(rows, 1)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/receipts-demo.mp4")
    ap.add_argument("--audio-only", action="store_true", help="write work/mix.wav and stop")
    a = ap.parse_args()
    timeline = json.loads((HERE / "work/timeline.json").read_text())
    events = json.loads((HERE / "work/events.json").read_text())
    total = timeline["total"]
    frames = sorted((HERE / "work/frames").glob("*.jpg"))
    if abs(len(frames) / FPS - total) > 0.5:
        raise SystemExit(f"{len(frames)} frames = {len(frames) / FPS:.1f} s but the timeline is {total:.1f} s")
    if total >= 175:
        raise SystemExit(f"the film is {total:.1f} s; the limit is 2:55")
    holds = [e["t"] for e in events["events"] if e["kind"] == "hit"]
    voice = voice_chain(narration(timeline, total))
    bed = arranged(timeline, holds)
    fx = foley(total, events["events"])
    wav = HERE / "work/mix.wav"
    mix_down(voice, bed, fx, wav, total)
    M.write_wav(HERE / "work/voice-processed.wav", voice / np.abs(voice).max() * 0.8)
    if a.audio_only:
        return
    out = HERE / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([M.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(FPS), "-i", str(HERE / "work/frames/%05d.jpg"), "-i", str(wav),
                    # the frames are full-range sRGB JPEGs; deliver standard BT.709 limited range like any HD video
                    "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709:flags=lanczos,format=yuv420p",
                    "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-profile:v", "high", "-r", str(FPS),
                    "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-shortest", str(out)], check=True)
    srt(timeline, out.with_suffix(".srt"))
    subprocess.run([sys.executable, str(HERE / "tools/sheet.py"), str(HERE / "work/frames"), str(out.with_name(out.stem + "-sheet.jpg")), "5", "384", f"0::{6 * FPS}"], check=True)
    print(out, f"{M.probe_duration(out):.2f} s", f"{out.stat().st_size / 2**20:.1f} MiB")


if __name__ == "__main__":
    main()
