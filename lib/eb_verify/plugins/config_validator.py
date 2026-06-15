"""
config validator — YAML/JSON/TOML syntax validation.
"""

from __future__ import annotations

import json
from pathlib import Path

from eb_verify.plugins import ValidationResult, safe_read


class ConfigValidator:
    artifact_type = "config"

    # Config file extensions to validate.
    CONFIG_SUFFIXES = (".yaml", ".yml", ".json", ".toml", ".hcl")
    _SKIP_DIRS = ("vendor", "node_modules", ".git")

    def validate(self, workspace: Path) -> ValidationResult:
        """
        Find config files that appear to be agent-produced artifacts and validate syntax.
        Looks for files in standard output locations.
        """
        # Check common output paths. The workspace root is scanned top-level
        # only (to avoid walking entire cloned repos); the dedicated output/
        # and artifacts/ dirs are small enough to scan recursively.
        output_dirs = [
            workspace / "output",
            workspace / "artifacts",
            workspace,
        ]

        errors: list[str] = []
        indeterminate: list[str] = []
        validated = 0

        for out_dir in output_dirs:
            if not out_dir.is_dir():
                continue
            top_level_only = out_dir == workspace
            for config_file in self._iter_config_files(out_dir, top_level_only):
                if any(p in self._SKIP_DIRS for p in config_file.parts):
                    continue
                result = self._validate_file(config_file, workspace)
                if result.indeterminate:
                    indeterminate.append(f"{config_file.name}: {result.detail}")
                elif not result.valid:
                    errors.append(f"{config_file.name}: {result.detail}")
                else:
                    validated += 1

        if errors:
            return ValidationResult(
                valid=False,
                detail=f"Config validation errors: {'; '.join(errors)}",
            )
        if validated == 0 and not indeterminate:
            return ValidationResult(
                valid=True,
                detail="No config artifacts found to validate (not required to exist)",
            )

        parts = []
        if validated:
            parts.append(f"Validated {validated} config files")
        if indeterminate:
            # Could not check these (parser unavailable). Surface as a warning
            # instead of silently passing them off as valid.
            parts.append(
                f"INDETERMINATE — syntax unchecked: {'; '.join(indeterminate)}"
            )
        return ValidationResult(
            valid=True,
            detail="; ".join(parts),
            indeterminate=bool(indeterminate),
        )

    def _iter_config_files(self, out_dir: Path, top_level_only: bool):
        """Yield config files of every supported extension under *out_dir*.

        At the workspace root *top_level_only* keeps the scan shallow; in the
        output/artifacts dirs it recurses.
        """
        for suffix in self.CONFIG_SUFFIXES:
            pattern = f"*{suffix}" if top_level_only else f"**/*{suffix}"
            yield from out_dir.glob(pattern)

    def _validate_file(self, path: Path, workspace: Path) -> ValidationResult:
        suffix = path.suffix.lower()
        try:
            content = safe_read(path, workspace)
            if suffix == ".json":
                json.loads(content)
            elif suffix in (".yaml", ".yml"):
                try:
                    import yaml
                except ImportError:
                    return ValidationResult(
                        valid=True,
                        detail="PyYAML not installed",
                        indeterminate=True,
                    )
                yaml.safe_load(content)
            elif suffix == ".toml":
                try:
                    import tomllib
                except ImportError:
                    try:
                        import tomli as tomllib  # type: ignore
                    except ImportError:
                        return ValidationResult(
                            valid=True,
                            detail="no TOML parser (tomllib/tomli) available",
                            indeterminate=True,
                        )
                tomllib.loads(content)
            # .hcl has no stdlib parser; existence is all we verify.
            return ValidationResult(valid=True)
        except Exception as e:
            return ValidationResult(valid=False, detail=str(e))
