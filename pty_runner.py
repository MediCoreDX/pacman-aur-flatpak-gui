#!/usr/bin/env python3
"""Relay a command through a real PTY from a subprocess-safe worker process."""

import errno
import json
import os
import pty
import select
import signal
import sys
import termios
import tty


child_pid = None


def forward_signal(signum, _frame):
    if child_pid is not None:
        try:
            os.killpg(child_pid, signum)
        except ProcessLookupError:
            pass
    if signum == signal.SIGTERM:
        signal.alarm(2)


def force_kill(_signum, _frame):
    if child_pid is not None:
        try:
            os.killpg(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def main():
    global child_pid

    command = json.loads(sys.argv[1])
    if not isinstance(command, list) or not command or not all(
        isinstance(arg, str) for arg in command
    ):
        raise SystemExit("Invalid command arguments")

    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)
    signal.signal(signal.SIGALRM, force_kill)

    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        try:
            os.execvp(command[0], command)
        except OSError as error:
            os.write(2, f"[Befehl konnte nicht gestartet werden: {error}]\n".encode())
            os._exit(127)

    input_fd = sys.stdin.fileno()
    output_fd = sys.stdout.fileno()
    old_terminal = None
    if os.isatty(input_fd):
        old_terminal = termios.tcgetattr(input_fd)
        tty.setraw(input_fd)

    input_open = True
    master_open = True
    child_status = None
    try:
        while master_open:
            readers = [master_fd]
            if input_open:
                readers.append(input_fd)
            try:
                ready, _, _ = select.select(readers, [], [], 0.2)
            except InterruptedError:
                continue

            if master_fd in ready:
                try:
                    output = os.read(master_fd, 4096)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    output = b""
                if output:
                    write_all(output_fd, output)
                else:
                    master_open = False

            if input_open and input_fd in ready:
                input_data = os.read(input_fd, 4096)
                if input_data:
                    write_all(master_fd, input_data)
                else:
                    input_open = False

            if child_status is None:
                waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
                if waited_pid:
                    child_status = status
    finally:
        if old_terminal is not None:
            termios.tcsetattr(input_fd, termios.TCSADRAIN, old_terminal)
        os.close(master_fd)

    if child_status is None:
        _, child_status = os.waitpid(child_pid, 0)
    signal.alarm(0)
    child_pid = None
    return os.waitstatus_to_exitcode(child_status)


if __name__ == "__main__":
    raise SystemExit(main())
