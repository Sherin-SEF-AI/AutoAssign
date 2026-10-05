"""Enforces the backend layering: lower layers never import higher ones, and synthetic data
is only reachable from the synthetic adapter, the adapter factory and the day simulator."""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

# Rank by package. Base modules (config, core, db, adapters.base) are rank 0.
RANKS = {
    "app.config": 0,
    "app.core": 0,
    "app.db": 0,
    "app.adapters.base": 0,
    "app.adapters": 1,
    "app.eta": 2,
    "app.graph": 3,
    "app.solver": 4,
    "app.plan": 5,
    "app.jobs": 6,
    "app.api": 6,
    "app.replay": 6,
}
TOP_LEVEL = {"app.main", "app.worker", "app.sim", "app.runtime", "app.cli", "app.simulator"}
SYNTHETIC_ALLOWED = ("app.adapters.synthetic", "app.adapters.factory", "app.sim", "app.simulator")


def _module_name(path: Path) -> str:
    rel = path.relative_to(APP.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _rank(module: str) -> int | None:
    best: tuple[int, int] | None = None
    for prefix, rank in RANKS.items():
        if module == prefix or module.startswith(prefix + "."):
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), rank)
    return best[1] if best else None


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module)
    return [m for m in out if m.startswith("app")]


def test_layers_only_import_downward() -> None:
    violations = []
    for path in APP.rglob("*.py"):
        module = _module_name(path)
        if any(module == t or module.startswith(t + ".") for t in TOP_LEVEL):
            continue
        own = _rank(module)
        if own is None:
            continue
        for imp in _imports(path):
            if any(imp == t or imp.startswith(t + ".") for t in TOP_LEVEL):
                violations.append(f"{module} imports top level {imp}")
                continue
            other = _rank(imp)
            if other is None:
                continue
            same_family = imp.split(".")[:2] == module.split(".")[:2]
            if other > own and not same_family:
                violations.append(f"{module} (rank {own}) imports {imp} (rank {other})")
    assert not violations, "\n".join(violations)


def test_synthetic_is_contained() -> None:
    leaks = []
    for path in APP.rglob("*.py"):
        module = _module_name(path)
        if module.startswith(SYNTHETIC_ALLOWED):
            continue
        for imp in _imports(path):
            if imp.startswith("app.adapters.synthetic"):
                leaks.append(f"{module} imports {imp}")
        if "synthetic" in path.read_text().lower() and module.startswith(
            ("app.eta", "app.graph", "app.solver")
        ):
            leaks.append(f"{module} mentions synthetic data")
    assert not leaks, "\n".join(leaks)
