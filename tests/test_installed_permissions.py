import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

import flatpak_trim


class InstalledPermissionsTest(unittest.TestCase):
    def test_run_edit_installed_builds_expected_override_command_and_prints_view_line(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            config_path = tmp / "rules.yaml"

            config_data = {
                "categories": {
                    "filesystem": {
                        "remove": ["home"],
                        "replace": {"xdg-download": "xdg-documents:ro"},
                    },
                    "socket": {
                        "remove": ["x11"],
                        "replace": {},
                    },
                }
            }

            config_path.write_text(
                yaml.safe_dump(config_data, sort_keys=False),
                encoding="utf-8",
            )

            app_id = "com.example.App"

            with patch("flatpak_trim.subprocess.run") as run_mock:
                output = io.StringIO()
                with redirect_stdout(output):
                    result = flatpak_trim.run_edit_installed(
                        app_id=app_id,
                        config_path=config_path,
                        system=False,
                    )

            self.assertEqual(0, result)

            run_mock.assert_called_once()
            (cmd,), kwargs = run_mock.call_args
            self.assertEqual(
                [
                    "flatpak",
                    "override",
                    "--user",
                    "--nofilesystem=home",
                    "--nofilesystem=xdg-download",
                    "--filesystem=xdg-documents:ro",
                    "--nosocket=x11",
                    app_id,
                ],
                cmd,
            )
            self.assertTrue(kwargs.get("check"))

            lines = output.getvalue().strip().splitlines()
            self.assertEqual(
                f"View permissions with: flatpak info --show-permissions {app_id}",
                lines[-1],
            )

    def test_run_edit_installed_env_remove_unsets_env_var(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            config_path = tmp / "rules.yaml"

            config_data = {"categories": {"env": {"remove": ["FOO=bar"], "replace": {}}}}
            config_path.write_text(
                yaml.safe_dump(config_data, sort_keys=False),
                encoding="utf-8",
            )

            app_id = "com.example.App"

            with patch("flatpak_trim.subprocess.run") as run_mock:
                output = io.StringIO()
                with redirect_stdout(output):
                    flatpak_trim.run_edit_installed(
                        app_id=app_id,
                        config_path=config_path,
                        system=False,
                    )

            run_mock.assert_called_once()
            (cmd,), _kwargs = run_mock.call_args
            self.assertEqual(
                ["flatpak", "override", "--user", "--unset-env=FOO", app_id],
                cmd,
            )

    def test_run_edit_installed_raises_on_unsupported_removal_own_name(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            config_path = tmp / "rules.yaml"

            config_data = {
                "categories": {
                    "own-name": {
                        "remove": ["org.example.Legacy"],
                        "replace": {},
                    }
                }
            }
            config_path.write_text(
                yaml.safe_dump(config_data, sort_keys=False),
                encoding="utf-8",
            )

            app_id = "com.example.App"

            with self.assertRaises(ValueError):
                flatpak_trim.run_edit_installed(
                    app_id=app_id,
                    config_path=config_path,
                    system=False,
                )


if __name__ == "__main__":
    unittest.main()

