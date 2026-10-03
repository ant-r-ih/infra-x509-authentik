locals {
  common_tags = {
    Project     = "x509-authentik"
    Environment = "prod"
    ManagedBy   = "terraform"
  }
}

module "vpc" {
  source = "../../modules/vpc"

  name          = var.cluster_name
  cluster_name  = var.cluster_name
  vpc_cidr      = var.vpc_cidr
  allowed_cidrs = var.allowed_cidrs
  tags          = local.common_tags
}

module "eks" {
  source = "../../modules/eks"

  cluster_name        = var.cluster_name
  kubernetes_version  = var.kubernetes_version
  private_subnet_ids  = module.vpc.private_subnet_ids
  node_sg_id          = module.vpc.eks_nodes_sg_id
  additional_sg_id    = module.vpc.eks_nodes_sg_id
  api_allowed_cidrs   = var.api_allowed_cidrs
  spot_instance_types = var.spot_instance_types
  desired_nodes       = var.desired_nodes
  min_nodes           = var.min_nodes
  max_nodes           = var.max_nodes
  tags                = local.common_tags
}

# IRSA role for AWS Load Balancer Controller
data "aws_iam_policy_document" "albc_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_id}:sub"
      values   = ["system:serviceaccount:kube-system:aws-load-balancer-controller"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_id}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "albc" {
  name               = "${var.cluster_name}-albc"
  assume_role_policy = data.aws_iam_policy_document.albc_assume.json
}

resource "aws_iam_policy" "albc" {
  name   = "${var.cluster_name}-albc-policy"
  policy = file("${path.module}/policies/albc.json")
}

resource "aws_iam_role_policy_attachment" "albc" {
  role       = aws_iam_role.albc.name
  policy_arn = aws_iam_policy.albc.arn
}

# IRSA role for EBS CSI driver (required for PVC provisioning)
locals {
  oidc_id = replace(module.eks.oidc_provider_url, "https://", "")
}

data "aws_iam_policy_document" "ebs_csi_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_id}:sub"
      values   = ["system:serviceaccount:kube-system:ebs-csi-controller-sa"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_id}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name               = "${var.cluster_name}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.ebs_csi_assume.json
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name             = module.eks.cluster_name
  addon_name               = "aws-ebs-csi-driver"
  service_account_role_arn = aws_iam_role.ebs_csi.arn
  depends_on               = [module.eks]
}

# S3 bucket for ALB Trust Store CA bundle
resource "aws_s3_bucket" "assets" {
  bucket = var.ca_bundle_s3_bucket
}

resource "aws_s3_bucket_versioning" "assets" {
  bucket = aws_s3_bucket.assets.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Upload CA bundle from repo — Terraform manages the S3 object directly
resource "aws_s3_object" "ca_bundle" {
  bucket = aws_s3_bucket.assets.id
  key    = var.ca_bundle_s3_key
  source = "${path.module}/../../../pki/ca-bundle.pem"
  etag   = filemd5("${path.module}/../../../pki/ca-bundle.pem")
}

# ALB Trust Store reads from S3; depends on the object existing
resource "aws_lb_trust_store" "client_ca" {
  name                             = "${var.cluster_name}-client-ca"
  ca_certificates_bundle_s3_bucket = aws_s3_bucket.assets.id
  ca_certificates_bundle_s3_key    = aws_s3_object.ca_bundle.key

  depends_on = [aws_s3_object.ca_bundle]
}

# ACM certificate for ALB — DNS validation (cross-account Route 53).
# After apply, run: terraform output acm_validation_cname
# and add the printed CNAME record in the example.org Route 53 hosted zone
# (separate AWS account). Certificate becomes ISSUED within ~5 minutes.
resource "aws_acm_certificate" "main" {
  domain_name       = var.authentik_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# Wait for the certificate to be issued (requires the CNAME to be in DNS first)
resource "aws_acm_certificate_validation" "main" {
  certificate_arn = aws_acm_certificate.main.arn
  # validation_record_fqdns intentionally omitted:
  # we cannot manage the external Route 53 zone from this account.
  # Terraform will wait (up to ~45 min) for ACM to detect the DNS record.
  timeouts {
    create = "45m"
  }
}

# ALB with mTLS for client certificate authentication
resource "aws_lb" "main" {
  name               = "${var.cluster_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [module.vpc.alb_sg_id]
  subnets            = module.vpc.public_subnet_ids

  tags = { Name = "${var.cluster_name}-alb" }
}

resource "aws_lb_target_group" "https" {
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
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.main.certificate_arn

  # mTLS: verify client certificate against client CA
  mutual_authentication {
    mode                             = "verify"
    trust_store_arn                  = aws_lb_trust_store.client_ca.arn
    ignore_client_certificate_expiry = false
  }

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.https.arn
  }
}

# RADIUS NLB (UDP, for FreeRADIUS/Authentik RADIUS outpost)
resource "aws_lb" "radius" {
  name               = "${var.cluster_name}-radius"
  internal           = false
  load_balancer_type = "network"
  subnets            = module.vpc.public_subnet_ids

  tags = { Name = "${var.cluster_name}-radius-nlb" }
}

resource "aws_lb_target_group" "radius_auth" {
  name        = "${var.cluster_name}-radius-1812"
  port        = 1812
  protocol    = "UDP"
  vpc_id      = module.vpc.vpc_id
  target_type = "ip"

  health_check {
    protocol = "TCP"
    port     = 9300
  }
}

resource "aws_lb_listener" "radius_auth" {
  load_balancer_arn = aws_lb.radius.arn
  port              = 1812
  protocol          = "UDP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.radius_auth.arn
  }
}

# ── Authentik application secrets ───────────────────────────────────────────
# Generated once; stored in Terraform state. Ansible reads these via
# terraform output -json — no environment variables needed at deploy time.

resource "random_password" "authentik_secret_key" {
  length  = 60
  special = false
  lifecycle { ignore_changes = [result] }
}

resource "random_password" "authentik_pg_password" {
  length  = 32
  special = false
  lifecycle { ignore_changes = [result] }
}

resource "random_password" "authentik_api_token" {
  length  = 60
  special = false
  lifecycle { ignore_changes = [result] }
}

resource "ansible_host" "deploy" {
  name   = "deploy.x509-authentik"
  groups = ["x509-authentik"]
  variables = {
    ansible_connection = "local"

    cluster_name              = var.cluster_name
    aws_region                = var.aws_region
    aws_profile               = var.aws_profile
    authentik_domain          = var.authentik_domain

    alb_target_group_arn      = aws_lb_target_group.https.arn
    albc_role_arn             = aws_iam_role.albc.arn
    vpc_id                    = module.vpc.vpc_id

    authentik_secret_key         = random_password.authentik_secret_key.result
    authentik_pg_password        = random_password.authentik_pg_password.result
    authentik_bootstrap_password = var.authentik_bootstrap_password
    authentik_bootstrap_email    = var.authentik_bootstrap_email
    authentik_api_token          = random_password.authentik_api_token.result
  }
}

