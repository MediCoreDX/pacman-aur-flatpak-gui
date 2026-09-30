import os
import subprocess
import sys
import threading
import time
import unittest
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


if __name__ == "__main__":
    unittest.main()
