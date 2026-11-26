"""Copy the third-party API keys from your local .env into the AWS demo's secret.

    uv run python scripts/aws/push_secrets.py --profile AdministratorAccess-<account-id>

Reads only the keys the containers need from Secrets Manager (see
infra/terraform/secrets.tf), refuses placeholders from .env.example, and prints key
names only, never values. Restart the services afterwards (scripts/aws/deploy.py
--restart-only) so running tasks pick up the new values.
"""

import argparse
import json
import sys

import boto3
from dotenv import dotenv_values

KEYS = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "DEEPGRAM_API_KEY",
    "CARTESIA_API_KEY",
    "CARTESIA_VOICE_ID_EN",
)
PLACEHOLDER_MARKERS = ("your-", "xxxx", "change-me", "+15555550")


def is_placeholder(value: str) -> bool:
    return not value or any(m in value for m in PLACEHOLDER_MARKERS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=None, help="AWS CLI profile (admin)")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--secret", default="voice-agent/app")
    args = parser.parse_args()

    env = dotenv_values(args.env_file)
    values = {k: (env.get(k) or "").strip() for k in KEYS}
    bad = [k for k, v in values.items() if is_placeholder(v)]
    if bad:
        print(f"Not pushed: still empty or a placeholder in {args.env_file}: {', '.join(bad)}")
        return 1

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    session.client("secretsmanager").put_secret_value(
        SecretId=args.secret, SecretString=json.dumps(values)
    )
    print(f"Updated {args.secret}: {', '.join(KEYS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
