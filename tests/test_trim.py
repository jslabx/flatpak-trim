import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional

import yaml

from flatpak_trim import apply_rules_to_finish_args, run, validate_rules


class FlatpakTrimTest(unittest.TestCase):
    def test_remove_and_replace_work_for_all_permission_categories(self):
        cases = [
            ("socket", "x11", "wayland"),
            ("filesystem", "home", "xdg-documents:ro"),
            ("device", "all", "dri"),
            ("share", "network", "ipc"),
            ("allow", "devel", "bluetooth"),
            ("talk-name", "org.freedesktop.Flatpak", "org.gtk.vfs.*"),
            ("system-talk-name", "org.freedesktop.login1", "org.freedesktop.UPower"),
            ("own-name", "org.example.Legacy", "org.example.Current"),
            ("env", "FOO=bar", "FOO=baz"),
            ("unset-env", "LD_PRELOAD", "OLD_ENV"),
            ("persist", ".my-app", ".my-app-v2"),
            ("add-policy", "filesystems=host:readonly", "filesystems=xdg-documents:readwrite"),
        ]

        for category, old_value, new_value in cases:
            with self.subTest(category=category, operation="remove"):
                self._assert_category_rule(
                    category=category,
                    original_value=old_value,
                    expected_value=None,
                    mode="remove",
                )

            with self.subTest(category=category, operation="replace"):
                self._assert_category_rule(
                    category=category,
                    original_value=old_value,
                    expected_value=new_value,
                    mode="replace",
                )

    def test_run_updates_manifest_in_place_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            manifest_path = tmp / "app.yaml"
            config_path = tmp / "rules.yaml"

            manifest_data = {
                "id": "com.example.App",
                "finish-args": [
                    "--share=network",
                    "--filesystem=home",
                    "--socket=fallback-x11",
                ],
            }
            config_data = {
                "categories": {
                    "share": {"remove": ["network"], "replace": {}},
                    "filesystem": {"remove": [], "replace": {"home": "xdg-documents:ro"}},
                    "socket": {"remove": [], "replace": {"fallback-x11": "wayland"}},
                }
            }

            manifest_path.write_text(
                yaml.safe_dump(manifest_data, sort_keys=False),
                encoding="utf-8",
            )
            config_path.write_text(
                yaml.safe_dump(config_data, sort_keys=False),
                encoding="utf-8",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                result = run(manifest_path=manifest_path, config_path=config_path)

            self.assertEqual(0, result)
            backup_path = tmp / "app.yaml.original"
            self.assertTrue(backup_path.exists())

            updated_manifest = yaml.safe_load(
                manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                ["--filesystem=xdg-documents:ro", "--socket=wayland"],
                updated_manifest["finish-args"],
            )

            backup_manifest = yaml.safe_load(backup_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest_data["finish-args"], backup_manifest["finish-args"])

            report = output.getvalue()
            self.assertIn(str(manifest_path), report)
            self.assertIn("share=network -> REMOVED", report)
            self.assertIn(
                "filesystem=home -> filesystem=xdg-documents:ro",
                report,
            )

    def _rules_for(self, category: str, *, remove=None, replace=None):
        config = {
            "categories": {
                category: {
                    "remove": remove or [],
                    "replace": replace or {},
                }
            }
        }
        return validate_rules(config)

    def _assert_category_rule(
        self,
        category: str,
        original_value: str,
        expected_value: Optional[str],
        mode: str,
    ):
        old_arg = f"--{category}={original_value}"
        if mode == "remove":
            rules = self._rules_for(category, remove=[original_value])
        elif mode == "replace":
            rules = self._rules_for(category, replace={original_value: expected_value})
        else:
            self.fail(f"Unknown mode: {mode}")

        result = apply_rules_to_finish_args([old_arg], rules)
        new_args = result.finish_args
        changes = result.changes
        if expected_value is None:
            self.assertEqual([], new_args)
            self.assertEqual(1, len(changes))
            self.assertIsNone(changes[0].new_arg)
            return

        expected_arg = f"--{category}={expected_value}"
        self.assertEqual([expected_arg], new_args)
        self.assertEqual(1, len(changes))
        self.assertEqual(expected_arg, changes[0].new_arg)


if __name__ == "__main__":
    unittest.main()

