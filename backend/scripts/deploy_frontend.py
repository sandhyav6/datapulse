"""Build the React app and publish it to AWS Amplify Hosting.

  python scripts/deploy_frontend.py               # npm run build, zip, deploy, wait
  python scripts/deploy_frontend.py --skip-build  # deploy an existing frontend/dist

Amplify "manual deploy": zip frontend/dist, ask Amplify for an upload URL, upload the zip, start
the deployment and wait until the job SUCCEEDs. Caching and security headers come from the
Amplify app's customHeaders in template.yaml (hashed assets cached for a year, index.html never).
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stack import add_common_args, session, stack_outputs  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
DIST = FRONTEND / "dist"
BRANCH = "main"


def build() -> None:
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    if not (FRONTEND / "node_modules").is_dir():
        subprocess.run([npm, "ci"], cwd=FRONTEND, check=True)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True)


def zip_dist(dist: Path) -> bytes:
    """Zip the build output with index.html at the archive root, as Amplify expects."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in dist.rglob("*") if p.is_file()):
            archive.write(path, path.relative_to(dist).as_posix())
    return buffer.getvalue()


def upload(url: str, payload: bytes) -> None:
    request = urllib.request.Request(url, data=payload, method="PUT",
                                     headers={"Content-Type": "application/zip"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - AWS presigned
        if response.status not in (200, 201):
            raise RuntimeError(f"upload failed: HTTP {response.status}")


def wait_for_job(amplify, app_id: str, job_id: str, timeout: float = 600) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = amplify.get_job(appId=app_id, branchName=BRANCH,
                                 jobId=job_id)["job"]["summary"]["status"]
        if status in ("SUCCEED", "FAILED", "CANCELLED"):
            return status
        time.sleep(3)
    return "TIMED_OUT"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-build", action="store_true")
    add_common_args(parser)
    args = parser.parse_args()

    if not args.skip_build:
        build()
    if not (DIST / "index.html").is_file():
        sys.exit(f"{DIST} has no index.html - run npm run build in frontend/ first.")

    sess = session(args)
    outputs = stack_outputs(sess, args.stack)
    app_id = outputs["SiteAppId"]
    amplify = sess.client("amplify")

    payload = zip_dist(DIST)
    deployment = amplify.create_deployment(appId=app_id, branchName=BRANCH)
    upload(deployment["zipUploadUrl"], payload)
    job_id = deployment["jobId"]
    amplify.start_deployment(appId=app_id, branchName=BRANCH, jobId=job_id)
    print(f"Uploaded {len(payload) / 1024:,.0f} KB; Amplify deployment job {job_id} started...")

    status = wait_for_job(amplify, app_id, job_id)
    if status != "SUCCEED":
        sys.exit(f"Deployment {job_id} ended with status {status} - see the Amplify console.")
    print(f"Deployed. Live at {outputs['SiteUrl']}")


if __name__ == "__main__":
    main()
