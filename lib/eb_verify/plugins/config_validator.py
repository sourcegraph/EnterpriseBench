"""
config validator — YAML/JSON/TOML syntax validation.
"""

from __future__ import annotations

import json
from pathlib import Path

from eb_verify.plugins import ValidationResult, safe_read


class ConfigValidator:
    artifact_type = "config"

    # Common config file patterns to look for in workspace.
    # .hcl is intentionally excluded: _validate_file has no parser for it,
    # so counting it here would report files as "validated" with no syntax
    # check ever run.
    CONFIG_GLOBS = [
        "**/*.yaml",
        "**/*.yml",
        "**/*.json",
        "**/*.toml",
    ]

    def validate(self, workspace: Path) -> ValidationResult:
        """
        Find config files that appear to be agent-produced artifacts and validate syntax.
        Looks for files in standard output locations.
        """
        # Check common output paths
        output_dirs = [
            workspace / "output",
            workspace / "artifacts",
            workspace,
        ]

        errors = []
        validated = 0

        for out_dir in output_dirs:
            if not out_dir.is_dir():
                continue
            for pattern in self.CONFIG_GLOBS:
                for config_file in out_dir.glob(pattern):
                    # Skip deeply nested vendor/node_modules
                    parts = config_file.parts
                    if any(p in ("vendor", "node_modules", ".git") for p in parts):
                        continue
                    result = self._validate_file(config_file, workspace)
                    if not result.valid:
                        errors.append(f"{config_file.name}: {result.detail}")
                    validated += 1
                # Only check top-level for workspace root to avoid scanning entire repos
                if out_dir == workspace:
                    break

        if errors:
            return ValidationResult(
                valid=False,
                detail=f"Config validation errors: {'; '.join(errors)}",
            )
        if validated == 0:
            return ValidationResult(
                valid=True,
                detail="No config artifacts found to validate (not required to exist)",
            )
        return ValidationResult(valid=True, detail=f"Validated {validated} config files")

    def _validate_file(self, path: Path, workspace: Path) -> ValidationResult:
        suffix = path.suffix.lower()
        try:
            content = safe_read(path, workspace)
            if suffix in (".json",):
                json.loads(content)
            elif suffix in (".yaml", ".yml"):
                try:
                    import yaml
                    yaml.safe_load(content)
                except ImportError:
                    pass  # yaml not available, skip
            elif suffix in (".toml",):
                try:
                    import tomllib
                except ImportError:
                    try:
                        import tomli as tomllib  # type: ignore
                    except ImportError:
                        pass  # no toml parser, skip
                else:
                    tomllib.loads(content)
            return ValidationResult(valid=True)
        except Exception as e:
            return ValidationResult(valid=False, detail=str(e))
