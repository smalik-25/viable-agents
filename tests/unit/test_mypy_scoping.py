"""CLAUDE.md hard rule 2: --strict on the kernel, standard mypy everywhere else.

This is the only check in the repo whose failure mode is invisible in normal CI
output, in both directions. Writing `strict = true` inside a per-module override
does not scope it: mypy's config parser sees the key, fires the global
set_strict_flags() callback and continues, so the flag never lands in the
per-module dict and the whole repo is strict-checked. CI is green either way.
The likely reaction to a repo-wide strict failure is to delete the override,
which silently loses strict on the kernel: the exact opposite of the intent, and
also green.

So this test asserts both halves. It reads the real pyproject.toml (so the
assertion tracks the config that actually ships), then runs mypy against a
synthetic two-package tree using those same flags and proves exactly one error
lands, in the kernel package.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

# The per-module-legal members of mypy's strict set as of 2.3.0. Deliberately
# enumerated: `strict = true` cannot be used per-module. `warn_redundant_casts`
# is NOT in this list; it is not per-module-legal and is already default-on.
STRICT_FLAGS = frozenset(
    {
        "disallow_any_generics",
        "disallow_subclassing_any",
        "disallow_untyped_calls",
        "disallow_untyped_defs",
        "disallow_incomplete_defs",
        "check_untyped_defs",
        "disallow_untyped_decorators",
        "warn_unused_ignores",
        "warn_return_any",
        "implicit_reexport",
        "strict_equality",
        "extra_checks",
    }
)

UNTYPED = "def f():\n    return 1\n"


@pytest.fixture(scope="session")
def mypy_config(repo_root: Path) -> dict:
    with (repo_root / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["tool"]["mypy"]


@pytest.fixture(scope="session")
def kernel_override(mypy_config: dict) -> dict:
    overrides = [
        o
        for o in mypy_config.get("overrides", [])
        if any("kernel" in m for m in _as_list(o.get("module")))
    ]
    assert len(overrides) == 1, "expected exactly one mypy override targeting the kernel"
    return overrides[0]


def _as_list(value) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def test_strict_is_never_set_as_a_key(mypy_config: dict, kernel_override: dict):
    # The trap. If this ever appears, strict silently applies repo-wide.
    assert "strict" not in mypy_config
    assert "strict" not in kernel_override


def test_kernel_override_enumerates_every_strict_flag(kernel_override: dict):
    missing = {f for f in STRICT_FLAGS if f not in kernel_override}
    assert not missing, f"kernel is not fully strict; missing flags: {sorted(missing)}"
    # implicit_reexport is the one strict flag that is disabled rather than
    # enabled, so check the intended value rather than assuming truthiness.
    assert kernel_override["implicit_reexport"] is False
    for flag in STRICT_FLAGS - {"implicit_reexport"}:
        assert kernel_override[flag] is True, f"{flag} is present but not enabled"


def test_kernel_override_targets_the_kernel_and_nothing_else(kernel_override: dict):
    assert set(_as_list(kernel_override["module"])) == {
        "viable_agents.kernel",
        "viable_agents.kernel.*",
    }


def test_strict_actually_scopes_to_the_kernel_when_mypy_runs(tmp_path: Path, kernel_override: dict):
    """Run mypy for real. One untyped def inside the kernel errors; one outside does not."""
    src = tmp_path / "src" / "probe"
    for package in ("kernel", "systems"):
        (src / package).mkdir(parents=True)
        (src / package / "__init__.py").write_text("")
        (src / package / "m.py").write_text(UNTYPED)
    (src / "__init__.py").write_text("")

    flags = "\n".join(
        f"{flag} = {str(kernel_override[flag]).lower()}" for flag in sorted(STRICT_FLAGS)
    )
    (tmp_path / "mypy.ini").write_text(
        f"[mypy]\npython_version = 3.12\nmypy_path = src\n\n[mypy-probe.kernel.*]\n{flags}\n"
    )

    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(tmp_path / "mypy.ini"),
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            "--no-error-summary",
            str(src),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,  # a non-zero exit is the expected outcome: we want one error
    )
    out = result.stdout

    assert out.count("no-untyped-def") == 1, (
        "strict either leaked outside the kernel or was not applied to it.\n"
        f"mypy said:\n{out}\n{result.stderr}"
    )
    assert "kernel" in out, f"the one error is not in the kernel:\n{out}"
    assert "systems" not in out, f"strict leaked into a non-kernel package:\n{out}"
