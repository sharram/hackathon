import os
import requests
import zipfile
import io
from pathlib import Path
import re


import subprocess

def run_git(cmd):
    subprocess.run(cmd, check=True)


# =========================
# GitHub MCP Tool
# =========================
class GitHubTool:
    def __init__(self):
        self.token = os.environ["GITHUB_TOKEN"]
        self.repo = os.environ["REPO"]
        self.run_id = os.environ["RUN_ID"]
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json"
        }

    def get_ci_logs(self) -> str:
        """
        Fetch logs for the failed CI workflow run.
        (Maps to GitHub MCP Server: read CI logs)
        """
        url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}/logs"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()

        zip_file = zipfile.ZipFile(io.BytesIO(response.content))
        logs = ""
        for name in zip_file.namelist():
            logs += zip_file.read(name).decode("utf-8", errors="ignore")

        return logs

    def post_pr_comment(self, body: str):
        """
        Post a comment on the associated PR.
        (Maps to GitHub MCP Server: PR comments)
        """
        run_url = f"https://api.github.com/repos/{self.repo}/actions/runs/{self.run_id}"
        run = requests.get(run_url, headers=self.headers).json()

        if not run.get("pull_requests"):
            print("No PR associated with this workflow run.")
            return

        pr_number = run["pull_requests"][0]["number"]
        comment_url = f"https://api.github.com/repos/{self.repo}/issues/{pr_number}/comments"

        requests.post(
            comment_url,
            headers=self.headers,
            json={"body": body}
        )


# =========================
# Filesystem MCP Tool
# =========================
class FilesystemTool:
    def add_dependency(self, dependency: str):
        """
        Apply a minimal patch to requirements.txt
        (Maps to Filesystem MCP Server: apply patch)
        """
        req = Path("requirements.txt")
        content = req.read_text()

        if dependency not in content:
            req.write_text(content + f"\n{dependency}\n")

def commit_and_push_fix(dep: str):
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

    branch = os.environ.get("PR_BRANCH")
    if not branch:
        raise RuntimeError("PR_BRANCH not set; cannot push fix.")


    run_git(["git", "push", "origin", f"HEAD:{branch}"])




# =========================
# Agent Reasoning Core
# =========================
class CIFixAgent:
    def __init__(self):
        self.github = GitHubTool()
        self.fs = FilesystemTool()

    def diagnose(self, logs: str):
        """
        Decide WHAT is wrong.
        (Pure reasoning, no side effects)
        """
        match = re.search(r"No module named ['\"]([^'\"]+)['\"]", logs)
        if match:
            return {
                "type": "missing_dependency",
                "dependency": match.group(1)
            }

        return {"type": "unknown"}


    def act(self, diagnosis):
        """
        Decide HOW to fix it.
        (Calls MCP tools)
        """
        if diagnosis["type"] == "missing_dependency":
            dep = diagnosis["dependency"]
            self.fs.add_dependency(dep)
            commit_and_push_fix(dep)

            comment = f"""
            🤖 **CI Janitor Report**

            **Error Detected**
            - Missing Python dependency: `{dep}`

            **Root Cause**
            - Dependency not listed in `requirements.txt`

            **Action Taken**
            - Added `{dep}` to `requirements.txt`
            - Committed fix to PR branch

            **Result**
            - CI automatically re-triggered
            """
            self.github.post_pr_comment(comment)

            print(f"✔ Fixed and committed missing dependency: {dep}")

    def run(self):
        logs = self.github.get_ci_logs()
        diagnosis = self.diagnose(logs)
        self.act(diagnosis)


# =========================
# Entry Point
# =========================
if __name__ == "__main__":
    agent = CIFixAgent()
    agent.run()
