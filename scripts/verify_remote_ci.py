"""Read final-SHA push workflows, every paginated job/check and branch checks.

Uses the existing Git credential helper in memory; never writes/prints credentials.
Exit 0 means all observed required evidence passed; exit 2 means pending or failed.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sha")
    parser.add_argument("--repo", default="hengshu-credit/appstore_applications_monitoring")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--failed-logs", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        parser.error("Provide the complete final SHA")
    credential = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                                text=True, capture_output=True, check=True,
                                env=os.environ | {"GIT_TERMINAL_PROMPT": "0"})
    values = dict(line.split("=", 1) for line in credential.stdout.splitlines() if "=" in line)
    session = requests.Session()
    session.headers.update(Authorization="Bearer " + values["password"], Accept="application/vnd.github+json",
                           **{"X-GitHub-Api-Version": "2022-11-28"})
    base = "https://api.github.com/repos/" + args.repo

    def get(path, params=None):
        response = session.get(base + path, params=params, timeout=45)
        if response.status_code != 200:
            raise RuntimeError(f"GitHub HTTP {response.status_code} for {path}")
        return response.json()

    def pages(path, key=None, params=None):
        rows, page = [], 1
        while True:
            data = get(path, (params or {}) | {"per_page": 100, "page": page})
            batch = data[key] if key else data
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            page += 1

    runs = pages("/actions/runs", "workflow_runs", {"head_sha": args.sha})
    checks = pages(f"/commits/{args.sha}/check-runs", "check_runs", {"filter": "latest"})
    statuses = get(f"/commits/{args.sha}/status")
    branch = get("/branches/" + args.branch)
    required = []
    if branch["protected"]:
        rules = get("/rules/branches/" + args.branch)
        for rule in rules:
            if rule["type"] == "required_status_checks":
                required.extend(x["context"] for x in rule["parameters"]["required_status_checks"])
        protection = session.get(base + "/branches/" + args.branch + "/protection/required_status_checks", timeout=45)
        if protection.status_code == 200:
            required.extend(protection.json().get("contexts", []))
        elif protection.status_code != 404:
            raise RuntimeError("Unable to read required branch checks")
    jobs = []
    for run in runs:
        jobs.extend(pages(f"/actions/runs/{run['id']}/jobs", "jobs", {"filter": "latest"}))
    passed_names = {c["name"] for c in checks if c["conclusion"] == "success"}
    passed_names.update(s["context"] for s in statuses["statuses"] if s["state"] == "success")
    expected_ci = any(r["name"] == "CI" for r in runs) and len(jobs) >= 7
    passed = bool(runs and checks and expected_ci and all(r["status"] == "completed" and r["conclusion"] == "success" for r in runs)
                  and all(j["status"] == "completed" and j["conclusion"] == "success" for j in jobs)
                  and all(c["conclusion"] == "success" for c in checks)
                  and all(s["state"] == "success" for s in statuses["statuses"])
                  and set(required) <= passed_names)
    result = {"sha": args.sha, "passed": passed, "branch_protected": branch["protected"], "required_checks": sorted(set(required)),
              "runs": [{k: r[k] for k in ("id", "name", "event", "status", "conclusion", "html_url")} for r in runs],
              "jobs": [{k: j[k] for k in ("id", "name", "status", "conclusion", "html_url")} for j in jobs],
              "checks": [{k: c[k] for k in ("name", "status", "conclusion")} for c in checks],
              "status_contexts": [{k: s[k] for k in ("context", "state", "target_url")} for s in statuses["statuses"]]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.failed_logs:
        for job in jobs:
            if job["conclusion"] == "failure":
                response = session.get(base + f"/actions/jobs/{job['id']}/logs", timeout=45)
                if response.status_code == 200:
                    print(f"Failed job: {job['name']}\n" + response.text[-18000:])
    return 0 if passed else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (requests.RequestException, RuntimeError, subprocess.CalledProcessError, KeyError) as exc:
        print(f"CI verification unavailable: {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
