output "eks_cluster_name" {
  value = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "nlb_dns_name" {
  value       = aws_lb.radius.dns_name
  description = "Web DNS CNAME target; TCP 443 terminates at nginx"
}

output "nlb_target_group_arn" {
  value = aws_lb_target_group.nginx_https.arn
}

output "legacy_alb_dns_name" {
  value = var.retain_legacy_alb ? aws_lb.main[0].dns_name : null
}

output "nlb_http_target_group_arn" {
  value = aws_lb_target_group.acme_http.arn
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
