#!/usr/bin/env python3
"""
Trim Flatpak manifest permissions using YAML config.

Usage:
  python3 flatpak_trim.py --manifest com.example.App.yaml --config config.yaml
  python3 flatpak_trim.py --git-repo <repo-url> --manifest path/in/repo.yaml --config config.yaml
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: PyYAML. Install with: pip install pyyaml"
    ) from exc


@dataclass(frozen=True)
class ChangeRecord:
    category: str
    old_arg: str
    new_arg: str | None


@dataclass(frozen=True)
class PermissionArg:
    category: str
    value: str


@dataclass(frozen=True)
class CategoryRules:
    """
    Normalized trim rules for a single finish-arg category.

    `remove` contains values that should be deleted completely.
    `replace` maps values to their replacement, or `None` to remove.
    """

    remove: set[str]
    replace: dict[str, str | None]


@dataclass(frozen=True)
class TrimResult:
    finish_args: list[str]
    changes: list[ChangeRecord]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove or replace Flatpak finish-args permissions based on YAML rules."
    )
    parser.add_argument(
        "--git-repo",
        dest="git_repo",
        help="Enable git mode by cloning this repo into the current directory (accepts SSH and HTTP URIs).",
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to Flatpak manifest file (.yaml/.yml/.json). In --git-repo mode, this is relative to the checked-out repo root.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to YAML config file with permission trim rules.",
    )
    return parser.parse_args()

def run(manifest_path: Path, config_path: Path) -> int:
    config = load_yaml(config_path)
    rules_by_category = validate_rules(config)

    manifest, manifest_fmt = load_manifest(manifest_path)
    finish_args = ensure_manifest_shape(manifest, manifest_path)
    finish_args_before = copy.deepcopy(finish_args)

    result = apply_rules_to_finish_args(
        finish_args=finish_args_before,
        rules_by_category=rules_by_category,
    )

    backup_manifest(manifest_path)
    manifest["finish-args"] = result.finish_args
    save_manifest(manifest_path, manifest, manifest_fmt)

    print_report(manifest_path, result.changes)
    return 0


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()

    try:
        if args.git_repo:
            cwd = Path.cwd()
            manifest_rel_path = Path(args.manifest)
            exit_code = run_git_mode(
                repo_url=args.git_repo,
                manifest_rel_path=manifest_rel_path,
                config_path=config_path,
                cwd=cwd,
            )
        else:
            manifest_path = Path(args.manifest).expanduser().resolve()
            exit_code = run(manifest_path=manifest_path, config_path=config_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except OSError as exc:
        print(f"File error: {exc}", file=sys.stderr)
        raise SystemExit(3) from exc

    raise SystemExit(exit_code)


def print_report(manifest_path: Path, changes: list[ChangeRecord]) -> None:
    print(f"Manifest: {manifest_path}")
    if not changes:
        print("No permission changes were applied.")
        return

    print("Permission changes:")
    for idx, item in enumerate(changes, start=1):
        from_value = format_permission_arg(item.old_arg)
        to_value = (
            format_permission_arg(item.new_arg) if item.new_arg is not None else "REMOVED"
        )
        print(f"{idx}. [{item.category}] {from_value} -> {to_value}")


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"File not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return data


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise ValueError(f"Manifest file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in manifest {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Manifest root must be a mapping: {path}")
        return data, "json"

    if suffix in {".yaml", ".yml"}:
        data = load_yaml(path)
        return data, "yaml"

    raise ValueError(
        f"Unsupported manifest extension '{suffix}'. Use .json, .yaml, or .yml."
    )


def save_manifest(path: Path, data: dict[str, Any], fmt: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        if fmt == "json":
            json.dump(data, handle, indent=2)
            handle.write("\n")
            return
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=False)


def ensure_manifest_shape(manifest: dict[str, Any], manifest_path: Path) -> list[Any]:
    finish_args = manifest.get("finish-args")
    if finish_args is None:
        raise ValueError(f"Manifest has no 'finish-args': {manifest_path}")
    if not isinstance(finish_args, list):
        raise ValueError(f"'finish-args' must be a list: {manifest_path}")
    return finish_args


def backup_manifest(manifest_path: Path) -> Path:
    backup = manifest_path.with_name(f"{manifest_path.name}.original")
    shutil.copy2(manifest_path, backup)
    return backup


def apply_rules_to_finish_args(
    finish_args: list[Any],
    rules_by_category: dict[str, CategoryRules],
) -> TrimResult:
    new_finish_args: list[str] = []
    changes: list[ChangeRecord] = []

    for raw_arg in finish_args:
        if not isinstance(raw_arg, str):
            new_finish_args.append(str(raw_arg))
            continue

        parsed = parse_permission_arg(raw_arg)
        if parsed is None:
            new_finish_args.append(raw_arg)
            continue

        category = parsed.category
        value = parsed.value
        rules_for_category = rules_by_category.get(category)
        if not rules_for_category:
            new_finish_args.append(raw_arg)
            continue

        remove_values = rules_for_category.remove
        replace_values = rules_for_category.replace

        if value in remove_values:
            changes.append(ChangeRecord(category=category, old_arg=raw_arg, new_arg=None))
            continue

        if value not in replace_values:
            new_finish_args.append(raw_arg)
            continue

        replacement = replace_values[value]
        if replacement is None:
            changes.append(ChangeRecord(category=category, old_arg=raw_arg, new_arg=None))
            continue

        new_arg = f"--{category}={replacement}"
        if new_arg != raw_arg:
            changes.append(ChangeRecord(category=category, old_arg=raw_arg, new_arg=new_arg))
        new_finish_args.append(new_arg)

    return TrimResult(finish_args=new_finish_args, changes=changes)


def validate_rules(rules: dict[str, Any]) -> dict[str, CategoryRules]:
    normalized: dict[str, CategoryRules] = {}
    categories = rules.get("categories", {})
    if not isinstance(categories, dict):
        raise ValueError("Config key 'categories' must be a mapping.")

    for category, cfg in categories.items():
        if not isinstance(category, str):
            raise ValueError("Each category name must be a string.")
        if not isinstance(cfg, dict):
            raise ValueError(f"Category '{category}' must be a mapping.")

        remove = cfg.get("remove", [])
        replace = cfg.get("replace", {})

        if not isinstance(remove, list) or not all(isinstance(i, str) for i in remove):
            raise ValueError(f"Category '{category}': 'remove' must be a string list.")
        if not isinstance(replace, dict) or not all(
            isinstance(k, str) for k in replace.keys()
        ):
            raise ValueError(f"Category '{category}': 'replace' must be a mapping.")

        for key, value in replace.items():
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    f"Category '{category}': replace value for '{key}' must be string or null."
                )

        normalized[category] = CategoryRules(
            remove=set(remove),
            replace=replace,
        )

    return normalized


def parse_permission_arg(arg: str) -> PermissionArg | None:
    if not isinstance(arg, str) or not arg.startswith("--"):
        return None
    body = arg[2:]
    if "=" not in body:
        return None
    key, value = body.split("=", 1)
    if not key or value == "":
        return None
    return PermissionArg(category=key, value=value)


def format_permission_arg(arg: str | None) -> str:
    if arg is None:
        return "REMOVED"
    return arg[2:] if arg.startswith("--") else arg


def checkout_repo(repo_url: str, *, cwd: Path) -> None:
    # Avoid surprising behavior by cloning into a non-empty directory.
    if any(cwd.iterdir()):
        raise ValueError(
            f"Refusing to clone into non-empty directory: {cwd}. Use an empty directory or run without git mode."
        )

    print(f"Cloning repo into {cwd}")
    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, "."],
        cwd=str(cwd),
        check=True,
    )


def run_git_mode(
    repo_url: str, *, manifest_rel_path: Path, config_path: Path, cwd: Path
) -> int:
    if manifest_rel_path.is_absolute():
        raise ValueError(
            "In --mode=git, --manifest must be a path relative to the checked-out repo root."
        )
    if any(part == ".." for part in manifest_rel_path.parts):
        raise ValueError("In --mode=git, --manifest must not contain '..'.")
    if not repo_url:
        raise ValueError("Missing --repo-url for --mode=git.")

    checkout_repo(repo_url, cwd=cwd)

    manifest_path = (cwd / manifest_rel_path).resolve()
    return run(manifest_path=manifest_path, config_path=config_path)


if __name__ == "__main__":
    main()
