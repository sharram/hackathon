import os
import requests
import zipfile
import io
from pathlib import Path
import subprocess
import re

# =========================
# GitHub Tool (READ-ONLY)
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
# Dependency Impact Analyzer
# =========================
class DependencyImpactAnalyzer:
    """
    Lightweight dependency-graph awareness.
    DOES NOT modify files.
    """

    COMMON_TRANSITIVE_LIBS = {
        "numpy", "pandas", "six", "setuptools", "wheel"
    }

    def analyze(self, dependency: str) -> dict:
        risk = "LOW"
        note = "Appears to be a direct dependency."

        if dependency in self.COMMON_TRANSITIVE_LIBS:
            risk = "MEDIUM"
            note = (
                "This package is commonly transitive and may already exist "
                "via another dependency. Adding it explicitly may affect "
                "the dependency graph."
            )

        return {
            "risk": risk,
            "note": note
        }


# =========================
# Agent Reasoning Core
# =========================
class CIFixAgent:
    def __init__(self):
        self.github = GitHubTool()
        self.dep_graph = DependencyImpactAnalyzer()

    def diagnose(self, logs: str):
        """
        Decide WHAT is wrong.
        (Pure reasoning, no side effects)
        """
        if "ModuleNotFoundError" in logs:
            missing = (
                logs.split("No module named")[-1]
                .strip()
                .strip("'\"")
                .split()[0]
            )
            return {
                "type": "missing_dependency",
                "dependency": missing
            }

        if "No such file or directory" in logs:
            return {
                "type": "broken_path"
            }

        return {"type": "unknown"}

    def act(self, diagnosis, logs: str):
        """
        Decide HOW to respond.
        NO AUTO-FIXES.
        HUMAN APPROVAL REQUIRED.
        """

        if diagnosis["type"] == "missing_dependency":
            dep = diagnosis["dependency"]
            impact = self.dep_graph.analyze(dep)

            comment = f"""
[CI-HYGIENE-AGENT] Diagnosis Report

❌ CI Failure Type
Missing Dependency

🔍 Root Cause
Module `{dep}` is imported in the code but not declared in `requirements.txt`.
This often passes locally due to cached or transitive dependencies,
but fails in clean CI environments.

🧩 Dependency Graph Impact
Risk Level: {impact["risk"]}
Note: {impact["note"]}

🛠 Suggested Fix (NOT APPLIED)
Add `{dep}` to `requirements.txt`

⚠️ HUMAN APPROVAL REQUIRED
No files were modified automatically.
Please review dependency implications before applying this fix.
"""

            self.github.post_pr_comment(comment)
            return

        if diagnosis["type"] == "broken_path":
            comment = """
[CI-HYGIENE-AGENT] Diagnosis Report

❌ CI Failure Type
Broken File Path

🔍 Root Cause
A file path exists locally but not in the CI environment.
This commonly occurs due to relative paths or ignored files.

🛠 Suggested Fix (NOT APPLIED)
Verify repository structure and file paths.

⚠️ HUMAN APPROVAL REQUIRED
"""

            self.github.post_pr_comment(comment)
            return

        # Fallback
        self.github.post_pr_comment(
            "[CI-HYGIENE-AGENT] CI failure could not be classified automatically. Manual inspection required."
        )

    def run(self):
        logs = self.github.get_ci_logs()
        diagnosis = self.diagnose(logs)
        self.act(diagnosis, logs)


# =========================
# Entry Point
# =========================
if __name__ == "__main__":
    agent = CIFixAgent()
    agent.run()
