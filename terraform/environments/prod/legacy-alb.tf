# Temporary compatibility stack. Disable only after NLB cutover.
# Existing versioned S3 CA archives are retained; no force_destroy.
# S3 bucket for ALB Trust Store CA bundle
resource "aws_s3_bucket" "assets" {
  count  = var.ca_bundle_s3_bucket != "" ? 1 : 0
  bucket = var.ca_bundle_s3_bucket
}

resource "aws_s3_bucket_versioning" "assets" {
  count  = var.ca_bundle_s3_bucket != "" ? 1 : 0
  bucket = aws_s3_bucket.assets[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "assets" {
  count                   = var.ca_bundle_s3_bucket != "" ? 1 : 0
  bucket                  = aws_s3_bucket.assets[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Upload CA bundle from repo — Terraform manages the S3 object directly
resource "aws_s3_object" "ca_bundle" {
  count  = var.ca_bundle_s3_bucket != "" ? 1 : 0
  bucket = aws_s3_bucket.assets[0].id
  key    = var.ca_bundle_s3_key
  source = "${path.module}/../../../pki/ca-bundle.pem"
  etag   = filemd5("${path.module}/../../../pki/ca-bundle.pem")
}

# ALB Trust Store reads from S3; depends on the object existing
resource "aws_lb_trust_store" "client_ca" {
  count                            = var.retain_legacy_alb ? 1 : 0
  name                             = "${var.cluster_name}-client-ca"
  ca_certificates_bundle_s3_bucket = aws_s3_bucket.assets[0].id
  ca_certificates_bundle_s3_key    = aws_s3_object.ca_bundle[0].key

  ca_certificates_bundle_s3_object_version = aws_s3_object.ca_bundle[0].version_id

  depends_on = [aws_s3_object.ca_bundle]
}

# Preserve the existing ACM certificate and its DNS validation during cutover.
resource "aws_acm_certificate" "main" {
  count             = var.retain_legacy_alb ? 1 : 0
  domain_name       = var.authentik_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# Wait for the certificate to be issued (requires the CNAME to be in DNS first)
resource "aws_acm_certificate_validation" "main" {
  count           = var.retain_legacy_alb ? 1 : 0
  certificate_arn = aws_acm_certificate.main[0].arn
  # validation_record_fqdns intentionally omitted:
  # we cannot manage the external Route 53 zone from this account.
  # Terraform will wait (up to ~45 min) for ACM to detect the DNS record.
  timeouts {
    create = "45m"
  }
}

# ALB with mTLS for client certificate authentication
resource "aws_lb" "main" {
  count              = var.retain_legacy_alb ? 1 : 0
  name               = "${var.cluster_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [module.vpc.alb_sg_id]
  subnets            = module.vpc.public_subnet_ids

  tags = { Name = "${var.cluster_name}-alb" }
}

resource "aws_lb_target_group" "https" {
  count       = var.retain_legacy_alb ? 1 : 0
  name        = "${var.cluster_name}-https"
  port        = 9000
  protocol    = "HTTP"
  vpc_id      = module.vpc.vpc_id
  target_type = "ip"

  health_check {
    path                = "/-/health/live/"
    protocol            = "HTTP"
    port                = "9000"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "https" {
  count             = var.retain_legacy_alb ? 1 : 0
  load_balancer_arn = aws_lb.main[0].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.main[0].certificate_arn

  # mTLS: verify client certificate against client CA
  mutual_authentication {
    mode                             = "verify"
    trust_store_arn                  = aws_lb_trust_store.client_ca[0].arn
    ignore_client_certificate_expiry = false
  }

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.https[0].arn
  }
}


# Preserve resource identities when upgrading an existing main deployment.
moved {
  from = aws_s3_bucket.assets
  to   = aws_s3_bucket.assets[0]
}
moved {
  from = aws_s3_bucket_versioning.assets
  to   = aws_s3_bucket_versioning.assets[0]
}
moved {
  from = aws_s3_bucket_public_access_block.assets
  to   = aws_s3_bucket_public_access_block.assets[0]
}
moved {
  from = aws_s3_object.ca_bundle
  to   = aws_s3_object.ca_bundle[0]
}
moved {
  from = aws_lb_trust_store.client_ca
  to   = aws_lb_trust_store.client_ca[0]
}
moved {
  from = aws_acm_certificate.main
  to   = aws_acm_certificate.main[0]
}
moved {
  from = aws_acm_certificate_validation.main
  to   = aws_acm_certificate_validation.main[0]
}
moved {
  from = aws_lb.main
  to   = aws_lb.main[0]
}
moved {
  from = aws_lb_target_group.https
  to   = aws_lb_target_group.https[0]
}
moved {
  from = aws_lb_listener.https
  to   = aws_lb_listener.https[0]
}
