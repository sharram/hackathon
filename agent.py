import os
import io
import re
import zipfile
import subprocess
from pathlib import Path
from typing import Optional

import requests


# =========================
# Helpers
# =========================
def run_git(cmd):
    subprocess.run(cmd, check=True)


def commit_and_push_fix(dep: str, branch: str):
    run_git(["git", "config", "user.name", "ci-janitor-bot"])
    run_git(["git", "config", "user.email", "ci-janitor@users.noreply.github.com"])

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True
    ).stdout.strip()

    if not status:
        print("No changes detected, skipping commit.")
        return

    run_git(["git", "add", "requirements.txt"])
    run_git(["git", "commit", "-m", f"ci-fix: add missing dependency {dep}"])
    run_git(["git", "push", "origin", f"HEAD:{branch}"])


def find_missing_dependency(logs: str) -> Optional[str]:
    # ModuleNotFoundError: No module named 'requests'
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", logs)
    if not m:
        return None
    return m.group(1).strip()


def make_log_excerpt(logs: str, max_lines: int = 30, max_chars: int = 1800) -> str:
    lines = logs.splitlines()
    idx = next((i for i, l in enumerate(lines) if "ModuleNotFoundError" in l), None)

    if idx is None:
        snippet = lines[:max_lines]
    else:
        start = max(0, idx - 10)
        end = min(len(lines), idx + 10)
        snippet = lines[start:end]

    text = "\n".join(snippet).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text


# =========================
# GitHub Tool
# =========================
class GitHubTool:
    def __init__(self):
        self.token = os.environ["GITHUB_TOKEN"]
        self.repo = os.environ["REPO"]  # e.g. owner/repo
        self.run_id = os.environ.get("RUN_ID")  # present for workflow_run, not for issue_comment
        self.pr_number = os.environ.get("PR_NUMBER")  # can be provided for issue_comment apply job
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def _get_json(self, url: str) -> dict:
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def _post_json(self, url: str, payload: dict):
        r = requests.post(url, headers=self.headers, json=payload)
        r.raise_for_status()
        return r.json()

    def get_ci_logs(self) -> str:
        """
        Fetch logs for a workflow run.
        - If RUN_ID is set: use it.
        - Else if PR_NUMBER is set: find latest failed "CI" run for that PR head SHA.
        """
        run_id = self.run_id

        if not run_id:
            if not self.pr_number:
                raise RuntimeError("Neither RUN_ID nor PR_NUMBER set; cannot fetch CI logs.")

            # Get PR details -> head SHA
            pr_url = f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}"
            pr = self._get_json(pr_url)
            head_sha = pr["head"]["sha"]

            # Find workflow runs for that SHA; pick the latest failed CI run
            runs_url = f"https://api.github.com/repos/{self.repo}/actions/runs?per_page=50"
            runs = self._get_json(runs_url).get("workflow_runs", [])

            chosen = None
            for r in runs:
                if r.get("head_sha") != head_sha:
                    continue
                # Match your CI workflow name if possible
                name = (r.get("name") or "").lower()
                if "ci" not in name:
                    continue
                if r.get("conclusion") == "failure":
                    chosen = r
                    break

            if not chosen:
                # fallback: take most recent run with same sha even if not named "CI"
                for r in runs:
                    if r.get("head_sha") == head_sha and r.get("conclusion") == "failure":
                        chosen = r
                        break

            if not chosen:
                raise RuntimeError("Could not find a failed CI run for this PR to fetch logs from.")

            run_id = str(chosen["id"])
            self.run_id = run_id  # cache it for commenting

        url = f"https://api.github.com/repos/{self.repo}/actions/runs/{run_id}/logs"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()

        zip_file = zipfile.ZipFile(io.BytesIO(response.content))
        logs = ""
        for name in zip_file.namelist():
            logs += zip_file.read(name).decode("utf-8", errors="ignore")

        return logs

    def get_pr_number(self) -> int:
        """
        Get PR number.
        - If PR_NUMBER env is set, use it.
        - Else use RUN_ID to find associated PR.
        """
        if self.pr_number:
            return int(self.pr_number)

        if not self.run_id:
            raise RuntimeError("No PR_NUMBER or RUN_ID set; cannot determine PR number.")

        run_url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}"
        run = self._get_json(run_url)

        if not run.get("pull_requests"):
            raise RuntimeError("No PR associated with this workflow run.")

        return int(run["pull_requests"][0]["number"])

    def post_pr_comment(self, body: str):
        pr_number = self.get_pr_number()
        comment_url = f"https://api.github.com/repos/{self.repo}/issues/{pr_number}/comments"
        self._post_json(comment_url, {"body": body})


# =========================
# Filesystem Tool
# =========================
class FilesystemTool:
    def add_dependency(self, dependency: str):
        req = Path("requirements.txt")
        content = req.read_text()

        dep = dependency.strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]

        # already present?
        if dep in lines:
            return

        # Ensure newline, add exactly one clean line
        if not content.endswith("\n"):
            content += "\n"
        req.write_text(content + dep + "\n")


# =========================
# Agent Core
# =========================
class CIFixAgent:
    def __init__(self):
        self.github = GitHubTool()
        self.fs = FilesystemTool()

    def run(self):
        logs = self.github.get_ci_logs()
        dep = find_missing_dependency(logs)

        if not dep:
            self.github.post_pr_comment("🤖 CI Janitor: couldn't find a missing dependency pattern in the logs.")
            return

        approved = os.environ.get("CI_JANITOR_APPROVED", "0") == "1"

        if not approved:
            # Keep comment SMALL; optional collapsed excerpt
            excerpt = make_log_excerpt(logs)
            comment = (
                f"🤖 **CI Janitor**\n\n"
                f"Found missing dependency `{dep}`.\n\n"
                f"**Proposed change:** add `{dep}` to `requirements.txt`.\n\n"
                f"Reply with `/ci-janitor approve` to apply the fix.\n\n"
                f"<details><summary>Log excerpt</summary>\n\n"
                f"```text\n{excerpt}\n```\n"
                f"</details>"
            )
            self.github.post_pr_comment(comment)
            return

        # Approved: actually apply and push
        self.fs.add_dependency(dep)

        branch = os.environ.get("PR_BRANCH")
        if not branch:
            # For issue_comment path, checkout is already on the PR head branch,
            # and push will work if we just push HEAD to the current branch name.
            # But we still need the branch name. Try GITHUB_REF_NAME.
            branch = os.environ.get("GITHUB_REF_NAME") or os.environ.get("GITHUB_HEAD_REF")

        if not branch:
            raise RuntimeError("PR_BRANCH not set and could not infer branch name for push.")

        commit_and_push_fix(dep, branch)

        self.github.post_pr_comment(f"🤖 CI Janitor: added `{dep}` to `requirements.txt` and pushed a fix.")
        print(f"✔ Fixed and committed missing dependency: {dep}")


# =========================
# Entry Point
# =========================
if __name__ == "__main__":
    CIFixAgent().run()
