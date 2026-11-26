# terraform

Two AWS demo stacks. They create resources with the same names, so apply only one. How to deploy, cost and troubleshooting: [`docs/README.md` → Deploying the AWS demo](../../docs/README.md#deploying-the-aws-demo).

| Stack | What it is | Cost while running |
|---|---|---|
| [`ec2/`](ec2/) | **Default.** One EC2 server running the docker compose stack behind CloudFront (decision D15) | about $21/month |
| [`ecs/`](ecs/) | ECS Fargate + ALB + RDS + ElastiCache + CodeBuild behind CloudFront (decision D14) | about $100–110/month |

State is local and gitignored. It holds generated secrets: don't commit or share it.
