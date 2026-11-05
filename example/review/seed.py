"""Write review/marks.seed.json: example marks that show each lean-review feature.

Run after generating review/graph.json. Fingerprints come from the current graph, so the marks
are up to date, except where a change is simulated on purpose.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
graph = json.loads((HERE / "graph.json").read_text())
decls, library = graph["declarations"], graph["library"]
info = lambda n: decls.get(n) or library.get(n)
P = "ReviewExample."


def below(n, seen=None):
    seen = set() if seen is None else seen
    for d in decls.get(n, {}).get("deps", []) + decls.get(n, {}).get("uses", []):
        if d not in seen:
            seen.add(d)
            below(d, seen)
    return seen


# Example marks use fixed dates, so the screenshots don't change from day to day.
def mark(name, status, *notes, changed=()):
    """A mark made now; `changed` lists dependencies to show as changed since it was made."""
    fp = {"own": info(name)["own"] if info(name) else "0", "deps": {}}
    if info(name):
        fp["deps"] = {d: (info(d) or {}).get("own") for d in below(name)}
    for d in changed:
        fp["deps"][d] = "0000000000000000"
    return {"status": status, "date": "2026-11-02", "fingerprint": fp,
            "notes": [{"by": by, "date": "2026-11-02", "text": text} for by, text in notes]}


marks = {
    "module ReviewExample.Divides": mark("module", "agree"),
    P + "Divides": mark(P + "Divides", "agree",
        ("Ada", "Why define this as ∃ k, n = d * k rather than n % d = 0?"),
        ("assistant", "They agree for every d and n, but the existential form is what proofs need: "
                      "to show d divides n you give the k, and from a proof you get the k back. It is "
                      "also how Mathlib defines divisibility."),
        ("Ada", "So 0 divides only 0, and every number divides 0?"),
        ("assistant", "Yes. Both are now theorems: Divides.zero (every d divides 0) in this module, "
                      "and Divides.zero_left (0 divides n exactly when n = 0) in ReviewExample.Zero.")),
    P + "Divides.refl": mark(P + "Divides.refl", "agree"),
    P + "Divides.zero": mark(P + "Divides.zero", "agree"),
    P + "Divides.of_mod": mark(P + "Divides.of_mod", "agree"),
    P + "Divides.mod": mark(P + "Divides.mod", "agree"),
    P + "gcd": mark(P + "gcd", "question",
        ("Ada", "gcd 0 0 = 0. Is that right?"),
        ("assistant", "It is the usual convention: every number divides 0, and 0 is the greatest in "
                      "the divisibility order. divides_gcd states exactly that.")),
    "Nat.mod": mark("Nat.mod", "agree",
        ("Ada", "m % 0 = m. Noted; gcd never takes a remainder by 0.")),
    # Marked under the old name; the server moves it to gcd_divides (a deprecated alias says so).
    P + "gcd_dvd": mark(P + "gcd_divides", "agree"),
    # Marked before Divides changed below it: flagged for re-checking.
    P + "divides_gcd": mark(P + "divides_gcd", "agree", changed=[P + "Divides"]),
    # A declaration that was removed: listed under orphaned notes.
    P + "hcf": {"status": "question", "date": "2026-11-01", "notes": [
        {"by": "Ada", "date": "2026-11-01", "text": "Do we still need hcf here?"}]},
    # A statement can be agreed while its proof still rests on a `sorry`.
    P + "gcd_divides_mul": mark(P + "gcd_divides_mul", "agree",
        ("Ada", "The statement is right, but is this proved?"),
        ("assistant", "Not yet: the proof uses gcd_mul_lcm, which is still a sorry. The mark stands, since "
                      "it's about the statement. Follow the dashed red arrow to the gap.")),
}
(HERE / "marks.seed.json").write_text(json.dumps(marks, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
print(f"wrote {len(marks)} marks")
