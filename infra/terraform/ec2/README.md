# terraform/ec2

The low-cost AWS demo (decision D15 in `docs/ARCHITECTURE.md`): one EC2 instance runs the same `docker compose` stack as local development, behind CloudFront. How to deploy: [`docs/README.md` → Deploying the AWS demo](../../../docs/README.md#deploying-the-aws-demo).

| File | Creates |
|---|---|
| `main.tf` | VPC with one public subnet; security group (port 80 from CloudFront's ranges only, no SSH); the `voice-agent/app` and `voice-agent/internal` secrets; instance role (Bedrock for `llm_model`, the two secrets, SSM); the instance with an Elastic IP; the CloudFront distribution with a secret origin header |
| `user_data.sh.tftpl` | First boot: Docker + compose, 2 GB swap, clones the repo, installs `voice-deploy` |
| `../../docker/docker-compose.aws.yml`, `../../docker/Caddyfile.aws` | Compose override used on the server: Caddy on port 80 requiring the origin header, no published database ports, restart on reboot |

`voice-deploy <ref>` (run by `scripts/aws/deploy_ec2.py` through SSM) checks out the ref, writes `.env` from both secrets and runs `docker compose up -d --build`.
