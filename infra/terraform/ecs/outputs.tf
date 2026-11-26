output "public_base_url" {
  description = "Public HTTPS base URL (PUBLIC_BASE_URL in the containers)."
  value       = local.public_base_url
}

output "twilio_voice_webhook" {
  description = "Twilio number -> Voice configuration -> A call comes in (HTTP POST)."
  value       = "${local.public_base_url}/twilio/voice"
}

output "twilio_status_webhook" {
  description = "Twilio number -> Voice configuration -> Call status changes (HTTP POST)."
  value       = "${local.public_base_url}/twilio/status"
}

output "media_ws_url" {
  value = "wss://${aws_cloudfront_distribution.main.domain_name}/media"
}

output "app_secret_name" {
  description = "Fill with: uv run python scripts/aws/push_secrets.py"
  value       = aws_secretsmanager_secret.app.name
}

output "internal_secret_name" {
  description = "Holds INTERNAL_API_TOKEN (for POST /calls/outbound)."
  value       = aws_secretsmanager_secret.internal.name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "codebuild_project" {
  value = aws_codebuild_project.image.name
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecs_services" {
  value = [aws_ecs_service.gateway.name, aws_ecs_service.agent.name]
}

output "log_group" {
  value = aws_cloudwatch_log_group.app.name
}
