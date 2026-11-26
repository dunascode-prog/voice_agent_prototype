# Public entry point. CloudFront gives a valid HTTPS/WSS address (dxxxx.cloudfront.net)
# with no domain or certificate to manage, which is all Twilio needs for webhooks and
# Media Streams. Nothing is cached; every request goes to the ALB.
#
# Twilio signs the URL it called (https://dxxxx.cloudfront.net/twilio/voice). The
# gateway rebuilds that URL from PUBLIC_BASE_URL, so the Host header the ALB sees
# does not matter. A custom domain later = ACM certificate + alias here + new
# PUBLIC_BASE_URL; nothing else changes.

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "main" {
  enabled         = true
  comment         = "${var.name} demo: Twilio webhooks + media WebSocket"
  price_class     = "PriceClass_100"
  is_ipv6_enabled = true

  origin {
    origin_id   = "alb"
    domain_name = aws_lb.main.dns_name

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only" # the ALB has no certificate without a domain
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
    target_origin_id         = "alb"
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
