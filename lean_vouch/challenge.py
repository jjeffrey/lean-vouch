"""Write a challenge for `lake comparator`: the theorems you choose, as statements, with the project's
definitions they rest on. Then check that it says exactly what the project says.

A theorem means what its statement says, given what each definition in it means, so a challenge holds
the statements of the chosen theorems and everything those statements use from the project, in full,
but no proofs. The text comes from the project's own source. Copied out of its file, a declaration can
mean something else, through an `open` or a `variable` it relied on, so the challenge is compiled and
fingerprinted as the project is, and every declaration compared.
"""
import json
import os
import re
import subprocess
from pathlib import Path

# A declaration's header: modifiers and attributes, then its keyword and the name as written.
HEADER = re.compile(
    r"^(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable|partial|unsafe|nonrec)\s+)*"
    r"(def|theorem|lemma|abbrev|instance|structure|class|inductive|opaque|axiom)\b\s*([^\s:({\[⦃]*)", re.M)
OPEN = re.compile(r"^open\s.*$", re.M)
IMPORT = re.compile(r"^(?:public\s+)?(?:meta\s+)?import\s+(\S+)", re.M)
STANDARD_AXIOMS = ["propext", "Quot.sound", "Classical.choice"]


# The chosen theorems are the project's, not one person's, so they're kept in one file that git
# tracks: {"format": 1, "theorems": [name]}.
TARGETS_FORMAT = 1


class ChallengeError(Exception):
    pass


def chosen(path):
    """The theorems chosen for the challenge, in the order they were chosen."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names = data.get("theorems") if isinstance(data, dict) and data.get("format") == TARGETS_FORMAT else None
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


def choose(path, names):
    """Save the chosen theorems, swapping the file in whole."""
    unique = list(dict.fromkeys(names))
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"format": TARGETS_FORMAT, "theorems": unique}, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)
    return unique


def closure(graph, targets):
    """The targets and every project declaration below them. A theorem's dependencies are its
    statement's, so no proof is followed."""
    decls = graph["declarations"]
    missing = [name for name in targets if name not in decls]
    if missing:
        raise ChallengeError(f"not in the project's graph: {', '.join(missing)}")
    wrong = [name for name in targets if decls[name]["kind"] != "theorem"]
    if wrong:
        raise ChallengeError(f"a challenge's targets are theorems; these aren't: {', '.join(wrong)}")
    needed, stack = set(), list(targets)
    while stack:
        name = stack.pop()
        if name in needed or name not in decls:
            continue
        needed.add(name)
        stack.extend(decls[name]["deps"])
    return needed


def ordered(graph, names):
    """Dependencies first, and otherwise in the order of the project's modules and lines."""
    decls, modules = graph["declarations"], list(graph.get("modules", {}))
    place = lambda name: (modules.index(decls[name]["module"]) if decls[name]["module"] in modules else len(modules),
                          (decls[name].get("lines") or [0])[0], name)
    done, out = set(), []

    def visit(name, path=()):
        if name in done:
            return
        if name in path:
            raise ChallengeError(f"{name} depends on itself through {', '.join(path)}; mutual declarations aren't supported yet")
        for dep in sorted((d for d in decls[name]["deps"] if d in names), key=place):
            visit(dep, path + (name,))
        done.add(name)
        out.append(name)

    for name in sorted(names, key=place):
        visit(name)
    return out


def proof_start(text):
    """Where a theorem's proof starts: the first `:=` outside brackets, or a `|` case starting a
    line. As `reviewProofStart` in the pages."""
    depth, i = 0, 0
    while i < len(text):
        two = text[i:i + 2]
        if two == "--":
            i = text.find("\n", i)
            if i < 0:
                return -1
        elif two == "/-":
            i = text.find("-/", i) + 1
            if i <= 0:
                return -1
        elif text[i] in "([{⟨⦃":
            depth += 1
        elif text[i] in ")]}⟩⦄":
            depth -= 1
        elif depth == 0 and two == ":=":
            return i
        elif depth == 0 and text[i] == "|":
            line = text.rfind("\n", 0, i) + 1
            if not text[line:i].strip():
                return line
        i += 1
    return -1


def source(graph, folders, name):
    """A declaration's text and its file's text, from the last build's line numbers."""
    decl = graph["declarations"][name]
    path = Path(decl["module"].replace(".", "/") + ".lean")
    folder = next((folder for folder in folders if (folder / path).exists()), None)
    if folder is None or not decl.get("lines"):
        raise ChallengeError(f"can't find the source of {name} ({path})")
    whole = (folder / path).read_text(encoding="utf-8")
    lines = whole.split("\n")
    start, end = decl["lines"][0] - 1, decl["lines"][1]
    return "\n".join(lines[start:end]), "\n".join(lines[:start]), whole


def namespace_of(name, text):
    """The namespace a declaration was written in: its full name, less the name as written."""
    header = HEADER.search(text)
    written = header[2] if header and header[2] else None
    if written and name.endswith("." + written):
        return name[: -len(written) - 1]
    if written == name:
        return ""
    return name.rsplit(".", 1)[0] if "." in name else ""   # an instance with no written name


def block(name, text, before, theorem):
    """One declaration, with the `open`s above it in its file, and the namespace it goes in."""
    if theorem:
        cut = proof_start(text)
        if cut < 0:
            raise ChallengeError(f"can't find where the proof of {name} starts")
        text = text[:cut].rstrip() + " := by\n  sorry"
    opens = OPEN.findall(before)
    return namespace_of(name, text), "\n".join([*opens, text]) if opens else text


def grouped(blocks):
    """Neighbors in one namespace share a `namespace` block."""
    groups = []
    for namespace, body in blocks:
        if groups and groups[-1][0] == namespace:
            groups[-1][1].append(body)
        else:
            groups.append((namespace, [body]))
    return ["\n\n".join(bodies) if not namespace else f"namespace {namespace}\n\n" + "\n\n".join(bodies) + f"\n\nend {namespace}"
            for namespace, bodies in groups]


def write(graph, folders, targets, out):
    """Write `Challenge.lean` and `comparator.json` into `out`, and return what went into the challenge."""
    decls, project = graph["declarations"], set(graph["modules"])
    names = ordered(graph, closure(graph, targets))
    private = [name for name in names if name.startswith("_private.")]
    if private:
        raise ChallengeError(f"private declarations can't be copied into a challenge yet: {', '.join(private)}")
    imports, blocks, warnings = [], [], []
    for name in names:
        text, before, whole = source(graph, folders, name)
        for module in IMPORT.findall(whole):
            if module not in project and module not in imports:
                imports.append(module)
        if re.search(r"^\s*variable\b", before, re.M):
            warnings.append(f"{decls[name]['module']} declares variables, which the copy of {name} leaves out")
        blocks.append(block(name, text, before, decls[name]["kind"] == "theorem"))
    header = (f"/-\nA challenge for `lake comparator`, written by lean-vouch from {graph.get('root') or 'the project'}.\n"
              "Its theorems are the targets, as statements; its definitions are everything they rest on.\n-/\n")
    lean = header + "".join(f"import {module}\n" for module in imports) + "\n" + "\n\n".join(grouped(blocks)) + "\n"
    out.mkdir(parents=True, exist_ok=True)
    (out / "Challenge.lean").write_text(lean, encoding="utf-8")
    solution = graph.get("root") or decls[targets[0]]["module"]
    config = {"challenge_module": "Challenge", "solution_module": solution, "theorem_names": list(targets),
              "permitted_axioms": STANDARD_AXIOMS}
    (out / "comparator.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return names, warnings


def check(graph, out, program, lake="lake"):
    """Compile the challenge and fingerprint it as the project was. Return the declarations that differ
    from the project's, or are missing from the challenge."""
    build = out / "build"
    build.mkdir(exist_ok=True)
    compiled = subprocess.run([lake, "env", "lean", "-R", str(out), "-o", str(build / "Challenge.olean"),
                               str(out / "Challenge.lean")], capture_output=True, text=True)
    if compiled.returncode:
        raise ChallengeError("the challenge doesn't compile:\n" + (compiled.stdout + compiled.stderr)[-4000:])
    fingerprints = out / "challenge-graph.json"
    # Lake sets the search path, so the challenge's folder goes in front of it inside. `program` runs
    # the graph program, as the build does.
    done = subprocess.run([lake, "env", "sh", "-c", 'LEAN_PATH="$0:$LEAN_PATH" exec "$@" Challenge',
                           str(build.resolve()), *program], capture_output=True, text=True,
                          env={**os.environ, "LEAN_VOUCH_GRAPH": str(fingerprints.resolve())})
    if done.returncode:
        said = "\n".join(line for line in (done.stdout + done.stderr).splitlines() if not line.startswith("lean-vouch-progress"))
        raise ChallengeError("fingerprinting the challenge failed:\n" + said[-4000:])
    theirs = json.loads(fingerprints.read_text(encoding="utf-8"))["declarations"]
    ours = graph["declarations"]
    names = json.loads((out / "comparator.json").read_text(encoding="utf-8"))["theorem_names"]
    expected = ordered(graph, closure(graph, names))
    return [name for name in expected if theirs.get(name, {}).get("own") != ours[name]["own"]]
