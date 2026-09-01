"""The kernel stays small, pure, and time-injected. These are enforced, not hoped.

The line budget alone would happily permit one heavy module, so the import graph,
the ambient-time ban, and the file manifest are all asserted. If any of these
fail, the "small strict kernel" differentiator (the stated reason for not using
LangGraph) has quietly stopped being true.
"""

from __future__ import annotations

import ast
from pathlib import Path

KERNEL = Path(__file__).resolve().parents[2] / "src" / "viable_agents" / "kernel"

BANNED_MODULES = {
    "sqlalchemy",
    "langfuse",
    "opentelemetry",
    "anthropic",
    "yaml",
    "langchain",
    "langgraph",
}

ALLOWED_FILES = {
    "__init__.py",
    "address.py",
    "agent.py",
    "bus.py",
    "channels.py",
    "clock.py",
    "cost.py",
    "envelope.py",
    "errors.py",
    "llm.py",
    "payload.py",
    "topology.py",
    "tracing.py",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_kernel_imports_nothing_heavy() -> None:
    offenders: dict[str, set[str]] = {}
    for path in KERNEL.glob("*.py"):
        for module in _imported_modules(path):
            top = module.split(".")[0]
            if top in BANNED_MODULES:
                offenders.setdefault(path.name, set()).add(module)
    assert not offenders, f"kernel imports banned modules: {offenders}"


def test_kernel_imports_no_other_app_package() -> None:
    offenders: dict[str, set[str]] = {}
    for path in KERNEL.glob("*.py"):
        for module in _imported_modules(path):
            if module.startswith("viable_agents.") and not module.startswith(
                "viable_agents.kernel"
            ):
                offenders.setdefault(path.name, set()).add(module)
    assert not offenders, f"kernel reaches outside itself: {offenders}"


def test_time_is_never_read_ambiently_outside_the_clock() -> None:
    offenders: dict[str, list[str]] = {}
    for path in KERNEL.glob("*.py"):
        if path.name == "clock.py":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("datetime.now(", "datetime.utcnow(", "asyncio.sleep("):
            if needle in text:
                offenders.setdefault(path.name, []).append(needle)
    assert not offenders, f"ambient time in the kernel: {offenders}"


def test_kernel_file_manifest_is_exact() -> None:
    present = {p.name for p in KERNEL.glob("*.py")}
    assert present == ALLOWED_FILES, (
        f"kernel file set changed. added: {present - ALLOWED_FILES}, "
        f"removed: {ALLOWED_FILES - present}. Update the manifest deliberately."
    )
