"""PF truth-separation enforcement: import-linter contract config, lint-imports clean, signature-level rejection of truth types, AST guard against make_onboard_map import.

Tests in this file were extracted from the original 2305-LOC
``test_pf_float.py`` as part of the post-implementation simplify pass.
Shared fixtures live in ``tests/maritime/_pf_float_helpers.py``.
"""

from __future__ import annotations

import ast
import subprocess
import tomllib
import typing
from pathlib import Path

import pytest

from rtl.vectors.maritime.pf_float import PFFloat


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_pyproject_has_pf_truth_separation_contract():
    """Task 26.1 / Spec scenario "import-linter contract forbids
    scenario_truth_schema in pf_float.py".

    The PF-library truth-separation contract lives in
    ``pyproject.toml`` under ``[tool.importlinter]`` → ``contracts``.
    The contract MUST:

    - Be named ``"PF library does not access truth"`` exactly so that a
      grep for that string locates it unambiguously.
    - Be of type ``forbidden``.
    - List ``rtl.vectors.maritime.pf_float`` as the SOLE entry in
      ``source_modules`` (a list of length 1, not a superset). If
      ``run_pf_float.py`` were added to ``source_modules``, the CLI
      could no longer import ``ScenarioTruthReader`` for the
      ``pf_summary.json`` RMSE computation — see design D12.
    - Forbid both ``rtl.vectors.maritime.scenario_truth_schema`` and
      ``rtl.vectors.maritime.current_fields``.

    This test fails until the implementer (Batch E) adds the contract
    block to ``pyproject.toml`` — the file currently has
    ``contracts = []``.
    """
    pyproject_path = _PROJECT_ROOT / "pyproject.toml"
    with pyproject_path.open("rb") as fh:
        config = tomllib.load(fh)

    importlinter = config.get("tool", {}).get("importlinter", {})
    contracts = importlinter.get("contracts", [])

    target_name = "PF library does not access truth"
    matching = [c for c in contracts if c.get("name") == target_name]
    assert len(matching) == 1, (
        f"expected exactly one [tool.importlinter] contract named "
        f"{target_name!r} in pyproject.toml; found {len(matching)}. "
        f"All contract names: {[c.get('name') for c in contracts]}"
    )
    contract = matching[0]

    assert contract["type"] == "forbidden", (
        f"contract {target_name!r} must be of type 'forbidden'; "
        f"got type={contract['type']!r}"
    )

    assert contract["source_modules"] == ["rtl.vectors.maritime.pf_float"], (
        f"contract {target_name!r} must list "
        f"['rtl.vectors.maritime.pf_float'] as its SOLE source_modules "
        f"entry (the PF library is the only module the contract scopes "
        f"truth out of; run_pf_float is intentionally exempt per "
        f"design D12). Got: {contract['source_modules']!r}"
    )

    assert "rtl.vectors.maritime.scenario_truth_schema" in contract["forbidden_modules"], (
        f"contract {target_name!r} must list "
        f"'rtl.vectors.maritime.scenario_truth_schema' in "
        f"forbidden_modules (the truth-bearing schema split out by "
        f"maritime-scenario-gen). Got forbidden_modules="
        f"{contract['forbidden_modules']!r}"
    )

    assert "rtl.vectors.maritime.current_fields" in contract["forbidden_modules"], (
        f"contract {target_name!r} must list "
        f"'rtl.vectors.maritime.current_fields' in forbidden_modules "
        f"(the truth current-field module). Got forbidden_modules="
        f"{contract['forbidden_modules']!r}"
    )

    assert "rtl.vectors.maritime.run_pf_float" not in contract["source_modules"], (
        f"contract {target_name!r} must NOT include "
        f"'rtl.vectors.maritime.run_pf_float' in source_modules — the "
        f"reporting CLI is intentionally exempt (it reads truth via "
        f"ScenarioTruthReader to compute per-class RMSE in "
        f"pf_summary.json — see design D12). Got source_modules="
        f"{contract['source_modules']!r}"
    )


def test_lint_imports_exits_zero():
    """Task 26.2 / Spec scenario "Introducing a forbidden import into
    the library triggers contract failure" (positive direction).

    With the contract from task 26.1 in place AND ``pf_float.py``
    actually clean of forbidden imports (which is the state delivered
    by Batches B/C), ``uv run lint-imports`` MUST exit zero. A nonzero
    exit means either:

    - the contract config is malformed (import-linter rejects it); or
    - ``pf_float.py`` (or some other contract's source module) imports
      a forbidden module; or
    - some new import-linter contract has been added that the project
      now violates.

    All three are real bugs the developer needs to see; the assertion
    message surfaces stderr so the failing contract is named in the
    pytest output.

    This test invokes the real ``uv run lint-imports`` subprocess and
    is therefore slower than a typical unit test. The project does not
    declare a ``slow`` pytest marker (per ``pyproject.toml``
    ``[tool.pytest.ini_options]``), so this test runs as part of the
    default suite.
    """
    result = subprocess.run(
        ["uv", "run", "lint-imports"],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"`uv run lint-imports` exited {result.returncode}; expected 0. "
        f"stdout:\n{result.stdout.decode(errors='replace')}\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )


def test_pf_float_step_signature_rejects_truth_types():
    """Task 26.3 / Spec scenario "PFFloat function signatures reject
    truth types at type-check time".

    The import-linter contract (task 26.1) blocks module-level imports
    of truth-bearing modules from ``pf_float.py``. The complementary
    defense is type signatures: even if some other module (allowed to
    read truth, e.g. ``run_pf_float.py``) tried to pass a truth-typed
    object into a ``PFFloat`` method, pyright strict would reject it
    because no method's annotations mention ``ScenarioTruthReader``,
    ``TruthTickView``, or ``CurrentField``.

    This test inspects ``PFFloat.step``, ``.predict``, and ``.weight``
    via ``typing.get_type_hints`` (resolves forward references against
    the ``pf_float`` module's namespace). For each annotation —
    parameters and return — we stringify with ``repr`` and substring-
    search for the three forbidden type names. ``repr(cls)`` yields
    the qualified name including module, so a leak via a generic
    container (``Iterable[ScenarioTruthReader]``) would still be
    caught — the substring appears in the repr of the parameterized
    type.

    The PFFloat methods don't use exotic generics; substring-on-repr
    is robust here. If the signatures grow more complex (e.g., heavily
    nested ``Callable``s), a recursive ``typing.get_args`` /
    ``typing.get_origin`` walk would be the stricter check — but the
    substring approach catches every case we care about today.

    This test ALREADY PASSES against the Batch B/C implementation —
    no method in ``pf_float.py`` mentions a truth type. It is a
    regression guard against future changes that try to smuggle truth
    into the PF library through a typed parameter.
    """
    forbidden_names = ("ScenarioTruthReader", "TruthTickView", "CurrentField")

    methods_to_check = (
        ("step", PFFloat.step),
        ("predict", PFFloat.predict),
        ("weight", PFFloat.weight),
    )

    for method_name, method in methods_to_check:
        hints = typing.get_type_hints(method)
        for hint_name, annotation in hints.items():
            annotation_repr = repr(annotation)
            for forbidden in forbidden_names:
                assert forbidden not in annotation_repr, (
                    f"PFFloat.{method_name} parameter/return "
                    f"{hint_name!r} has annotation {annotation_repr!r} "
                    f"which mentions forbidden truth type "
                    f"{forbidden!r}. Truth-typed parameters in the PF "
                    f"library defeat the truth-separation invariant — "
                    f"the import-linter contract (task 26.1) catches "
                    f"module-level imports, but a typed parameter "
                    f"would let truth flow in from run_pf_float.py "
                    f"(which is allowed to read truth for "
                    f"pf_summary.json). See design D12 / spec "
                    f"\"Truth Separation via Module Boundaries and "
                    f"Import Linting\"."
                )


def test_pf_float_does_not_import_make_onboard_map():
    """Task 27.2 / Spec scenario "PF does not import make_onboard_map".

    The PF library uses only ``RegionalMap`` instances received from
    outside (loaded by the CLI from ``ScenarioReader.onboard_map()``,
    which materializes the sidecar onboard map produced by
    ``maritime-scenario-gen``). The PF MUST NEVER build its own
    onboard map from a truth map via ``make_onboard_map`` — doing so
    would require access to the truth map, defeating the
    onboard-map-reconstruction invariant that ``maritime-scenario-gen``
    establishes by emitting the onboard map as a sidecar.

    This AST walk catches both direct and aliased references:

    - ``ast.ImportFrom``: ``from rtl.vectors.maritime.map_payload
      import make_onboard_map`` — the obvious form.
    - ``ast.Attribute``: ``map_payload.make_onboard_map(...)`` after
      ``from rtl.vectors.maritime import map_payload`` — the
      attribute-access form a future developer might reach for to
      sidestep a direct import.

    ``import-linter`` would also catch the ``ImportFrom`` case at the
    module-graph level, but the attribute walk extends coverage to
    code paths the linter doesn't see (e.g., a deferred import
    followed by attribute access).

    This test ALREADY PASSES against the Batch B/C implementation —
    ``pf_float.py`` does not reference ``make_onboard_map`` in any
    form. It is a regression guard against a future change that
    smuggles truth-map reconstruction into the PF library. See design
    D12 / spec "Onboard Map From Scenario Reader."
    """
    pf_float_path = _PROJECT_ROOT / "rtl" / "vectors" / "maritime" / "pf_float.py"
    source = pf_float_path.read_text()
    tree = ast.parse(source)

    forbidden_symbol = "make_onboard_map"

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names = [alias.name for alias in node.names]
            assert forbidden_symbol not in imported_names, (
                f"pf_float.py imports forbidden symbol "
                f"{forbidden_symbol!r} via 'from {node.module} "
                f"import ...' at line {node.lineno}. The PF library "
                f"must use only RegionalMap instances received from "
                f"the caller (loaded via ScenarioReader.onboard_map()) "
                f"— it must not build its own onboard map from a "
                f"truth map. See design D12 / spec \"Onboard Map "
                f"From Scenario Reader.\""
            )

        elif isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "map_payload"
                and node.attr == forbidden_symbol
            ):
                raise AssertionError(
                    f"pf_float.py references "
                    f"'map_payload.{forbidden_symbol}' at line "
                    f"{node.lineno}. Even via attribute access on an "
                    f"aliased module import, the PF library must not "
                    f"reach for make_onboard_map — onboard maps come "
                    f"from ScenarioReader.onboard_map(), not from "
                    f"truth-map reconstruction. See design D12 / "
                    f"spec \"Onboard Map From Scenario Reader.\""
                )
