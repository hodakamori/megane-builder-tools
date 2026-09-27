# megane-builder-tools on AWS App Runner: an always-on Streamable HTTP MCP server
# for megane Builder's "Python tools" section.
#
# Two-step apply (the service needs the image to exist; see README.md and
# .github/workflows/deploy.yml):
#   1. terraform apply -target=aws_ecr_repository.tools -var image_tag=<tag>
#   2. docker push <ecr>/megane-builder-tools:<tag>
#   3. terraform apply -var image_tag=<tag>

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    # No resource uses it any more; kept so the bearer token of the first
    # (token-authenticated) deployment, still in the state, can be destroyed.
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # The same state bucket megane's demo stack uses, under its own key.
  backend "s3" {
    bucket = "megane-terraform-state"
    key    = "builder-tools/terraform.tfstate"
    region = "ap-northeast-1"
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  common_tags = {
    App       = var.app_name
    ManagedBy = "terraform"
  }

  runtime_environment_variables = {
    # Public mode: no token (the demo page is public, so a token in it would
    # be too); every request must carry one of these Origins.
    MEGANE_BUILDER_TOOLS_PUBLIC          = "1"
    MEGANE_BUILDER_TOOLS_ALLOWED_ORIGINS = join(",", var.allowed_origins)
    # App Runner's public name is only known after creation; the Origin gate
    # and CORS apply regardless, so the Host check is off.
    MEGANE_BUILDER_TOOLS_ALLOWED_HOSTS   = "*"
    MEGANE_BUILDER_TOOLS_STATELESS       = "1"
    MEGANE_BUILDER_TOOLS_CALL_TIMEOUT    = tostring(var.call_timeout)
    MEGANE_BUILDER_TOOLS_MAX_CONCURRENCY = tostring(var.max_concurrency)
  }
}

# ---------------------------------------------------------------------------
# Container registry
# ---------------------------------------------------------------------------
resource "aws_ecr_repository" "tools" {
  name                 = var.app_name
  image_tag_mutability = "MUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "tools" {
  repository = aws_ecr_repository.tools.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------
# IAM: App Runner pulls from ECR
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "build_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "access" {
  name               = "${var.app_name}-apprunner-ecr-access"
  assume_role_policy = data.aws_iam_policy_document.build_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "access_ecr" {
  role       = aws_iam_role.access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# The role the running container assumes. It has no permissions; it exists
# because App Runner cannot drop an instance role once a service has one (the
# provider omits an empty instance_role_arn, which App Runner reads as
# "unchanged"), so a service created with it keeps needing a role by this name.
data "aws_iam_policy_document" "tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.app_name}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.tasks_assume.json
  tags               = local.common_tags
}

# ---------------------------------------------------------------------------
# App Runner: one always-on instance
# ---------------------------------------------------------------------------
resource "aws_apprunner_auto_scaling_configuration_version" "fixed" {
  auto_scaling_configuration_name = "${var.app_name}-fixed"
  min_size                        = var.instances
  max_size                        = var.instances
  max_concurrency                 = 25
  tags                            = local.common_tags
}

resource "aws_apprunner_service" "tools" {
  service_name                   = var.app_name
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.fixed.arn

  source_configuration {
    auto_deployments_enabled = false

    authentication_configuration {
      access_role_arn = aws_iam_role.access.arn
    }

    image_repository {
      image_identifier      = "${aws_ecr_repository.tools.repository_url}:${var.image_tag}"
      image_repository_type = "ECR"

      image_configuration {
        port                          = "8080"
        runtime_environment_variables = local.runtime_environment_variables
      }
    }
  }

  instance_configuration {
    cpu               = var.cpu
    memory            = var.memory
    instance_role_arn = aws_iam_role.instance.arn
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  tags = local.common_tags

  # Terraform creates the service; the deploy workflow rolls out every image
  # and configuration change with `aws apprunner update-service`. The provider
  # cannot remove runtime secrets or an instance role (it omits empty values,
  # which App Runner reads as "unchanged"), so an update through it can leave a
  # service pointing at deleted resources, which then fails to deploy.
  lifecycle {
    ignore_changes = [source_configuration, instance_configuration]
  }

  depends_on = [aws_iam_role_policy_attachment.access_ecr]
}
