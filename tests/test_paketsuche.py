import os
import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import paketsuche


class CommandTests(unittest.TestCase):
    def test_builds_repo_and_aur_install_commands(self):
        packages = [
            {"name": "firefox", "source": "repo"},
            {"name": "yay-git", "source": "aur"},
        ]

        self.assertEqual(
            paketsuche.build_install_commands_for_selection(packages),
            [
                ["sudo", "pacman", "-S", "firefox"],
                ["yay", "-S", "yay-git"],
            ],
        )

    def test_builds_flatpak_install_uninstall_and_batch_commands(self):
        self.assertEqual(
            paketsuche.build_install_command(
                {
                    "name": "org.mozilla.firefox",
                    "source": "flatpak",
                    "repo": "flathub",
                    "installed": False,
                }
            ),
            ["flatpak", "install", "flathub", "org.mozilla.firefox"],
        )
        self.assertEqual(
            paketsuche.build_install_command(
                {
                    "name": "org.mozilla.firefox",
                    "source": "flatpak",
                    "installed": True,
                    "installation": "user",
                }
            ),
            ["flatpak", "uninstall", "--user", "org.mozilla.firefox"],
        )
        self.assertEqual(
            paketsuche.build_install_commands_for_selection(
                [
                    {
                        "name": "org.mozilla.firefox",
                        "source": "flatpak",
                        "repo": "flathub",
                    },
                    {
                        "name": "com.visualstudio.code",
                        "source": "flatpak",
                        "repo": "flathub",
                    },
                ]
            ),
            [[
                "flatpak", "install", "flathub",
                "org.mozilla.firefox", "com.visualstudio.code",
            ]],
        )

    def test_rejects_invalid_package_names_and_sources(self):
        with self.assertRaises(ValueError):
            paketsuche.build_install_command(
                {"name": "foo; touch /tmp/pwned", "source": "repo", "installed": False}
            )
        with self.assertRaises(ValueError):
            paketsuche.build_install_command(
                {"name": "-S", "source": "repo", "installed": False}
            )
        with self.assertRaises(ValueError):
            paketsuche.build_install_command(
                {"name": "foo", "source": "unknown", "installed": False}
            )
        with self.assertRaises(ValueError):
            paketsuche.build_install_command(
                {
                    "name": "org.example.App",
                    "source": "flatpak",
                    "repo": "--user",
                    "installed": False,
                }
            )

    def test_parses_flatpak_search_results_and_installed_scope(self):
        output = """[
          {
            "application_id": "org.mozilla.firefox",
            "name": "Firefox",
            "description": "Private browser",
            "version": "128.0",
            "remotes": "flathub"
          }
        ]"""

        results = paketsuche.parse_flatpak_search_output(
            output,
            {
                "org.mozilla.firefox": {
                    "installation": "user",
                    "version": "128.0",
                }
            },
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "org.mozilla.firefox")
        self.assertEqual(results[0]["repo"], "flathub")
        self.assertTrue(results[0]["installed"])
        self.assertEqual(results[0]["installation"], "user")
        self.assertEqual(results[0]["description"], "Private browser")

    @patch("paketsuche.subprocess.run")
    @patch.object(paketsuche.PackageSearchApp, "search_command")
    def test_flatpak_search_marks_locally_installed_apps(self, search_command, run):
        search_command.return_value = (
            [{
                "application_id": "org.mozilla.firefox",
                "name": "Firefox",
                "description": "Private browser",
                "version": "128.0",
                "remotes": "flathub",
            }],
            None,
        )
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout='[{"application_id":"org.mozilla.firefox",'
            '"installation":"system"}]',
            stderr="",
        )
        app = paketsuche.PackageSearchApp.__new__(paketsuche.PackageSearchApp)

        results, error = app.search_flatpak("firefox", threading.Event())

        self.assertIsNone(error)
        self.assertTrue(results[0]["installed"])
        self.assertEqual(results[0]["installation"], "system")
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_parses_flatpak_update_json(self):
        updates = paketsuche.parse_flatpak_updates(
            """[{
              "application_id": "org.mozilla.firefox",
              "name": "Firefox",
              "version": "128.0",
              "branch": "stable",
              "origin": "flathub"
            }]"""
        )

        self.assertEqual(
            updates,
            ["Firefox (org.mozilla.firefox) 128.0 stable flathub"],
        )


    def test_command_sequence_never_uses_a_shell(self):
        command = paketsuche.build_sequence_command(
            [["sudo", "pacman", "-S", "firefox"], ["yay", "-S", "yay-git"]]
        )

        self.assertEqual(command[0], paketsuche.sys.executable)
        self.assertNotIn("bash", command)

    def test_command_sequence_runs_in_order_and_stops_on_failure(self):
        command = paketsuche.build_sequence_command(
            [
                [sys.executable, "-c", "print('first')"],
                [sys.executable, "-c", "print('second')"],
            ]
        )
        result = subprocess.run(command, capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["first", "second"])

        failing_command = paketsuche.build_sequence_command(
            [
                [sys.executable, "-c", "raise SystemExit(7)"],
                [sys.executable, "-c", "print('must not run')"],
            ]
        )
        failed = subprocess.run(
            failing_command, capture_output=True, text=True, check=False
        )
        self.assertEqual(failed.returncode, 7)
        self.assertEqual(failed.stdout, "")

    def test_parse_output_reads_package_and_description(self):
        output = (
            "extra/firefox 128.0-1 [installed]\n"
            "    Fast, private web browser\n"
            "aur/yay-git 12.4.2-1\n"
            "    Yet another yogurt\n"
        )

        results = paketsuche.PackageSearchApp.parse_output(output, "repo")

        self.assertEqual([item["name"] for item in results], ["firefox", "yay-git"])
        self.assertTrue(results[0]["installed"])
        self.assertEqual(results[0]["description"], "Fast, private web browser")
        self.assertFalse(results[1]["installed"])

    def test_live_process_uses_pty_and_accepts_interactive_input(self):
        dialog = paketsuche.LiveOutputDialog.__new__(paketsuche.LiveOutputDialog)
        dialog.commands = [[
            sys.executable,
            "-c",
            "print('Password: ', end='', flush=True); "
            "print('received=' + input(), flush=True)",
        ]]
        dialog.process = None
        dialog.master_fd = None
        dialog.process_lock = threading.Lock()
        dialog.fd_lock = threading.Lock()
        dialog.cancel_requested = threading.Event()
        dialog.awaiting_password = False
        dialog.finished = False
        dialog.closing = False
        callbacks = []
        dialog.after = lambda delay, callback, *args: callbacks.append((callback, args))

        worker = threading.Thread(target=dialog.run_process)
        worker.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            output = "".join(
                args[0]
                for callback, args in callbacks
                if callback == dialog.append_text
            )
            if "Password:" in output:
                break
            time.sleep(0.01)

        if "Password:" not in output:
            if dialog.process is not None:
                os.killpg(dialog.process.pid, 9)
            worker.join(timeout=2)
            self.fail(f"PTY relay did not display prompt: {output!r}")

        self.assertIsNotNone(dialog.master_fd)
        os.write(dialog.master_fd, b"test-input\n")
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        output = "".join(
            args[0]
            for callback, args in callbacks
            if callback == dialog.append_text
        )
        self.assertIn("received=test-input", output)
        self.assertIn(
            (dialog.on_finished, (0,)),
            callbacks,
        )

    def test_live_process_abort_stops_the_process_group(self):
        dialog = paketsuche.LiveOutputDialog.__new__(paketsuche.LiveOutputDialog)
        dialog.commands = [[sys.executable, "-c", "import time; time.sleep(60)"]]
        dialog.process = None
        dialog.master_fd = None
        dialog.process_lock = threading.Lock()
        dialog.fd_lock = threading.Lock()
        dialog.cancel_requested = threading.Event()
        dialog.awaiting_password = False
        dialog.finished = False
        dialog.closing = False
        dialog.after = lambda delay, callback, *args: None
        dialog.append_text = lambda text: None

        worker = threading.Thread(target=dialog.run_process)
        worker.start()
        deadline = time.monotonic() + 5
        while dialog.process is None and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertIsNotNone(dialog.process)
        dialog.abort_process()
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive())

    @patch("paketsuche.subprocess.Popen")
    def test_search_passes_query_as_one_argument(self, popen):
        query = "name; touch /tmp/not-executed"
        process = Mock()
        process.communicate.return_value = ("extra/pkg 1.0-1\n    package\n", "")
        process.returncode = 0
        popen.return_value = process

        results, error = paketsuche.PackageSearchApp.search_command(
            ["pacman", "-Ss", query], threading.Event(), "repo"
        )

        self.assertIsNone(error)
        self.assertEqual(results[0]["name"], "pkg")
        self.assertEqual(popen.call_args.args[0], ["pacman", "-Ss", query])
        self.assertFalse(popen.call_args.kwargs.get("shell", False))

    @patch("paketsuche.subprocess.Popen")
    def test_flatpak_search_treats_blank_output_as_no_results(self, popen):
        process = Mock()
        process.communicate.return_value = (" \n\t", "")
        process.returncode = 0
        popen.return_value = process

        results, error = paketsuche.PackageSearchApp.search_command(
            ["flatpak", "search", "--json", "whasapp"],
            threading.Event(),
            "flatpak",
        )

        self.assertEqual(results, [])
        self.assertIsNone(error)

    @patch("paketsuche.subprocess.Popen")
    def test_flatpak_search_preserves_malformed_json_diagnostic(self, popen):
        process = Mock()
        process.communicate.return_value = ("not json", "")
        process.returncode = 0
        popen.return_value = process

        results, error = paketsuche.PackageSearchApp.search_command(
            ["flatpak", "search", "--json", "whasapp"],
            threading.Event(),
            "flatpak",
        )

        self.assertEqual(results, [])
        self.assertIn("flatpak search lieferte ungültiges JSON:", error)

    @patch("paketsuche.subprocess.Popen")
    def test_flatpak_search_preserves_command_failure_diagnostic(self, popen):
        process = Mock()
        process.communicate.return_value = ("error: no remotes configured\n", "")
        process.returncode = 1
        popen.return_value = process

        results, error = paketsuche.PackageSearchApp.search_command(
            ["flatpak", "search", "--json", "whasapp"],
            threading.Event(),
            "flatpak",
        )

        self.assertEqual(results, [])
        self.assertEqual(
            error,
            "flatpak fehlgeschlagen: error: no remotes configured",
        )

    def test_linux_mouse_buttons_scroll_results_when_pointer_is_inside(self):
        canvas = Mock()
        canvas.yview.return_value = (0.0, 0.5)
        scrollable = Mock()
        scrollable._parent_canvas = canvas
        scrollable.check_if_master_is_canvas.return_value = True

        result = paketsuche.scroll_frame_with_mousewheel(
            scrollable, SimpleNamespace(widget=object(), num=4, delta=0)
        )

        self.assertEqual(result, "break")
        canvas.yview_scroll.assert_called_once_with(-3, "units")

    def test_linux_mouse_buttons_ignore_widgets_outside_results(self):
        canvas = Mock()
        scrollable = Mock()
        scrollable._parent_canvas = canvas
        scrollable.check_if_master_is_canvas.return_value = False

        result = paketsuche.scroll_frame_with_mousewheel(
            scrollable, SimpleNamespace(widget=object(), num=5, delta=0)
        )

        self.assertIsNone(result)
        canvas.yview_scroll.assert_not_called()

    def test_mousewheel_does_not_consume_event_when_content_fits(self):
        canvas = Mock()
        canvas.yview.return_value = (0.0, 1.0)
        scrollable = Mock()
        scrollable._parent_canvas = canvas
        scrollable.check_if_master_is_canvas.return_value = True

        result = paketsuche.scroll_frame_with_mousewheel(
            scrollable, SimpleNamespace(widget=object(), num=5, delta=0)
        )

        self.assertIsNone(result)
        canvas.yview_scroll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
