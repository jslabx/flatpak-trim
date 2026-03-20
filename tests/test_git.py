import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

import flatpak_trim


class GitModeTest(unittest.TestCase):
    def test_checkout_repo_uses_git_clone_in_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cwd = Path(tmp_dir)
            repo_url = "https://example.com/some/repo.git"

            with patch("flatpak_trim.subprocess.run") as run_mock, patch(
                "builtins.print"
            ):
                flatpak_trim.checkout_repo(repo_url, cwd=cwd)

            run_mock.assert_called_once()
            args, kwargs = run_mock.call_args
            self.assertEqual(
                ["git", "clone", "--depth", "1", repo_url, "."],
                args[0],
            )
            self.assertEqual(str(cwd), kwargs["cwd"])
            self.assertTrue(kwargs["check"])

    def test_git_mode_calls_manifest_functions_against_checked_out_repo(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cwd = Path(tmp_dir)
            manifest_path = cwd / "app.yaml"
            config_path = cwd / "rules.yaml"

            manifest_dict = {
                "id": "com.example.App",
                "finish-args": [
                    "--share=network",
                    "--filesystem=home",
                    "--socket=fallback-x11",
                ],
            }
            manifest_path.write_text(
                yaml.safe_dump(manifest_dict, sort_keys=False),
                encoding="utf-8",
            )

            config_path.write_text(
                yaml.safe_dump(
                    {
                        "categories": {
                            "share": {"remove": ["network"], "replace": {}},
                            "filesystem": {
                                "remove": [],
                                "replace": {"home": "xdg-documents:ro"},
                            },
                        }
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            load_manifest_mock = Mock(return_value=(manifest_dict, "yaml"))
            backup_manifest_mock = Mock(return_value=cwd / "app.yaml.original")
            save_manifest_mock = Mock()
            print_report_mock = Mock()

            with patch.object(flatpak_trim, "checkout_repo") as checkout_mock, patch.object(
                flatpak_trim, "load_manifest", load_manifest_mock
            ), patch.object(
                flatpak_trim, "backup_manifest", backup_manifest_mock
            ), patch.object(
                flatpak_trim, "save_manifest", save_manifest_mock
            ), patch.object(
                flatpak_trim, "print_report", print_report_mock
            ):
                result = flatpak_trim.run_git_mode(
                    "https://example.com/some/repo.git",
                    manifest_rel_path=Path("app.yaml"),
                    config_path=config_path,
                    cwd=cwd,
                )

            self.assertEqual(0, result)
            checkout_mock.assert_called_once_with(
                "https://example.com/some/repo.git",
                cwd=cwd,
            )

            # Validate the manifest path used throughout the trimming pipeline.
            expected_manifest_path = manifest_path.resolve()
            load_manifest_mock.assert_called_once_with(expected_manifest_path)
            backup_manifest_mock.assert_called_once_with(expected_manifest_path)
            save_manifest_mock.assert_called_once()
            save_args, _ = save_manifest_mock.call_args
            self.assertEqual(expected_manifest_path, save_args[0])

            # `run()` should update the manifest in memory before saving.
            saved_manifest_dict = save_args[1]
            self.assertEqual(
                ["--filesystem=xdg-documents:ro", "--socket=fallback-x11"],
                saved_manifest_dict["finish-args"],
            )

    def test_main_routes_to_git_mode_when_git_repo_provided(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            manifest = tmp / "app.yaml"
            config = tmp / "rules.yaml"

            manifest.write_text("dummy: true", encoding="utf-8")
            config.write_text("dummy: true", encoding="utf-8")

            with patch.object(flatpak_trim, "run_git_mode", return_value=0) as run_git_mode_mock, patch.object(
                flatpak_trim, "run", return_value=0
            ) as run_mock, patch(
                "sys.argv",
                [
                    "flatpak_trim.py",
                    "--git-repo",
                    "https://example.com/repo.git",
                    "--manifest",
                    "app.yaml",
                    "--config",
                    str(config),
                ],
            ):
                with self.assertRaises(SystemExit) as exc:
                    flatpak_trim.main()

            self.assertEqual(0, exc.exception.code)
            run_git_mode_mock.assert_called_once()
            run_mock.assert_not_called()

    def test_main_routes_to_local_mode_when_git_repo_not_provided(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            manifest_path = tmp / "app.yaml"
            config_path = tmp / "rules.yaml"

            manifest_path.write_text("dummy: true", encoding="utf-8")
            config_path.write_text("dummy: true", encoding="utf-8")

            with patch.object(flatpak_trim, "run", return_value=0) as run_mock, patch.object(
                flatpak_trim, "run_git_mode", return_value=0
            ) as run_git_mode_mock, patch(
                "sys.argv",
                [
                    "flatpak_trim.py",
                    "--manifest",
                    str(manifest_path),
                    "--config",
                    str(config_path),
                ],
            ):
                with self.assertRaises(SystemExit) as exc:
                    flatpak_trim.main()

            self.assertEqual(0, exc.exception.code)
            run_mock.assert_called_once()
            run_git_mode_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

