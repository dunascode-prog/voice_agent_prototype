"""Deploy to the single-server AWS demo (infra/terraform/ec2) through SSM.

    uv run python scripts/aws/deploy_ec2.py --profile AdministratorAccess-<account-id>
    uv run python scripts/aws/deploy_ec2.py --profile ... --ref claude/some-branch

The server checks out the ref from the public GitHub repo (so push it first), writes
its .env from Secrets Manager, and rebuilds/restarts the docker compose stack. Run it
again after push_secrets.py so the new keys are picked up.
"""

import argparse
import subprocess
import sys
import time

import boto3


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()  # noqa: S607


def find_instance(ec2, name: str) -> str:
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [name]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )
    ids = [i["InstanceId"] for r in resp["Reservations"] for i in r["Instances"]]
    if len(ids) != 1:
        raise SystemExit(f"Expected one running instance tagged Name={name}, found {ids or 'none'}")
    return ids[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=None, help="AWS CLI profile (admin)")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--name", default="voice-agent", help="Terraform var.name")
    parser.add_argument("--ref", default=None, help="Git branch or commit (default: local HEAD)")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    instance = find_instance(session.client("ec2"), args.name)
    ref = args.ref or git_head()
    ssm = session.client("ssm")
    cmd_id = ssm.send_command(
        InstanceIds=[instance],
        DocumentName="AWS-RunShellScript",
        Comment=f"voice-deploy {ref[:40]}",
        TimeoutSeconds=1800,
        Parameters={
            "commands": [f"voice-deploy '{ref}' 2>&1"],
            "executionTimeout": ["1800"],
        },
    )["Command"]["CommandId"]
    print(f"Deploying {ref} to {instance} (SSM command {cmd_id}) ...")

    started = time.monotonic()
    time.sleep(5)
    while True:
        try:
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(5)
            continue
        if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
            break
        time.sleep(10)

    out = (inv.get("StandardOutputContent") or "").strip().splitlines()
    print("\n".join(out[-25:]))
    print(f"{inv['Status']} after {time.monotonic() - started:.0f}s")
    return 0 if inv["Status"] == "Success" else 1


if __name__ == "__main__":
    sys.exit(main())
