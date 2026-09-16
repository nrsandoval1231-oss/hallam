"""Capture real ATS payloads as committed fixtures.

Run once during provider bring-up. Fixtures are committed so provider tests are
reproducible offline and against real shapes rather than invented ones.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
UA = {"User-Agent": "SabraJobEngine/0.1 (+candidate-owned job search)"}


def get_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get_text(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def write_json(path: Path, payload) -> int:
    """Write valid JSON. A byte slice of a JSON document is not JSON: truncating
    it produced an unparseable fixture, so bounding happens on the *structure*
    (how many postings) and never on the serialized bytes."""

    text = json.dumps(payload, indent=1)
    path.write_text(text, encoding="utf-8")
    return len(text)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # Greenhouse: board list with content, plus one job detail (metadata/questions).
    gh_board = get_json("https://boards-api.greenhouse.io/v1/boards/revance/jobs?content=true")
    jobs = gh_board.get("jobs", [])
    size = write_json(
        OUT / "greenhouse_revance_board.json", {"jobs": jobs[:15], "meta": {"total": 36}}
    )
    print(f"greenhouse board jobs: {len(jobs)} captured 15 ({size} bytes)")
    if jobs:
        first = jobs[0]
        print("greenhouse job keys:", sorted(first.keys()))
        print("greenhouse sample id:", first.get("id"), "|", first.get("title"))
        print("greenhouse location:", json.dumps(first.get("location"))[:200])
        detail = get_json(
            f"https://boards-api.greenhouse.io/v1/boards/revance/jobs/{first['id']}?questions=true"
        )
        write_json(OUT / "greenhouse_revance_job_detail.json", detail)
        print("greenhouse detail keys:", sorted(detail.keys()))
        print(
            "greenhouse questions:",
            [q.get("label") for q in (detail.get("questions") or [])][:12],
        )

    # Lever: postings list.
    lever = get_json("https://api.lever.co/v0/postings/elfbeauty?mode=json")
    size = write_json(OUT / "lever_elfbeauty_board.json", lever[:15])
    print(f"lever postings: {len(lever)} captured 15 ({size} bytes)")
    if lever:
        print("lever posting keys:", sorted(lever[0].keys()))
        print("lever sample:", lever[0].get("id"), "|", lever[0].get("text"))
        print("lever categories:", json.dumps(lever[0].get("categories")))
        print("lever workplaceType:", lever[0].get("workplaceType"))
        print("lever salaryRange:", json.dumps(lever[0].get("salaryRange")))
        print("lever createdAt:", lever[0].get("createdAt"))
        print("lever lists:", json.dumps([x.get("text") for x in (lever[0].get("lists") or [])]))


if __name__ == "__main__":
    main()
