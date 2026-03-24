#!/usr/bin/env python3
"""
Trim Flatpak manifest permissions using YAML config.

Usage:
  python flatpak_trim.py --manifest com.example.App.yaml --config config.yaml
  python flatpak_trim.py --git-repo <repo-url> --manifest path/in/repo.yaml --config config.yaml

Edit permissions for an installed Flatpak app (via overrides):
  python flatpak_trim.py --app-id com.example.App --config config.yaml
"""

from __future__ import annotations
import argparse
import copy
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable

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
        description="Trim Flatpak manifest permissions or edit installed app overrides based on YAML rules."
    )
    parser.add_argument(
        "--git-repo",
        dest="git_repo",
        help="(trim-manifest) Enable git mode by cloning this repo into the current directory (accepts SSH and HTTP URIs).",
    )
    parser.add_argument(
        "--manifest",
        help="(trim-manifest) Path to Flatpak manifest file (.yaml/.yml/.json). In --git-repo mode, this is relative to the checked-out repo root.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to YAML config file with permission trim rules.",
    )
    parser.add_argument(
        "--app-id",
        help="(edit-installed) Flatpak application id to update (for example: org.gnome.gedit).",
    )
    parser.add_argument(
        "--system",
        action="store_true",
        help="(edit-installed) Apply overrides system-wide instead of per-user.",
    )
    return parser.parse_args()

def run(manifest_path: Path, config_path: Path) -> int:
    config = load_yaml(config_path)
    rules_by_category = validate_rules(config)

    manifest, manifest_fmt = load_manifest(manifest_path)
    finish_args = ensure_manifest_shape(manifest, manifest_path)
    finish_args_before = copy.deepcopy(finish_args)

    result = apply_rules_to_manifest(
        finish_args=finish_args_before,
        rules_by_category=rules_by_category,
    )

    backup_manifest(manifest_path)
    manifest["finish-args"] = result.finish_args
    save_manifest(manifest_path, manifest, manifest_fmt)

    print_manifest_diff(manifest_path, result.changes)
    return 0


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()

    try:
        if args.app_id:
            if args.git_repo:
                raise ValueError("--git-repo is only supported in trim-manifest mode.")
            exit_code = run_edit_installed(
                app_id=args.app_id,
                config_path=config_path,
                system=bool(args.system),
            )
        else:
            if not args.manifest:
                raise ValueError("--manifest is required in trim manifest mode.")
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


def print_manifest_diff(manifest_path: Path, changes: list[ChangeRecord]) -> None:
    print(f"Manifest: {manifest_path}")
    if not changes:
        print("No permission changes were applied.")
        return

    print_diff(changes)

def print_override_diff(app_id: str, changes: list[ChangeRecord]) -> None:
    print(f"App: {app_id}")
    if not changes:
        print("No permission changes were applied.")
    else:
        print_diff(changes)

    print(f"View permissions with: flatpak info --show-permissions {app_id}")


def print_diff(changes: list[ChangeRecord]) -> None:
    print("Permission changes:")
    for idx, item in enumerate[ChangeRecord](changes, start=1):
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


def apply_rules_to_manifest(
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


@dataclass(frozen=True)
class CategoryOverride:
    set_flag: Callable[[str], str]
    unset_flag: Callable[[str], str] | None


def _parse_env_var_value(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(
            f"Invalid env finish-arg value '{value}'. Expected 'VAR=VALUE'."
        )
    var, rhs = value.split("=", 1)
    if not var or not rhs:
        raise ValueError(
            f"Invalid env finish-arg value '{value}'. Expected 'VAR=VALUE'."
        )
    return var, rhs


def _validate_app_id(app_id: str) -> None:
    if not isinstance(app_id, str) or not app_id:
        raise ValueError("Invalid --app-id (must be a non-empty string).")
    if not re.match(r"^[A-Za-z0-9_.-]+$", app_id):
        raise ValueError(f"Invalid --app-id '{app_id}'.")


def _build_override_commands() -> dict[str, CategoryOverride]:
    return {
        "filesystem": CategoryOverride(
            set_flag=lambda v: f"--filesystem={v}",
            unset_flag=lambda v: f"--nofilesystem={v}",
        ),
        "socket": CategoryOverride(
            set_flag=lambda v: f"--socket={v}",
            unset_flag=lambda v: f"--nosocket={v}",
        ),
        "share": CategoryOverride(
            set_flag=lambda v: f"--share={v}",
            unset_flag=lambda v: f"--unshare={v}",
        ),
        "device": CategoryOverride(
            set_flag=lambda v: f"--device={v}",
            unset_flag=lambda v: f"--nodevice={v}",
        ),
        "allow": CategoryOverride(
            set_flag=lambda v: f"--allow={v}",
            unset_flag=lambda v: f"--disallow={v}",
        ),
        "talk-name": CategoryOverride(
            set_flag=lambda v: f"--talk-name={v}",
            unset_flag=lambda v: f"--no-talk-name={v}",
        ),
        "system-talk-name": CategoryOverride(
            set_flag=lambda v: f"--system-talk-name={v}",
            unset_flag=lambda v: f"--system-no-talk-name={v}",
        ),
        "env": CategoryOverride(
            set_flag=lambda v: f"--env={v}",
            unset_flag=lambda v: f"--unset-env={_parse_env_var_value(v)[0]}",
        ),
        "add-policy": CategoryOverride(
            set_flag=lambda v: f"--add-policy={v}",
            unset_flag=lambda v: f"--remove-policy={v}",
        ),
        # Removal/unsetting is not currently available for these categories.
        "own-name": CategoryOverride(set_flag=lambda v: f"--own-name={v}", unset_flag=None),
        "unset-env": CategoryOverride(
            set_flag=lambda v: f"--unset-env={v}", unset_flag=None
        ),
        "persist": CategoryOverride(
            set_flag=lambda v: f"--persist={v}", unset_flag=None
        ),
    }


def _build_installed_override_changes_and_flags(
    rules_by_category: dict[str, CategoryRules],
) -> tuple[list[ChangeRecord], list[str], list[str]]:
    specs = _build_override_commands()

    flags: list[str] = []
    changes: list[ChangeRecord] = []
    warnings: list[str] = []

    for category in sorted(rules_by_category.keys()):
        rules = rules_by_category[category]
        spec = specs.get(category)
        if spec is None:
            raise ValueError(
                f"Unsupported category '{category}' in edit-installed mode."
            )

        for old_value in sorted(rules.remove):
            if spec.unset_flag is None:
                raise ValueError(
                    f"Cannot remove '{category}={old_value}' via flatpak override."
                )
            flags.append(spec.unset_flag(old_value))
            changes.append(
                ChangeRecord(
                    category=category,
                    old_arg=f"--{category}={old_value}",
                    new_arg=None,
                )
            )

        for old_value in sorted(rules.replace.keys()):
            new_value = rules.replace[old_value]
            if new_value is None:
                if spec.unset_flag is None:
                    raise ValueError(
                        f"Cannot remove '{category}={old_value}' via flatpak override."
                    )
                flags.append(spec.unset_flag(old_value))
                changes.append(
                    ChangeRecord(
                        category=category,
                        old_arg=f"--{category}={old_value}",
                        new_arg=None,
                    )
                )
                continue

            if category == "env":
                old_var, _old_rhs = _parse_env_var_value(old_value)
                new_var, _new_rhs = _parse_env_var_value(new_value)
                if old_var != new_var:
                    if spec.unset_flag is None:
                        raise ValueError(
                            f"Cannot remove env '{old_var}' via flatpak override."
                        )
                    flags.append(spec.unset_flag(old_value))
                flags.append(spec.set_flag(new_value))
            else:
                if spec.unset_flag is None:
                    warnings.append(
                        f"Cannot remove '{category}={old_value}' via flatpak override; it will remain alongside the replacement."
                    )
                else:
                    flags.append(spec.unset_flag(old_value))
                flags.append(spec.set_flag(new_value))

            changes.append(
                ChangeRecord(
                    category=category,
                    old_arg=f"--{category}={old_value}",
                    new_arg=f"--{category}={new_value}",
                )
            )

    return changes, flags, warnings


def run_edit_installed(*, app_id: str, config_path: Path, system: bool) -> int:
    _validate_app_id(app_id)

    config = load_yaml(config_path)
    rules_by_category = validate_rules(config)
    changes, flags, warnings = _build_installed_override_changes_and_flags(
        rules_by_category=rules_by_category
    )

    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    if flags:
        scope_flag = "--system" if system else "--user"
        cmd = ["flatpak", "override", scope_flag, *flags, app_id]
        subprocess.run(cmd, check=True)

    print_override_diff(app_id=app_id, changes=changes)
    return 0


if __name__ == "__main__":
    main()
