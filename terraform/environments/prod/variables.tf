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
  description = "allowed network — allowed to reach ALB and RADIUS"
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
  description = "FQDN for Authentik (must have ACM cert), e.g. auth.example.org"
}

variable "ca_bundle_s3_bucket" {
  type        = string
  description = "S3 bucket holding client CA bundle PEM for ALB Trust Store"
}

variable "ca_bundle_s3_key" {
  type        = string
  default     = "ca-bundle.pem"
  description = "S3 key for CA bundle"
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
