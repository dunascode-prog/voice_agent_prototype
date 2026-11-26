# terraform

The AWS demo stack (decision D14 in `docs/ARCHITECTURE.md`). How to deploy, cost and troubleshooting: [`docs/README.md` → Deploying the AWS demo](../../docs/README.md#deploying-the-aws-demo).

| File | Creates |
|---|---|
| `network.tf` | VPC, 2 public + 2 private subnets, security groups (ALB from CloudFront only, tasks from the ALB only, data stores from the tasks only) |
| `cloudfront.tf` | Public HTTPS/WSS entry point, no caching, secret origin header |
| `alb.tf` | ALB with `/media` → agent and everything else → gateway, only for requests carrying the origin header |
| `ecs.tf` | Cluster, task definitions and services for gateway and agent, log group |
| `data.tf` | RDS Postgres 16 and ElastiCache Redis 7, private, single-AZ |
| `secrets.tf` | `voice-agent/app` (provider keys, filled by `scripts/aws/push_secrets.py`) and `voice-agent/internal` (generated DB URL and tokens) |
| `iam.tf` | Task execution role, task role (Bedrock invoke for `llm_model` only, ECS Exec), CodeBuild role |
| `build.tf` | ECR repository and the CodeBuild project that builds the image from GitHub |

State is local and gitignored. It holds generated secrets: don't commit or share it.
