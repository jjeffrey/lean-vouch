"""Write review/seed/: example marks for each person, that show each lean-vouch feature.

Run after generating review/graph.json. Fingerprints come from the current graph, so the marks
are up to date, except where a change is simulated on purpose.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
graph = json.loads((HERE / "graph.json").read_text())
decls, library = graph["declarations"], graph["library"]
info = lambda n: {"own": graph["moduleDocs"].get(n[7:])} if n.startswith("module ") else decls.get(n) or library.get(n)
P = "ReviewExample."


def below(n, seen=None):
    seen = set() if seen is None else seen
    for d in decls.get(n, {}).get("deps", []) + decls.get(n, {}).get("uses", []):
        if d not in seen:
            seen.add(d)
            below(d, seen)
    return seen


# Example marks use fixed dates, so the screenshots don't change from day to day. A thread's notes
# are a minute apart, so they keep their order.
def mark(name, status, *notes, changed=()):
    """A mark made now; `changed` lists dependencies to show as changed since it was made."""
    fp = {"own": info(name)["own"] if info(name) else "0", "deps": {}, "lean": graph["lean"]}
    if info(name):
        fp["deps"] = {d: (info(d) or {}).get("own") for d in below(name)}
    for d in changed:
        fp["deps"][d] = "0000000000000000"
    # Marked at the end of the thread, after reading it.
    return {"status": status, "date": f"2026-11-02T10:{max(len(notes) - 1, 0):02}:00", "fingerprint": fp,
            "notes": [{"by": by, "date": f"2026-11-02T10:{i:02}:00", "text": text} for i, (by, text) in enumerate(notes)]}


marks = {
    "module ReviewExample.Divides": mark("module ReviewExample.Divides", "agree"),
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
    P + "hcf": {"status": "question", "date": "2026-11-01T10:00:00", "notes": [
        {"by": "Ada", "date": "2026-11-01T10:00:00", "text": "Do we still need hcf here?"}]},
    # A question about a proof that still rests on a `sorry`.
    P + "gcd_divides_mul": mark(P + "gcd_divides_mul", "question",
        ("Ada", "The statement looks right, but is this proved?"),
        ("assistant", "Not yet: the proof uses gcd_mul_lcm, which is still a sorry. "
                      "Follow the dashed red arrow to the gap.")),
    P + "lcm": mark(P + "lcm", "agree"),
    # The walkthrough's mismatch: Ada rejects the definition, and an assistant suggests a fix.
    P + "Prime": mark(P + "Prime", "reject",
        ("Ada", "I don't think this works: Prime 1 holds, but 1 is not a prime number. How can we fix this?"),
        ("assistant", "Add the hypothesis 2 ≤ n: Prime n := 2 ≤ n ∧ ∀ d, Divides d n → d = 1 ∨ d = n. "
                      "Prime.coprime still holds, and its proof only needs the second half.")),
    # Agreed before the mistake was noticed: it reads right, but is about the definition above.
    P + "Prime.coprime": mark(P + "Prime.coprime", "agree"),
}
# An assistant raises something: a question after Ada's mark, so it needs her attention.
raised = dict(mark(P + "lcm", "question", ("assistant",
    "With a = b = 0 this divides by gcd 0 0 = 0. Lean's 0 / 0 is 0, so lcm 0 0 = 0, the usual value, "
    "but only by way of that convention. Is that what you want?")), date="2026-11-03T09:00:00")
raised["notes"][0]["date"] = "2026-11-03T09:00:00"

people = {"ada": ("Ada", {}), "assistant": ("assistant", {P + "lcm": {**raised, "notes": []}})}
for name, entry in marks.items():
    people["ada"][1][name] = {key: value for key, value in entry.items() if key != "notes"} | {"notes": []}
    for note in entry["notes"]:
        who = note["by"].lower()
        text = {"date": note["date"], "text": note["text"]}
        people[who][1].setdefault(name, {"status": None, "notes": []})["notes"].append(text)
people["assistant"][1][P + "lcm"]["notes"] = [{key: raised["notes"][0][key] for key in ("date", "text")}]
# Stars, apart from marks: Ada's on the definition everything rests on, and the assistant's on a
# result it finds useful, so a closed folder shows how many are inside.
people["ada"][1][P + "Divides"]["star"] = True
people["assistant"][1][P + "gcd_divides"] = {"status": None, "notes": [], "star": True}

(HERE / "seed").mkdir(exist_ok=True)
for who, (name, entries) in people.items():
    text = json.dumps({"format": 3, "name": name, "marks": entries}, indent=2, ensure_ascii=False, sort_keys=True)
    (HERE / "seed" / f"{who}.json").write_text(text + "\n")
print(f"wrote {sum(len(entries) for _, entries in people.values())} entries")
