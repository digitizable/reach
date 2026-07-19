"""Unit tests for Spotify exclude config seeding (no GTK)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.apps import RoutedApp
from core.launcher import (
    argv_for_separate_instance,
    ensure_spectre_spotify_config,
    resolve_spotify_config_dir,
)


def _app() -> RoutedApp:
    return RoutedApp(
        id="sys:spotify.desktop",
        name="Spotify",
        command="spotify",
        desktop_id="spotify.desktop",
        source="system",
    )


class SpotifyConfigSeedTests(unittest.TestCase):
    def test_resolve_spotify_config_dir_prefers_prefs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            cfg = home / ".config" / "spotify"
            cfg.mkdir(parents=True)
            (cfg / "prefs").write_text('autologin.blob="x"\n', encoding="utf-8")
            with mock.patch.object(Path, "home", return_value=home):
                with mock.patch.dict("os.environ", {}, clear=False):
                    # Ensure we do not pick up the real session XDG path.
                    env = {k: v for k, v in __import__("os").environ.items() if k != "XDG_CONFIG_HOME"}
                    with mock.patch.dict("os.environ", env, clear=True):
                        self.assertEqual(resolve_spotify_config_dir(), cfg)

    def test_ensure_spotify_copies_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            src = home / ".config" / "spotify"
            src.mkdir(parents=True)
            (src / "prefs").write_text('autologin.blob="secret"\n', encoding="utf-8")
            users = src / "Users" / "me-user"
            users.mkdir(parents=True)
            (users / "prefs").write_text("a=1\n", encoding="utf-8")
            data = root / "spectre-data"

            with mock.patch.object(Path, "home", return_value=home):
                with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(home / ".config")}):
                    with mock.patch("app_config.user_data_dir", return_value=data):
                        note1 = ensure_spectre_spotify_config(_app(), tag="exclude")
                        dest = (
                            data
                            / "instances"
                            / "exclude"
                            / "sys-spotify.desktop"
                            / "config"
                            / "spotify"
                        )
                        self.assertTrue(dest.is_dir())
                        self.assertEqual(
                            (dest / "prefs").read_text(encoding="utf-8"),
                            'autologin.blob="secret"\n',
                        )
                        self.assertTrue((dest / "Users" / "me-user" / "prefs").is_file())
                        self.assertTrue(
                            "copied" in note1.lower() or "from" in note1.lower()
                        )

                        # Second call must not re-copy (even if source changes).
                        (src / "prefs").write_text(
                            'autologin.blob="changed"\n', encoding="utf-8"
                        )
                        note2 = ensure_spectre_spotify_config(_app(), tag="exclude")
                        self.assertEqual(
                            (dest / "prefs").read_text(encoding="utf-8"),
                            'autologin.blob="secret"\n',
                        )
                        self.assertIn("Spectre Spotify", note2)

    def test_argv_spotify_seeds_and_flags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            src = home / ".config" / "spotify"
            src.mkdir(parents=True)
            (src / "prefs").write_text("autologin.blob=1\n", encoding="utf-8")
            data = root / "spectre-data"

            with mock.patch.object(Path, "home", return_value=home):
                with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(home / ".config")}):
                    with mock.patch("app_config.user_data_dir", return_value=data):
                        argv, note, private_xdg = argv_for_separate_instance(
                            ["spotify"], _app(), tag="exclude"
                        )

            self.assertTrue(private_xdg)
            self.assertEqual(argv[0], "spotify")
            self.assertTrue(any(a.startswith("--mu=") for a in argv))
            self.assertTrue(any(a.startswith("--user-data-dir=") for a in argv))
            self.assertTrue("Spotify" in note or "spotify" in note.lower())
            dest = (
                data
                / "instances"
                / "exclude"
                / "sys-spotify.desktop"
                / "config"
                / "spotify"
            )
            self.assertTrue((dest / "prefs").is_file())


if __name__ == "__main__":
    unittest.main()
