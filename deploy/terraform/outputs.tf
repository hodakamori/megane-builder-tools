output "service_arn" {
  description = "App Runner service ARN (the deploy workflow starts and waits for deployments with it)"
  value       = aws_apprunner_service.tools.arn
}

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
