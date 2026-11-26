output "public_base_url" {
  value = local.public_base_url
}

output "twilio_voice_webhook" {
  description = "Twilio number -> Voice configuration -> A call comes in (HTTP POST)."
  value       = "${local.public_base_url}/twilio/voice"
}

output "twilio_status_webhook" {
  description = "Twilio number -> Voice configuration -> Call status changes (HTTP POST)."
  value       = "${local.public_base_url}/twilio/status"
}

output "instance_id" {
  value = aws_instance.server.id
}

output "app_secret_name" {
  description = "Fill with: uv run python scripts/aws/push_secrets.py"
  value       = aws_secretsmanager_secret.app.name
}

output "internal_secret_name" {
  description = "Holds INTERNAL_API_TOKEN (for POST /calls/outbound)."
  value       = aws_secretsmanager_secret.internal.name
}
