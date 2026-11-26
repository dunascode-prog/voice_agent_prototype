# Image build without local Docker: CodeBuild clones the public repo at a given ref,
# builds infra/docker/Dockerfile and pushes <short-sha> and latest to ECR.
# Started by scripts/aws/deploy_ecs.py (GIT_REF = the commit to build).

resource "aws_ecr_repository" "app" {
  name                 = var.name
  image_tag_mutability = "MUTABLE" # `latest` moves on every deploy
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "build" {
  name              = "/codebuild/${var.name}-image"
  retention_in_days = var.log_retention_days
}

resource "aws_codebuild_project" "image" {
  name          = "${var.name}-image"
  description   = "Build the voice-agent image and push it to ECR"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 20

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/amazonlinux-x86_64-standard:5.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true # docker build

    environment_variable {
      name  = "GIT_REPO_URL"
      value = var.git_repo_url
    }
    environment_variable {
      name  = "GIT_REF"
      value = "main"
    }
    environment_variable {
      name  = "ECR_REPO"
      value = aws_ecr_repository.app.repository_url
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.build.name
    }
  }

  source {
    type      = "NO_SOURCE"
    buildspec = <<-YAML
      version: 0.2
      phases:
        pre_build:
          commands:
            - aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$${ECR_REPO%%/*}"
            - git clone --quiet "$GIT_REPO_URL" src
            - git -C src fetch --quiet origin "$GIT_REF" && git -C src checkout --quiet FETCH_HEAD
            - echo "building $(git -C src rev-parse HEAD)"
        build:
          commands:
            - cd src && TAG=$(git rev-parse --short=12 HEAD) && docker build --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim -f infra/docker/Dockerfile -t "$ECR_REPO:$TAG" -t "$ECR_REPO:latest" . && docker push "$ECR_REPO:$TAG" && docker push "$ECR_REPO:latest"
    YAML
  }
}
