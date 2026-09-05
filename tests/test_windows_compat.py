"""Portable regressions, plus native Windows checks without model inference."""

import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from codex_reviewer.catalog import CodexBinary
from codex_reviewer.cli import _write_result
from codex_reviewer.runner import CodexProcessRunner


def run_python(source, *, prompt=None, **options):
    binary = CodexBinary(requested=sys.executable, path=sys.executable)
    runner = CodexProcessRunner(binary, timeout=options.pop("timeout", 5), **options)
    return runner.run(
        [sys.executable, "-X", "utf8", "-u", "-c", source],
        stdin_payload=prompt, mode="generic", scope={}, model="fake", effort="high", service_tier=None,
    )


class PortableIOTests(unittest.TestCase):
    def test_utf8_stdin_and_jsonl_ignore_locale_encoding(self):
        source = '''import json,sys
text=sys.stdin.read()
assert text == "中文提示 🧪"
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"中文結果 🧪"}},ensure_ascii=False))
print(json.dumps({"type":"turn.completed"}))
'''
        # TextIOWrapper's implicit encoding is locale-specific on older Windows Python.
        with patch("locale.getencoding", return_value="cp1252", create=True), patch("locale.getpreferredencoding", return_value="cp1252"):
            result = run_python(source, prompt="中文提示 🧪")
        self.assertTrue(result["success"], result.get("error"))
        self.assertEqual(result["final_result"], "中文結果 🧪")

    def test_invalid_utf8_cannot_be_accepted_as_a_completed_review(self):
        result = run_python("import sys; sys.stdout.buffer.write(b'\\xff\\n')")
        self.assertFalse(result["success"])
        self.assertIn("UTF-8", result["error"])

    def test_outputs_work_without_unix_fchmod(self):
        with tempfile.TemporaryDirectory() as tmp:
            final = Path(tmp) / "中文結果.txt"
            envelope = Path(tmp) / "結果.json"
            original = getattr(os, "fchmod", None)
            try:
                if original is not None:
                    del os.fchmod
                CodexProcessRunner._write_private(final, "中文結果 🧪")
                self.assertIsNone(_write_result(str(envelope), {"success": False}))
            finally:
                if original is not None:
                    os.fchmod = original
            self.assertEqual(final.read_text(encoding="utf-8"), "中文結果 🧪")
            self.assertFalse(json.loads(envelope.read_text(encoding="utf-8"))["success"])

    def test_launcher_requires_handshake_and_preserves_complete_prompt(self):
        from codex_reviewer.windows_job import launcher_command
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "started"
            command = launcher_command([sys.executable, "-X", "utf8", "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).touch(); sys.stdout.write(sys.stdin.read())", str(marker)])
            denied = subprocess.run(command, input="", capture_output=True, text=True, encoding="utf-8", timeout=5)
            self.assertNotEqual(denied.returncode, 0)
            self.assertFalse(marker.exists())
            prompt = "中文 🧪\n" * 10000
            accepted = subprocess.run(command, input="\0" + prompt, capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(accepted.stdout, prompt)


class ExecutionLockTests(unittest.TestCase):
    def test_lock_excludes_another_process_and_can_be_reacquired(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = "root:" + tmp
            descriptor, error = CodexProcessRunner._acquire_execution_lock(key)
            self.assertIsNone(error)
            source = '''import sys
sys.path.insert(0,sys.argv[1])
from codex_reviewer.runner import CodexProcessRunner
fd,error=CodexProcessRunner._acquire_execution_lock(sys.argv[2])
if fd is not None: CodexProcessRunner._release_execution_lock(fd)
sys.exit(2 if error else 0)
'''
            other_key = key.upper() if os.name == "nt" else key
            command = [sys.executable, "-c", source, str(Path(__file__).resolve().parents[1] / "scripts"), other_key]
            try:
                blocked = subprocess.run(command, capture_output=True, timeout=5)
                self.assertEqual(blocked.returncode, 2, blocked.stderr)
            finally:
                CodexProcessRunner._release_execution_lock(descriptor)
            acquired = subprocess.run(command, capture_output=True, timeout=5)
            self.assertEqual(acquired.returncode, 0, acquired.stderr)

    def test_missing_lock_backend_fails_closed(self):
        with patch("codex_reviewer.runner.fcntl", None), patch("codex_reviewer.runner.msvcrt", None):
            with self.assertRaisesRegex(RuntimeError, "execution lock"):
                CodexProcessRunner._acquire_execution_lock("root:missing-backend")


@unittest.skipUnless(os.name == "nt", "Requires native Windows Job Objects")
class WindowsJobTests(unittest.TestCase):
    def test_assignment_failure_never_starts_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "started"
            with patch("codex_reviewer.windows_job.WindowsJob.assign", side_effect=OSError("assignment denied")):
                result = run_python(f"from pathlib import Path; Path({str(marker)!r}).touch()")
            self.assertFalse(result["success"])
            self.assertFalse(marker.exists())

    def test_cancellation_and_parent_death_stop_the_job_and_release_lock(self):
        from codex_reviewer.windows_job import WindowsJob
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as tmp:
                pidfile = Path(tmp) / "child.pid"
                resultfile = Path(tmp) / "result.json"
                key = "root:" + tmp
                target = f"import os,signal,time,pathlib; signal.signal(signal.SIGBREAK,signal.SIG_IGN); pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)"
                wrapper = '''import json,sys,pathlib
from tests.test_windows_compat import CodexBinary,CodexProcessRunner
binary=CodexBinary(requested=sys.executable,path=sys.executable)
result=CodexProcessRunner(binary,timeout=30).run([sys.executable,"-u","-c",sys.argv[1]],stdin_payload=None,mode="generic",scope={},model="fake",effort="high",service_tier=None,lock_key=sys.argv[3])
pathlib.Path(sys.argv[2]).write_text(json.dumps(result),encoding="utf-8")
sys.exit(result.get("exit_code") or 0)
'''
                process = subprocess.Popen(
                    [sys.executable, "-X", "utf8", "-u", "-c", wrapper, target, str(resultfile), key],
                    cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                # Also retain the child in a test-owned job for cleanup on assertion failure.
                cleanup = WindowsJob()
                try:
                    deadline = time.monotonic() + 10
                    while (not pidfile.exists() or not pidfile.stat().st_size) and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.05)
                    self.assertTrue(pidfile.exists(), "Reviewer child failed to start")
                    cleanup.assign(int(pidfile.read_text()))
                    if cancel:
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        process.kill()
                    process.communicate(timeout=15)
                    if cancel:
                        self.assertEqual(process.returncode, 130)
                        result = json.loads(resultfile.read_text(encoding="utf-8"))
                        self.assertEqual(result["execution_status"], "interrupted")
                        self.assertFalse(result["success"])
                    deadline = time.monotonic() + 5
                    while cleanup.active_processes() and time.monotonic() < deadline:
                        time.sleep(.05)
                    self.assertEqual(cleanup.active_processes(), 0)
                    descriptor, error = CodexProcessRunner._acquire_execution_lock(key)
                    try:
                        self.assertIsNone(error)
                    finally:
                        CodexProcessRunner._release_execution_lock(descriptor)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
                    cleanup.close()

    def test_descendants_stop_after_timeout_and_success(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        for successful in (False, True):
            with self.subTest(successful=successful), tempfile.TemporaryDirectory() as tmp:
                pidfile = Path(tmp) / "child.pid"
                child = f"import os,signal,time,pathlib; signal.signal(signal.SIGBREAK,signal.SIG_IGN); pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)"
                source = f'''import subprocess,sys,time,pathlib,json
child=subprocess.Popen([sys.executable,"-c",{child!r}],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
while not pathlib.Path({str(pidfile)!r}).exists() or not pathlib.Path({str(pidfile)!r}).stat().st_size: time.sleep(.02)
if {successful!r}:
 print(json.dumps({{"type":"item.completed","item":{{"type":"agent_message","text":"done"}}}}))
 print(json.dumps({{"type":"turn.completed"}}))
else: time.sleep(60)
'''
                result = run_python(source, timeout=3)
                self.assertEqual(result["success"], successful, result.get("error"))
                self.assertTrue(pidfile.exists(), "Child never started")
                pid = int(pidfile.read_text())
                handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                if handle:
                    try:
                        self.assertEqual(api.WaitForSingleObject(handle, 5000), 0, "Descendant still running")
                    finally:
                        api.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
