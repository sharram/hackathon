from pathlib import Path

def read_ci_logs():
    # In real version: GitHub MCP Server
    # Demo version: deterministic input
    return "ModuleNotFoundError: No module named 'requests'"

def fix_missing_dependency(dep):
    req = Path("requirements.txt")
    content = req.read_text()

    if dep not in content:
        req.write_text(content + f"\n{dep}\n")

def main():
    logs = read_ci_logs()

    if "ModuleNotFoundError" in logs:
        missing = logs.split("named '")[1].split("'")[0]
        fix_missing_dependency(missing)

        print("🤖 CI Janitor Report")
        print(f"- Error: missing dependency `{missing}`")
        print("- Fix: added to requirements.txt")
        print("- Action: awaiting human approval")
    else:
        print("No fixable CI hygiene issue detected")

if __name__ == "__main__":
    main()
