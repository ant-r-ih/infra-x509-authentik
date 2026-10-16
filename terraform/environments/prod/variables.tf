variable "aws_region" {
  type    = string
  default = "ap-northeast-1"
}

variable "aws_profile" {
  type        = string
  description = "Local AWS CLI profile for the infrastructure account"
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
  description = "Client CIDRs enforced by nginx for HTTPS and, for the SSH demo, by the VPC NACL (RADIUS access is configured separately)"

  validation {
    condition     = length(var.allowed_cidrs) <= 70
    error_message = "At most 70 CIDRs fit within the SSH demo NACL rule numbers 10-89."
  }
}

variable "ebs_reclaim_policy" {
  type        = string
  default     = "Retain"
  description = "gp3 StorageClass reclaim policy: Retain keeps EBS volumes after PVC deletion (production), Delete removes them (demo)"

  validation {
    condition     = contains(["Retain", "Delete"], var.ebs_reclaim_policy)
    error_message = "ebs_reclaim_policy must be Retain or Delete."
  }
}

variable "ssh_demo_enabled" {
  type        = bool
  default     = false
  description = "Expose the RADIUS-authenticated SSH demo device on NLB TCP 22"
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
  type        = list(string)
  default     = ["t4g.medium", "t4g.large"]
  description = "Graviton ARM64 Spot instance types for the AL2023 ARM64 node group"
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
