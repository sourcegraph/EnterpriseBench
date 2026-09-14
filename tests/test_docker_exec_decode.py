"""Decode-contract coverage for ``_docker_exec`` (EnterpriseBench-d0jih).

``_docker_exec`` captures arbitrary container bytes (compiler output, file
contents, agent text echoed into a verifier's ``detail``). Under ``text=True``'s
strict UTF-8 default a single non-UTF-8 byte raises ``UnicodeDecodeError`` at
decode time — before the verifier path can hand ``result.stdout`` to
``guard_verifier_output`` — so the fail-closed InfraError route is bypassed and
every checkpoint in the run is lost to an unhandled exception. The fix decodes
leniently (``errors="replace"``) so malformed bytes reach the caller as data.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)

import run_task
from run_task import _docker_exec


class TestDockerExecDecodeContract:
    def test_non_utf8_output_does_not_raise_and_reaches_caller(self) -> None:
        """A container that emits a raw 0x80 byte must not crash the strict
        decode; the byte reaches the caller (and thus the scorer guard) as a
        replacement char, not an unhandled UnicodeDecodeError."""
        real_run = subprocess.run

        def fake_run(_cmd, **kwargs):
            # Ignore the docker exec argv; run a local command that emits an
            # invalid-UTF-8 byte on stdout, forwarding the exact decode kwargs
            # _docker_exec chose. If those kwargs lacked errors="replace", the
            # real subprocess.run would raise UnicodeDecodeError here.
            return real_run(
                [
                    sys.executable,
                    "-c",
                    r"import sys; sys.stdout.buffer.write(b'bad\x80byte')",
                ],
                **kwargs,
            )

        with patch.object(run_task.subprocess, "run", side_effect=fake_run):
            result = _docker_exec("container", ["bash", "-c", "true"])

        assert result.stdout.startswith("bad")
        assert result.stdout.endswith("byte")
        # 0x80 decoded to the replacement char instead of aborting the run.
        assert "�" in result.stdout

    def test_passes_errors_replace_to_subprocess(self) -> None:
        """The decode leniency is wired at the call site, not left to a locale
        accident in the base image."""
        captured: dict = {}

        def fake_run(_cmd, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(_cmd, 0, stdout="", stderr="")

        with patch.object(run_task.subprocess, "run", side_effect=fake_run):
            _docker_exec("container", ["true"])

        assert captured.get("errors") == "replace"
        assert captured.get("text") is True
