"""End-to-end demo against a running server.

    uvicorn app.main:app            # in one terminal
    python scripts/demo.py          # in another

Submits sample_request.json, polls progress, prints per-recipient results and
downloads every generated certificate as a ZIP into ./demo_output/.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--file", default=ROOT / "sample_request.json", type=Path, help="request JSON")
    parser.add_argument("--api-key", default=None, help="value for X-API-Key, if the server requires one")
    parser.add_argument("--out", default=ROOT / "demo_output", type=Path, help="where to save the ZIP")
    args = parser.parse_args()

    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    client = httpx.Client(base_url=args.url, headers=headers, timeout=30)

    payload = json.loads(args.file.read_text(encoding="utf-8"))
    response = client.post("/api/v1/jobs", json=payload)
    if response.status_code != 202:
        print(f"Request rejected ({response.status_code}): {response.text}")
        return 1

    created = response.json()
    print(f"Job {created['job_id']}: {created['accepted']} accepted, {created['rejected']} rejected")
    for row in created["validation_errors"]:
        problems = "; ".join(f"{e['field']}: {e['message']}" for e in row["errors"])
        print(f"  row {row['index']}: {problems}")

    while True:
        job = client.get(created["status_url"]).json()
        p = job["progress"]
        print(f"  {job['status']:<22} {p['processed']}/{p['total']} ({p['percent_complete']}%)")
        if job["status"] not in ("pending", "processing"):
            break
        time.sleep(0.5)

    print("\nResults:")
    listing = client.get(created["certificates_url"], params={"limit": 1000}).json()
    for cert in listing["items"]:
        detail = cert["verification_code"] if cert["status"] == "generated" else cert["error_message"]
        print(f"  [{cert['index']}] {cert['status']:<9} {cert['recipient_name'] or '-':<28} {detail}")

    if job["download_url"]:
        args.out.mkdir(parents=True, exist_ok=True)
        zip_path = args.out / f"certificates_{created['job_id']}.zip"
        zip_path.write_bytes(client.get(job["download_url"]).content)
        print(f"\nSaved {p['generated']} certificate(s) to {zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
