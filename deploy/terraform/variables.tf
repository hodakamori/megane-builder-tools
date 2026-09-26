variable "aws_region" {
  description = "AWS region for App Runner and ECR"
  type        = string
  default     = "ap-northeast-1"
}

variable "app_name" {
  description = "Name of the ECR repository and the App Runner service"
  type        = string
  default     = "megane-builder-tools"
}

variable "image_tag" {
  description = "Image tag in the ECR repository to run (the deploy workflow uses the git SHA)"
  type        = string
}

variable "cpu" {
  description = "App Runner vCPU units (1024 = 1 vCPU; 2048 = 2 vCPU)"
  type        = string
  default     = "2048"
}

variable "memory" {
  description = "App Runner memory in MB (must be valid for the chosen cpu)"
  type        = string
  default     = "4096"
}

variable "instances" {
  description = "Instances kept running (min = max; 1 is always-on without scaling)"
  type        = number
  default     = 1
}

variable "allowed_origins" {
  description = "Web origins allowed to call the server (megane Builder pages); requests from anywhere else are refused"
  type        = list(string)
  default = [
    "https://megane.tech-office-mori.com",
    "https://megane-labs.github.io",
  ]
}

variable "call_timeout" {
  description = "Seconds a tool call may take. App Runner closes every request at 120 s, so keep this below it."
  type        = number
  default     = 100

  validation {
    condition     = var.call_timeout > 0 && var.call_timeout < 120
    error_message = "call_timeout must be between 0 and 120 seconds (App Runner's request limit)."
  }
}

variable "max_concurrency" {
  description = "Tool calls computed at the same time per instance (roughly one per vCPU)"
  type        = number
  default     = 2
}
