variable "aws_region" {
  type    = string
  default = "ap-northeast-1"
}

variable "aws_profile" {
  type    = string
  default = "default"
}

variable "cluster_name" {
  type    = string
  default = "x509-authentik"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "allowed_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "Client CIDRs enforced by nginx for HTTPS (RADIUS access is configured separately)"
}

variable "api_allowed_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDRs allowed to reach EKS API server (add operator IPs here)"
}

variable "kubernetes_version" {
  type    = string
  default = "1.37"
}

variable "spot_instance_types" {
  type    = list(string)
  default = ["t3.medium", "t3a.medium", "t3.large"]
}

variable "desired_nodes" {
  type    = number
  default = 2
}

variable "min_nodes" {
  type    = number
  default = 1
}

variable "max_nodes" {
  type    = number
  default = 4
}

variable "authentik_domain" {
  type        = string
  description = "FQDN for Authentik, e.g. auth.example.org"
}

variable "authentik_bootstrap_email" {
  type        = string
  description = "Email address for the initial akadmin account"
}

variable "authentik_bootstrap_password" {
  type        = string
  sensitive   = true
  description = "Password for the initial akadmin account (set by operator)"
}

variable "acme_email" {
  type        = string
  description = "ACME account contact email"
}

variable "acme_server" {
  type        = string
  default     = "https://acme-v02.api.letsencrypt.org/directory"
  description = "Use the Let's Encrypt staging directory for initial HTTP01 tests"
}
