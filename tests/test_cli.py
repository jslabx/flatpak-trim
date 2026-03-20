import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import flatpak_trim


class CliRoutingTest(unittest.TestCase):
    def test_main_routes_to_edit_installed_when_app_id_is_provided(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            config = tmp / "rules.yaml"
            config.write_text("categories: {}", encoding="utf-8")

            with patch.object(
                flatpak_trim, "run_edit_installed", return_value=0
            ) as run_edit_installed_mock, patch.object(
                flatpak_trim, "run", return_value=0
            ) as run_mock, patch.object(
                flatpak_trim, "run_git_mode", return_value=0
            ) as run_git_mode_mock, patch(
                "sys.argv",
                [
                    "flatpak_trim.py",
                    "--app-id",
                    "com.example.App",
                    "--config",
                    str(config),
                ],
            ):
                with self.assertRaises(SystemExit) as exc:
                    flatpak_trim.main()

            self.assertEqual(0, exc.exception.code)
            run_edit_installed_mock.assert_called_once()
            run_mock.assert_not_called()
            run_git_mode_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

