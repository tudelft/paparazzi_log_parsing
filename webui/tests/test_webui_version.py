import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from .test_historical import AppTests


class WebuiVersionTests(AppTests):
    def test_me_reports_startup_snapshot_without_running_git_per_request(self):
        version = dict(commit="a" * 40, modified=True, tag="webui-v0.1.0", commits_since_tag=2)
        with patch.dict(self.ns, WEBUI_VERSION=version), patch("subprocess.run") as run:
            for _ in range(2):
                response = self.client.get("/api/me")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["webui_version"], version)
                self.assertEqual(response.json["user"]["username"], "pic")
            run.assert_not_called()
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_detects_clean_and_modified_checkout(self):
        for modified in (False, True):
            with self.subTest(modified=modified), patch("subprocess.run", side_effect=[
                subprocess.CompletedProcess([], 0, stdout="b" * 40 + "\n"),
                subprocess.CompletedProcess([], int(modified)),
                subprocess.CompletedProcess([], 0, stdout="bbbbbbb\n"),
            ]) as run:
                self.assertEqual(self.ns["detect_webui_version"](),
                                 dict(commit="b" * 40, modified=modified, tag=None, commits_since_tag=None))
                self.assertEqual(run.call_count, 3)
                self.assertEqual(run.call_args.kwargs["cwd"], self.ns["HERE"].parent)
                for call in run.call_args_list:
                    self.assertEqual(call.args[0][:3],
                                     ["git", "-c", f"safe.directory={self.ns['HERE'].parent}"])

    def test_detection_failures_are_explicitly_unknown_and_logged(self):
        for error in (FileNotFoundError("git"), subprocess.TimeoutExpired("git", 5),
                      subprocess.CalledProcessError(128, "git")):
            with self.subTest(error=type(error).__name__), patch("subprocess.run", side_effect=error):
                with self.assertLogs(self.app.logger, level="WARNING"):
                    self.assertEqual(self.ns["detect_webui_version"](),
                                     dict(commit=None, modified=None, tag=None, commits_since_tag=None))

    def test_missing_git_metadata_does_not_use_a_parent_repository(self):
        with patch("pathlib.Path.exists", return_value=False), patch("subprocess.run") as run:
            with self.assertLogs(self.app.logger, level="WARNING"):
                self.assertEqual(self.ns["detect_webui_version"](),
                                 dict(commit=None, modified=None, tag=None, commits_since_tag=None))
            run.assert_not_called()

    def test_invalid_revision_and_failed_change_check_are_logged(self):
        for results in (
            [subprocess.CompletedProcess([], 0, stdout="not-a-commit")],
            [subprocess.CompletedProcess([], 0, stdout="c" * 40),
             subprocess.CompletedProcess([], 128)],
        ):
            with self.subTest(results=results), patch("subprocess.run", side_effect=results):
                with self.assertLogs(self.app.logger, level="WARNING"):
                    self.assertEqual(self.ns["detect_webui_version"](),
                                     dict(commit=None, modified=None, tag=None, commits_since_tag=None))

    def test_release_tag_and_commits_since_release(self):
        for ahead in (0, 3):
            with self.subTest(ahead=ahead), patch("subprocess.run", side_effect=[
                subprocess.CompletedProcess([], 0, stdout="d" * 40),
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, stdout=f"webui-v0.1.0-{ahead}-gddddddd\n"),
            ]) as run:
                self.assertEqual(self.ns["detect_webui_version"](), dict(
                    commit="d" * 40, modified=False, tag="webui-v0.1.0", commits_since_tag=ahead))
                self.assertIn("webui-v[0-9]*", run.call_args.args[0])
                self.assertEqual(run.call_args.args[0][-1], "d" * 40)

    def test_root_owned_deployment_detection_with_real_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            webui = repo / "webui"
            webui.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid", "commit", "-q",
                            "--allow-empty", "-m", "fixture"], check=True)
            subprocess.run(["git", "-C", str(repo), "tag", "webui-v0.1.0"], check=True)
            # Git's test switch exercises its real ownership check without changing file ownership.
            with patch.dict(os.environ, GIT_TEST_ASSUME_DIFFERENT_OWNER="1",
                            GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory", GIT_CONFIG_VALUE_0=""):
                rejected = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                          capture_output=True, text=True)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("dubious ownership", rejected.stderr)
                with patch.dict(self.ns, HERE=webui):
                    version = self.ns["detect_webui_version"]()
                self.assertIsNotNone(version["commit"])
                self.assertEqual(version["tag"], "webui-v0.1.0")
                self.assertEqual(version["commits_since_tag"], 0)
                self.assertFalse(version["modified"])
