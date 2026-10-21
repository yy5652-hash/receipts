"""Voices the narration -> work/voice/<hash>.wav and work/timeline.json (scene, line and sentence times).

One voice: Kokoro-82M (Apache-2.0), af_heart, through the shared media kit (nw_media.speak). Every number in the
script comes from work/facts.json, in figures for the caption and in words for the voice.

Timeline: each scene starts with `lead` seconds of picture, then its lines, each after `gap` seconds of quiet (or at
`at`, if later), and ends `tail` seconds after the last line. A line is voiced one sentence at a time, so the art
scenes can light things up on a sentence.
"""
import hashlib, json, sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
KIT = Path("/Users/hudsonyu/Documents/Codex/2026-09-28/referenced-chatgpt-conversation-this-is-an-4/outputs/submission-assets")
sys.path.insert(0, str(KIT))
import nw_media as M  # noqa: E402

M.ESPEAK_DATA = HERE / "work/espeak-data"          # keep the phonemizer's data next to the project

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
ORD = {12: "twelfth", 10: "tenth", 8: "eighth", 5: "fifth", 4: "quarter", 3: "third", 2: "half", 20: "twentieth"}


def words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return ONES[n // 100] + " hundred" + (" and " + words(n % 100) if n % 100 else "")
    return words(n // 1000) + " thousand" + ((" and " if n % 1000 < 100 else " ") + words(n % 1000) if n % 1000 else "")


def decimal_words(x: float) -> str:
    whole, frac = f"{x:.1f}".split(".")
    return words(int(whole)) + (" point " + " ".join(ONES[int(d)] for d in frac) if frac != "0" else "")


def fill(text, values):
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    if "{" in text:
        raise SystemExit(f"unfilled value in: {text}")
    return text


def values_from(facts):
    pct = lambda s: s.rstrip("%")
    cents = facts["ultra_cost_cents"]                       # 0.3 -> "three tenths of a cent"
    tenths = {1: "a tenth", 2: "two tenths", 3: "three tenths", 4: "four tenths", 5: "half", 6: "six tenths", 7: "seven tenths", 8: "eight tenths", 9: "nine tenths"}
    assert 0 < cents < 1 and round(cents * 10) in tenths, cents
    return {
        "cases": f"{facts['cases']:,}", "cases_words": words(facts["cases"]),
        "wrong_growth": facts["wrong_growth"], "wrong_growth_words": words(int(pct(facts["wrong_growth"]))) + " percent",
        "real_growth": facts["real_growth"], "real_growth_words": words(int(pct(facts["real_growth"]))) + " percent",
        "items": facts["items"], "items_words": words(facts["items"]),
        "sources": facts["sources"], "sources_words": words(facts["sources"]),
        "ultra_k": facts["ultra_k"], "ultra_k_words": words(facts["ultra_k"]),
        "ultra_n": facts["ultra_n"], "ultra_n_words": words(facts["ultra_n"]),
        "ultra_cost": f"${facts['ultra_cost_per_claim']:.3f}", "ultra_cost_words": tenths[round(cents * 10)] + " of a cent",
        "nano_macro": f"{facts['nano_macro']}%", "nano_macro_words": decimal_words(facts["nano_macro"]) + " percent",
        "nano_fraction_word": ORD[facts["nano_fraction"]],
    }


def main():
    script = json.loads((HERE / "script.json").read_text())
    facts = json.loads((HERE / "work/facts.json").read_text())
    values = values_from(facts)
    (HERE / "work/voice").mkdir(parents=True, exist_ok=True)
    t = 0.0
    scenes = []
    for scene in script["scenes"]:
        start = t
        t += scene["lead"]
        lines = []
        for line in scene["lines"]:
            t = max(t + line.get("gap", 0.0), start + line.get("at", 0.0))
            shown = fill(line["text"], values)
            spoken = fill(line.get("spoken", line["text"]), values)
            parts, cur, first = [], t, True
            for s in M.sentences(spoken):
                key = hashlib.sha1(f"{s}|{script['voice']}|{script['rate']}".encode()).hexdigest()[:12]
                wav = HERE / f"work/voice/{key}.wav"
                x = M.speak(s, wav, script["voice"], script["rate"])
                if not first:
                    cur += 0.16
                first = False
                parts.append({"spoken": s, "wav": wav.name, "start": round(cur, 3), "end": round(cur + len(x) / M.SR, 3)})
                cur += len(x) / M.SR
            lines.append({"id": line["id"], "text": shown, "spoken": spoken, "start": round(t, 3), "end": round(cur, 3), "parts": parts})
            t = cur
        t += scene["tail"]
        scenes.append({"id": scene["id"], "kind": scene["kind"], "page": scene.get("page"), "start": round(start, 3), "end": round(t, 3), "lines": lines})
    out = {"total": round(t, 3), "values": values, "scenes": scenes}
    (HERE / "work/timeline.json").write_text(json.dumps(out, indent=1))
    for s in scenes:
        print(f"{s['id']:9s} {s['start']:7.2f}-{s['end']:7.2f}  ({s['end'] - s['start']:5.1f} s)  " + " ".join(f"{l['id']}[{l['start'] - s['start']:.1f}-{l['end'] - s['start']:.1f}]" for l in s["lines"]))
    print(f"total {t:.1f} s ({int(t // 60)}:{t % 60:04.1f})")


if __name__ == "__main__":
    main()
