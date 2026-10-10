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

# RADIUS NLB (UDP, for FreeRADIUS/Authentik RADIUS outpost)
resource "aws_lb" "radius" {
  name                             = "${var.cluster_name}-radius"
  internal                         = false
  load_balancer_type               = "network"
  subnets                          = module.vpc.public_subnet_ids
  enable_cross_zone_load_balancing = true

  tags = { Name = "${var.cluster_name}-shared-nlb" }
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

# Share the existing NLB with RADIUS. Keep its resource identity and DNS name.
resource "aws_lb_target_group" "nginx_https" {
  name_prefix          = "web-"
  port                 = 8443
  protocol             = "TCP"
  vpc_id               = module.vpc.vpc_id
  target_type          = "ip"
  proxy_protocol_v2    = true
  preserve_client_ip   = false
  deregistration_delay = 30

  # HTTP health probes would also receive a PROXY header. Kubernetes separately
  # checks nginx's HTTP readiness endpoint; NLB only checks its TLS socket.
  health_check {
    protocol = "TCP"
    port     = "traffic-port"
  }
  lifecycle { create_before_destroy = true }
}

resource "aws_lb_listener" "nginx_https" {
  load_balancer_arn = aws_lb.radius.arn
  port              = 443
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.nginx_https.arn
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

    cluster_name     = var.cluster_name
    aws_region       = var.aws_region
    aws_profile      = var.aws_profile
    authentik_domain = var.authentik_domain

    alb_target_group_arn      = var.retain_legacy_alb ? aws_lb_target_group.https[0].arn : ""
    retain_legacy_alb         = tostring(var.retain_legacy_alb)
    nlb_target_group_arn      = aws_lb_target_group.nginx_https.arn
    nlb_http_target_group_arn = aws_lb_target_group.acme_http.arn
    nlb_dns_name              = aws_lb.radius.dns_name
    acme_email                = var.acme_email
    acme_server               = var.acme_server
    vpc_cidr                  = var.vpc_cidr
    allowed_cidrs_json        = jsonencode(var.allowed_cidrs)
    albc_role_arn             = aws_iam_role.albc.arn
    vpc_id                    = module.vpc.vpc_id

    authentik_secret_key         = random_password.authentik_secret_key.result
    authentik_pg_password        = random_password.authentik_pg_password.result
    authentik_bootstrap_password = var.authentik_bootstrap_password
    authentik_bootstrap_email    = var.authentik_bootstrap_email
    authentik_api_token          = random_password.authentik_api_token.result
  }
}


# HTTP01 only. No PROXY header: ACME solver Ingress receives plain HTTP.
resource "aws_lb_target_group" "acme_http" {
  name_prefix          = "acme-"
  port                 = 8000
  protocol             = "TCP"
  vpc_id               = module.vpc.vpc_id
  target_type          = "ip"
  preserve_client_ip   = false
  deregistration_delay = 30
  health_check {
    protocol = "TCP"
    port     = "traffic-port"
  }
  lifecycle { create_before_destroy = true }
}

resource "aws_lb_listener" "acme_http" {
  load_balancer_arn = aws_lb.radius.arn
  port              = 80
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.acme_http.arn
  }
}
