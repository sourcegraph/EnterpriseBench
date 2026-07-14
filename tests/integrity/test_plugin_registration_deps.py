"""fact_triples must be registered only when its heavy deps are actually usable.

The scoring stack (numpy/scikit-learn) is imported lazily so that importers which
never score facts -- above all the chain runner, which reaches this package via
``eb_verify/__init__`` -> ``runner`` -> ``plugins/__init__`` -- do not pay ~0.8s
and ~100MB of TF-IDF machinery at startup.

That deferral silently defeated the old capability probe. The probe registered
FactTriplesValidator unless ``import fact_triples`` raised ImportError; once the
numpy import moved inside the functions that use it, the module imported cleanly
on a numpy-less sandbox, the validator registered, and the deferred ``import
numpy`` blew up later -- inside ``validate()``, at scoring time, where
``runner.py`` calls it with no try/except. Harness breakage destroyed the run
instead of yielding a clean, scoreable record.

These tests pin both halves of the invariant, which pull in opposite directions:
registration must consult the deps explicitly (not as an import side effect), and
the probe itself must not import them. Each runs in a fresh interpreter, because
import state is process-global and numpy is long since imported by the time the
suite reaches this module.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2] / "lib"

# Refuse to import the heavy stack, the way a minimal task sandbox does: lib's
# pyproject declares only jsonschema, so numpy-less is the canonical install.
BLOCK_HEAVY_DEPS = """
    import sys

    BLOCKED = ("numpy", "sklearn", "scipy")

    class _Blocker:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"No module named {name!r} (numpy-less sandbox)")
            return None

    sys.meta_path.insert(0, _Blocker())
    for _m in [m for m in sys.modules if m.split(".")[0] in BLOCKED]:
        del sys.modules[_m]
"""


def run_in_fresh_interpreter(
    body: str, *, block_deps: bool, extra_path: Path | None = None
) -> str:
    """Run ``body`` in a new interpreter, optionally with numpy/sklearn unimportable.

    ``extra_path`` is prepended to sys.path, which is how the broken-install tests
    shadow numpy with a module that raises on import.
    """
    prelude = textwrap.dedent(BLOCK_HEAVY_DEPS) if block_deps else ""
    if extra_path is not None:
        prelude += f"sys.path.insert(0, {str(extra_path)!r})\n"
    script = (
        f"import sys\nsys.path.insert(0, {str(LIB)!r})\n"
        + prelude
        + textwrap.dedent(body)
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, (
        f"probe interpreter died (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    return proc.stdout


def _fact_triples_workspace(tmp_path: Path) -> Path:
    """A schema-valid, groundedness-passing workspace that reaches the embedder.

    The candidate statement deliberately does not exact-match the ground truth, so
    scoring must fall through to TF-IDF similarity — the lazy-import site. A workspace
    that short-circuits earlier (no facts.json, bad schema) would never touch numpy and
    would make these tests pass for the wrong reason.
    """
    workspace = tmp_path / "ws"
    (workspace / "repo").mkdir(parents=True)
    (workspace / "ground_truth").mkdir()

    span = "score = 1.0 if passed else 0.0"
    (workspace / "repo" / "milestone.py").write_text(f"def run():\n    {span}\n")
    (workspace / "facts.json").write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "subject": "milestone.py",
                        "predicate": "fabricates",
                        "object": "scores",
                        "statement": (
                            "milestone.py fabricates a score from the process exit code"
                        ),
                        "evidence": {
                            "repo": "repo",
                            "file": "milestone.py",
                            "span": span,
                        },
                        "confidence": 90,
                    }
                ]
            }
        )
    )
    (workspace / "ground_truth" / "expected_facts.json").write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "subject": "milestone.py",
                        "predicate": "invents",
                        "object": "scores",
                        "statement": (
                            "The third runner invents a score from the process exit status"
                        ),
                    }
                ]
            }
        )
    )
    return workspace


class TestRegistrationRequiresUsableDeps:
    """Without numpy, the validator must not register -- and must say so."""

    def test_validator_not_registered_when_numpy_absent(self) -> None:
        out = run_in_fresh_interpreter(
            """
            from eb_verify.plugins import get_validator
            print("REGISTERED" if get_validator("fact_triples") else "ABSENT")
            """,
            block_deps=True,
        )
        assert "ABSENT" in out, (
            "fact_triples registered on a numpy-less sandbox. The lazy import means "
            "a clean `import fact_triples` no longer proves numpy is usable; "
            "registration must probe the deps explicitly."
        )

    def test_absent_validator_is_announced_not_silent(self) -> None:
        out = run_in_fresh_interpreter(
            """
            import warnings
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                import eb_verify.plugins  # noqa: F401
            print("WARNED" if any(
                issubclass(w.category, RuntimeWarning) for w in caught
            ) else "SILENT")
            """,
            block_deps=True,
        )
        assert "WARNED" in out, "dropping a validator must not be silent"

    def test_validator_registered_when_deps_present(self) -> None:
        """The guard must not over-fire: a normal install still scores facts."""
        pytest.importorskip("numpy")
        pytest.importorskip("sklearn")
        out = run_in_fresh_interpreter(
            """
            from eb_verify.plugins import get_validator
            print("REGISTERED" if get_validator("fact_triples") else "ABSENT")
            """,
            block_deps=False,
        )
        assert "REGISTERED" in out, "deps are installed; the validator must register"


class TestNoImportErrorReachesScoring:
    """The failure this bead exists to eliminate: harness breakage killing a run."""

    def test_missing_numpy_never_escapes_as_importerror_at_scoring(
        self, tmp_path: Path
    ) -> None:
        """A numpy-less sandbox must yield a clean record, not a dead process.

        ``runner.py`` calls ``validator.validate(workspace)`` unguarded, so an
        ImportError escaping ``validate()`` takes down the whole verification run.
        Score the artifact through whatever the registry hands back: either the
        validator is absent (the runner reports the unknown type and scores on),
        or it is present and must not raise ImportError.
        """
        workspace = _fact_triples_workspace(tmp_path)
        out = run_in_fresh_interpreter(
            f"""
            import pathlib
            from eb_verify.plugins import get_validator

            validator = get_validator("fact_triples")
            if validator is None:
                print("NO_VALIDATOR")  # runner reports the unknown type; run survives
            else:
                try:
                    validator.validate(pathlib.Path({str(workspace)!r}))
                    print("SCORED")
                except ImportError as exc:
                    print(f"IMPORTERROR_ESCAPED {{exc}}")
            """,
            block_deps=True,
        )
        assert "IMPORTERROR_ESCAPED" not in out, (
            "a missing dependency escaped validate() as an ImportError; "
            "runner.py calls validate() unguarded, so this kills the entire run"
        )
        assert "NO_VALIDATOR" in out


class TestBrokenInstallCannotKillTheRun:
    """A dependency can be findable and still unusable, and the probe cannot tell.

    ``find_spec`` resolves a module without executing it — that is precisely why the
    probe is cheap enough to sit on the chain-runner import path. The cost is that a
    *present but broken* numpy (ABI mismatch, half-installed, corrupt .so) satisfies
    the probe and only fails when something finally imports it, inside validate().

    The old module-scope probe caught that case for free, because it really did run
    the import. So the probe alone is a narrower version of the same regression, and
    the runner must hold the line: a validator that raises ImportError yields a
    record, not a dead process.
    """

    def _shadow_broken_numpy(self, tmp_path: Path, exc: str = "ImportError") -> Path:
        """A numpy that find_spec resolves happily and that explodes on execution."""
        shadow = tmp_path / f"shadow_{exc}"
        shadow.mkdir()
        (shadow / "numpy.py").write_text(
            f'raise {exc}("numpy: libopenblas.so: cannot open shared object file '
            '(simulated corrupt install)")\n'
        )
        return shadow

    def test_broken_numpy_is_findable_but_unimportable(self, tmp_path: Path) -> None:
        """Guard the premise: a vacuous shadow would make the next test meaningless."""
        out = run_in_fresh_interpreter(
            """
            import importlib.util
            print("FOUND" if importlib.util.find_spec("numpy") else "NOTFOUND")
            try:
                import numpy  # noqa: F401
                print("IMPORTED")
            except ImportError:
                print("IMPORT_RAISES")
            """,
            block_deps=False,
            extra_path=self._shadow_broken_numpy(tmp_path),
        )
        assert "FOUND" in out and "IMPORT_RAISES" in out, (
            f"the broken-numpy shadow does not exercise the gap it exists to test: {out}"
        )

    def test_broken_numpy_yields_a_record_not_a_dead_process(
        self, tmp_path: Path
    ) -> None:
        """validate() must absorb its own dependency fault, for every caller.

        runner.validate_artifacts() and cli both call validate() unguarded, so the
        guard belongs in the validator rather than at each call site.
        """
        workspace = _fact_triples_workspace(tmp_path)
        out = run_in_fresh_interpreter(
            f"""
            import pathlib
            from eb_verify.plugins import get_validator

            validator = get_validator("fact_triples")
            print(f"REGISTERED={{validator is not None}}")
            if validator is not None:
                try:
                    result = validator.validate(pathlib.Path({str(workspace)!r}))
                    print(f"RECORDED valid={{result.valid}} :: {{result.detail}}")
                except ImportError as exc:
                    print(f"IMPORTERROR_ESCAPED {{exc}}")
            """,
            block_deps=False,
            extra_path=self._shadow_broken_numpy(tmp_path),
        )
        assert "IMPORTERROR_ESCAPED" not in out, (
            "a broken numpy escaped validate() as an ImportError. runner.py and cli.py "
            "call validate() unguarded, so this aborts the entire verification run over "
            f"a dependency fault that is not the agent's doing. Got: {out.strip()}"
        )
        assert "RECORDED" in out, out

    # A broken dependency is under no obligation to raise ImportError: an ABI mismatch
    # raises RuntimeError, a truncated .so raises OSError, a bad C extension raises
    # SystemError. Guarding only ImportError leaves the run just as dead.
    @pytest.mark.parametrize(
        "exc", ["ImportError", "OSError", "SystemError", "RuntimeError", "ValueError"]
    )
    def test_any_import_failure_yields_a_record_not_a_dead_process(
        self, tmp_path: Path, exc: str
    ) -> None:
        workspace = _fact_triples_workspace(tmp_path)
        out = run_in_fresh_interpreter(
            f"""
            import pathlib
            from eb_verify.plugins import get_validator

            validator = get_validator("fact_triples")
            if validator is None:
                print("NO_VALIDATOR")
            else:
                try:
                    result = validator.validate(pathlib.Path({str(workspace)!r}))
                    print(f"RECORDED valid={{result.valid}} :: {{result.detail}}")
                except BaseException as exc:  # noqa: BLE001 — anything escaping is the bug
                    print(f"ESCAPED {{type(exc).__name__}}: {{exc}}")
            """,
            block_deps=False,
            extra_path=self._shadow_broken_numpy(tmp_path, exc),
        )
        assert "ESCAPED" not in out, (
            f"a numpy raising {exc} on import escaped validate() and killed the run. "
            "Only ImportError is guarded, but module-level code can raise anything; "
            f"the dependency load must be guarded on failure, not on type. Got: {out.strip()}"
        )
        assert "RECORDED" in out, out


    def test_process_control_still_propagates(self, tmp_path: Path) -> None:
        """The guard stops at Exception on purpose.

        A KeyboardInterrupt or SystemExit arriving mid-import is process control, not a
        broken dependency. Swallowing it into "dependency unavailable" would make the
        verifier unkillable and mask a deliberate exit, so it must keep propagating.
        """
        out = run_in_fresh_interpreter(
            f"""
            import pathlib
            from eb_verify.plugins import get_validator

            validator = get_validator("fact_triples")
            try:
                validator.validate(pathlib.Path({str(_fact_triples_workspace(tmp_path))!r}))
                print("SWALLOWED")
            except SystemExit:
                print("PROPAGATED")
            """,
            block_deps=False,
            extra_path=self._shadow_broken_numpy(tmp_path, "SystemExit"),
        )
        assert "PROPAGATED" in out, (
            f"SystemExit was swallowed by the dependency guard: {out.strip()}"
        )


class TestProbeStaysCheap:
    """The probe must not undo the deferral it exists to protect."""

    def test_chain_runner_import_path_does_not_pull_numpy(self) -> None:
        """Importing the scorer guard must not drag in the TF-IDF stack.

        ``milestone.py`` imports ``eb_verify.scorer_guard`` on every chain run, and
        that import reaches ``plugins/__init__``. Restoring the capability probe by
        actually importing numpy would re-impose the ~0.8s/~100MB cost on every
        chain run -- so this pins the cheap-probe property, and equally forbids
        "fixing" the registration bug by reverting the lazy import.
        """
        out = run_in_fresh_interpreter(
            """
            import sys
            import eb_verify.scorer_guard  # noqa: F401
            heavy = sorted({
                m.split(".")[0] for m in sys.modules
                if m.split(".")[0] in ("numpy", "sklearn", "scipy")
            })
            print("HEAVY:" + (",".join(heavy) if heavy else "none"))
            """,
            block_deps=False,
        )
        assert "HEAVY:none" in out, (
            f"the chain-runner import path pulled the heavy scoring stack ({out.strip()}); "
            "the numpy/sklearn imports must stay deferred and the probe must not execute them"
        )
