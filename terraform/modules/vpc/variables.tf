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

variable "tags" {
  type    = map(string)
  default = {}
}
