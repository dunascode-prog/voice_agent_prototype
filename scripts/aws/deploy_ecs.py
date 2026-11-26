"""Build the image in CodeBuild and roll it out to the ECS services (infra/terraform/ecs).

    uv run python scripts/aws/deploy_ecs.py --profile AdministratorAccess-<account-id>
    uv run python scripts/aws/deploy_ecs.py --profile ... --ref claude/some-branch
    uv run python scripts/aws/deploy_ecs.py --profile ... --restart-only  # after push_secrets

CodeBuild clones the public GitHub repo, so the ref must be pushed. The default is the
commit checked out locally (it must exist on GitHub).
"""

import argparse
import subprocess
import sys
import time

import boto3


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()  # noqa: S607


def build(session: boto3.Session, project: str, ref: str) -> None:
    cb = session.client("codebuild")
    build_id = cb.start_build(
        projectName=project,
        environmentVariablesOverride=[{"name": "GIT_REF", "value": ref, "type": "PLAINTEXT"}],
    )["build"]["id"]
    print(f"CodeBuild {build_id} building {ref} ...")
    started = time.monotonic()
    while True:
        b = cb.batch_get_builds(ids=[build_id])["builds"][0]
        status = b["buildStatus"]
        if status != "IN_PROGRESS":
            break
        time.sleep(10)
    print(f"  {status} after {time.monotonic() - started:.0f}s")
    if status != "SUCCEEDED":
        print(f"  logs: {b.get('logs', {}).get('deepLink', '(none)')}")
        raise SystemExit(1)


def roll_out(session: boto3.Session, cluster: str, services: list[str]) -> None:
    ecs = session.client("ecs")
    for svc in services:
        ecs.update_service(cluster=cluster, service=svc, forceNewDeployment=True)
        print(f"Rolling out {svc} ...")
    started = time.monotonic()
    ecs.get_waiter("services_stable").wait(
        cluster=cluster, services=services, WaiterConfig={"Delay": 15, "MaxAttempts": 60}
    )
    print(f"  stable after {time.monotonic() - started:.0f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=None, help="AWS CLI profile (admin)")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--name", default="voice-agent", help="Terraform var.name")
    parser.add_argument("--ref", default=None, help="Git branch or commit (default: local HEAD)")
    parser.add_argument("--restart-only", action="store_true", help="Skip the build")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if not args.restart_only:
        build(session, f"{args.name}-image", args.ref or git_head())
    roll_out(session, args.name, [f"{args.name}-gateway", f"{args.name}-agent"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
