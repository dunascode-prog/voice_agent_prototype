# Two Fargate services from one image: gateway (Twilio webhooks, outbound API) and
# agent (Media Streams WebSocket + Pipecat). The gateway runs database migrations on
# start; with one gateway task there is no race.

locals {
  public_base_url = "https://${aws_cloudfront_distribution.main.domain_name}"
  image           = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  environment = [
    for k, v in {
      APP_ENV                   = "aws"
      LOG_LEVEL                 = "INFO"
      PUBLIC_BASE_URL           = local.public_base_url
      MEDIA_WS_URL              = "wss://${aws_cloudfront_distribution.main.domain_name}/media"
      LLM_PROVIDER              = "bedrock"
      LLM_MODEL                 = var.llm_model
      BEDROCK_INFERENCE_PROFILE = "us"
      AWS_REGION                = var.region
      REDIS_URL                 = "redis://${aws_elasticache_cluster.main.cache_nodes[0].address}:6379/0"
      RECORDING_ENABLED         = "true"
    } : { name = k, value = v }
  ]

  secrets = concat(
    [for k in local.app_secret_keys : { name = k, valueFrom = "${aws_secretsmanager_secret.app.arn}:${k}::" }],
    [for k in local.internal_secret_keys : { name = k, valueFrom = "${aws_secretsmanager_secret.internal.arn}:${k}::" }],
  )
}

resource "aws_ecs_cluster" "main" {
  name = var.name
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${var.name}-gateway"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.gateway_cpu
  memory                   = var.gateway_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name         = "gateway"
    image        = local.image
    essential    = true
    command      = ["sh", "-c", "alembic upgrade head && exec voice-gateway"]
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment  = local.environment
    secrets      = local.secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "gateway"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "agent" {
  family                   = "${var.name}-agent"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.agent_cpu
  memory                   = var.agent_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name         = "agent"
    image        = local.image
    essential    = true
    command      = ["voice-agent"]
    portMappings = [{ containerPort = 8001, protocol = "tcp" }]
    environment  = local.environment
    secrets      = local.secrets
    stopTimeout  = 120 # Fargate maximum: let a live call finish its turn before SIGKILL
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "agent"
      }
    }
  }])
}

resource "aws_ecs_service" "gateway" {
  name                   = "${var.name}-gateway"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.gateway.arn
  desired_count          = var.desired_count
  launch_type            = "FARGATE"
  enable_execute_command = true

  health_check_grace_period_seconds  = 90 # migrations run first
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true # no NAT gateway, see network.tf
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.gateway.arn
    container_name   = "gateway"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener_rule.gateway]
}

resource "aws_ecs_service" "agent" {
  name                   = "${var.name}-agent"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.agent.arn
  desired_count          = var.desired_count
  launch_type            = "FARGATE"
  enable_execute_command = true

  health_check_grace_period_seconds  = 60
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.agent.arn
    container_name   = "agent"
    container_port   = 8001
  }

  depends_on = [aws_lb_listener_rule.media]
}
