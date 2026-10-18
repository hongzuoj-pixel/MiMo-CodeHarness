"""Collect GitHub's exact automated job outcomes; never sign off manual gates."""
import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from mimo_harness.product_acceptance import REQUIRED_JOBS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    repo, run = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"]
    jobs = []
    for attempt in range(5):
        jobs = []
        page = 1
        while True:
            request = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/runs/{run}/jobs?per_page=100&page={page}",
                headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"], "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(request, timeout=20) as response:
                batch = json.load(response)["jobs"]
            jobs.extend({"name": j["name"], "conclusion": j["conclusion"], "id": j["id"]} for j in batch if j["name"] != "release-automation")
            if len(batch) < 100: break
            page += 1
        if {j["name"] for j in jobs} == REQUIRED_JOBS and all(j["conclusion"] == "success" for j in jobs): break
        if attempt < 4: time.sleep(2)
    assert {j["name"] for j in jobs} == REQUIRED_JOBS and all(j["conclusion"] == "success" for j in jobs), jobs
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip()
    payload = {"schema_version": 1, "repository": repo, "commit": commit, "tree": tree, "jobs": jobs,
               "run_url": f"https://github.com/{repo}/actions/runs/{run}",
               "status": "AUTOMATED_CHECKS_PASSED_MANUAL_ACCEPTANCE_PENDING"}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Automated release record: {output}; {len(jobs)} successful jobs; manual gates remain pending.")


if __name__ == "__main__": main()
