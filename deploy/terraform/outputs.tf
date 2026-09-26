output "service_url" {
  description = "App Runner default domain"
  value       = aws_apprunner_service.tools.service_url
}

output "mcp_endpoint" {
  description = "Streamable HTTP endpoint to paste into megane Builder's Python tools section"
  value       = "https://${aws_apprunner_service.tools.service_url}/mcp"
}

output "ecr_repository_url" {
  description = "Push images here"
  value       = aws_ecr_repository.tools.repository_url
}

output "token_secret_arn" {
  description = "Secrets Manager secret holding the bearer token"
  value       = aws_secretsmanager_secret.token.arn
}

output "read_token_command" {
  description = "Prints the bearer token"
  value       = "aws secretsmanager get-secret-value --region ${var.aws_region} --secret-id ${aws_secretsmanager_secret.token.arn} --query SecretString --output text"
}
