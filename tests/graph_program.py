"""lean-vouch's graph program, compiled once for the example's Lean, as `lean-vouch` compiles it for a
project. `lean --run` compiles it again on every run, which costs about 2 seconds a graph."""

import atexit
import functools
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "lean_vouch" / "graph" / "DeclGraph.lean"
sys.path.insert(0, str(ROOT))
from lean_vouch.cli import GRAPH_LAKEFILE  # noqa: E402


@functools.cache
def graph_program():
    """The command that runs it, inside `lake env` in a project on the example's Lean."""
    folder = Path(tempfile.mkdtemp(prefix="lean-vouch-graph-"))
    atexit.register(shutil.rmtree, folder, True)
    shutil.copy(SOURCE, folder / "LeanVouchGraph.lean")
    shutil.copy(ROOT / "example" / "lean-toolchain", folder / "lean-toolchain")
    (folder / "lakefile.toml").write_text(GRAPH_LAKEFILE)
    built = subprocess.run(["lake", "build", "lean-vouch-graph"], cwd=folder, capture_output=True, text=True)
    executable = folder / ".lake" / "build" / "bin" / "lean-vouch-graph"
    if built.returncode != 0 or not executable.exists():
        raise RuntimeError(f"the graph program didn't compile:\n{built.stdout}{built.stderr}")
    return [str(executable)]
