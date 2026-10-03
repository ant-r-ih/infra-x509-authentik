variable "name" {
  type        = string
  description = "Resource name prefix"
}

variable "cluster_name" {
  type        = string
  description = "EKS cluster name (used for subnet tags)"
}

variable "vpc_cidr" {
  type        = string
  default     = "10.0.0.0/16"
  description = "VPC CIDR block"
}

variable "allowed_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDRs allowed to reach ALB and RADIUS (allowed network)"
}

variable "tags" {
  type    = map(string)
  default = {}
}
