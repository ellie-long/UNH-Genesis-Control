"""Nothing in the live Genesis tree may depend on the simulator.

cooldown_sim is purely additive: it reads live modules and profiles, but no
live module imports it, so removing or breaking it can never affect online
operation.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXEMPT_TOP = {"cooldown_sim", "tests", ".git", "logs", "archive"}


def _python_files():
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in EXEMPT_TOP or "__pycache__" in rel.parts:
            continue
        yield path


def test_no_live_module_imports_cooldown_sim():
    offenders = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n == "cooldown_sim" or n.startswith("cooldown_sim.") for n in names):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, offenders


def test_no_live_config_or_script_references_cooldown_sim():
    offenders = []
    for folder, pattern in (("configs", "*.yaml"), ("scripts", "*.sh"), ("gui", "*.bob")):
        for path in (ROOT / folder).rglob(pattern):
            if "cooldown_sim" in path.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, offenders
