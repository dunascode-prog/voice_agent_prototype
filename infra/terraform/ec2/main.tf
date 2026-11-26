# Low-cost AWS demo: one EC2 instance runs the same docker compose stack as local
# development (Caddy proxy, gateway, agent, Postgres, Redis). CloudFront gives it a
# valid HTTPS/WSS address, so no domain and no ngrok. About $20/month while running.
#
#   Twilio --HTTPS / WSS--> CloudFront --HTTP + X-Origin-Verify--> EC2:80 (Caddy)
#
# No SSH: the instance is managed through SSM (scripts/aws/deploy_ec2.py).

# --- Network: one public subnet ---

resource "aws_vpc" "main" {
  cidr_block           = "10.41.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = var.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = var.name }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  availability_zone       = data.aws_availability_zones.available.names[0]
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, 0)
  map_public_ip_on_launch = true

  tags = { Name = "${var.name}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.name}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "server" {
  name        = "${var.name}-server"
  description = "Port 80 from CloudFront only; no SSH (SSM instead)"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "http_from_cloudfront" {
  security_group_id = aws_security_group.server.id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.server.id
  description       = "Twilio, Deepgram, Cartesia, Bedrock, GitHub, package mirrors"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- Secrets ---
# <name>/app: provider keys, created empty, filled by scripts/aws/push_secrets.py.
# <name>/internal: generated tokens plus the non-secret runtime settings. The server
# turns both into the compose .env on every deploy.

locals {
  app_secret_keys = [
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "DEEPGRAM_API_KEY",
    "CARTESIA_API_KEY",
    "CARTESIA_VOICE_ID_EN",
  ]
  public_base_url = "https://${aws_cloudfront_distribution.main.domain_name}"
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/app"
  description             = "Third-party API keys for the voice agent (set with scripts/aws/push_secrets.py)"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app_placeholder" {
  secret_id     = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({ for k in local.app_secret_keys : k => "" })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "random_password" "internal_api_token" {
  length  = 43
  special = false
}

resource "random_password" "stream_token_signing_key" {
  length  = 43
  special = false
}

resource "random_password" "origin_verify" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "internal" {
  name                    = "${var.name}/internal"
  description             = "Generated tokens and runtime settings (Terraform)"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "internal" {
  secret_id = aws_secretsmanager_secret.internal.id
  secret_string = jsonencode({
    APP_ENV                   = "aws"
    PUBLIC_BASE_URL           = local.public_base_url
    MEDIA_WS_URL              = "wss://${aws_cloudfront_distribution.main.domain_name}/media"
    LLM_PROVIDER              = "bedrock"
    LLM_MODEL                 = var.llm_model
    BEDROCK_INFERENCE_PROFILE = "us"
    AWS_REGION                = var.region
    RECORDING_ENABLED         = "true"
    INTERNAL_API_TOKEN        = random_password.internal_api_token.result
    STREAM_TOKEN_SIGNING_KEY  = random_password.stream_token_signing_key.result
    ORIGIN_VERIFY             = random_password.origin_verify.result
  })
}

# --- Instance role: Bedrock, the two secrets, SSM management ---

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "server" {
  name               = "${var.name}-server"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.server.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "server" {
  statement {
    sid     = "InvokeClaude"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/us.anthropic.${var.llm_model}",
      "arn:aws:bedrock:*::foundation-model/anthropic.${var.llm_model}",
    ]
  }

  statement {
    sid       = "ReadSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app.arn, aws_secretsmanager_secret.internal.arn]
  }
}

resource "aws_iam_role_policy" "server" {
  name   = "app"
  role   = aws_iam_role.server.id
  policy = data.aws_iam_policy_document.server.json
}

resource "aws_iam_instance_profile" "server" {
  name = "${var.name}-server"
  role = aws_iam_role.server.name
}

# --- The server ---

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "server" {
  ami                    = data.aws_ssm_parameter.al2023.insecure_value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.server.id]
  iam_instance_profile   = aws_iam_instance_profile.server.name

  # Installs Docker and clones the repo. The app starts on the first
  # scripts/aws/deploy_ec2.py run, after the secrets are filled. CRs are stripped in
  # case a Windows checkout converted the template to CRLF (bash would fail on them).
  user_data = replace(templatefile("${path.module}/user_data.sh.tftpl", {
    name     = var.name
    region   = var.region
    repo_url = var.git_repo_url
  }), "\r", "")
  user_data_replace_on_change = true

  metadata_options {
    http_tokens = "required"
    # 2 hops so the containers (one bridge hop away) can reach the instance role.
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.volume_size_gb
    encrypted   = true
  }

  lifecycle {
    ignore_changes = [ami] # don't replace the server whenever a new AL2023 image ships
  }

  tags = { Name = var.name }
}

# Fixed address, so the CloudFront origin survives a stop/start.
resource "aws_eip" "server" {
  instance = aws_instance.server.id
  domain   = "vpc"
  tags     = { Name = var.name }
}

# --- CloudFront: valid HTTPS/WSS without a domain ---

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "main" {
  enabled         = true
  comment         = "${var.name} demo (EC2): Twilio webhooks + media WebSocket"
  price_class     = "PriceClass_100"
  is_ipv6_enabled = true

  origin {
    origin_id   = "server"
    domain_name = aws_eip.server.public_dns

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }

    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_verify.result
    }
  }

  default_cache_behavior {
    target_origin_id         = "server"
    viewer_protocol_policy   = "https-only" # a redirect would turn Twilio's POST into a GET
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    compress                 = false
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
