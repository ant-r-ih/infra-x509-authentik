output "eks_cluster_name" {
  value = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "acm_validation_cname" {
  description = "Add this CNAME record in the example.org Route 53 zone (other account) to validate the ACM certificate"
  value = {
    for dvo in aws_acm_certificate.main.domain_validation_options : dvo.domain_name => {
      name  = dvo.resource_record_name
      type  = dvo.resource_record_type
      value = dvo.resource_record_value
    }
  }
}

output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "Point your DNS CNAME (auth.example.org) to this after certificate is issued"
}

output "alb_target_group_arn" {
  value       = aws_lb_target_group.https.arn
  description = "ARN of the ALB target group; used by TargetGroupBinding in Ansible"
}

output "radius_target_group_arn" {
  value       = aws_lb_target_group.radius_auth.arn
  description = "ARN of the RADIUS NLB target group"
}

output "radius_nlb_dns_name" {
  value       = aws_lb.radius.dns_name
  description = "NLB DNS for RADIUS clients"
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "oidc_provider_arn" {
  value = module.eks.oidc_provider_arn
}

output "albc_role_arn" {
  value       = aws_iam_role.albc.arn
  description = "IAM role ARN for AWS Load Balancer Controller (used in Helm values)"
}

output "authentik_secret_key" {
  value     = random_password.authentik_secret_key.result
  sensitive = true
}

output "authentik_pg_password" {
  value     = random_password.authentik_pg_password.result
  sensitive = true
}

output "authentik_bootstrap_password" {
  value     = var.authentik_bootstrap_password
  sensitive = true
}

output "authentik_bootstrap_email" {
  value = var.authentik_bootstrap_email
}

output "authentik_api_token" {
  value     = random_password.authentik_api_token.result
  sensitive = true
}

output "authentik_domain" {
  value = var.authentik_domain
}
