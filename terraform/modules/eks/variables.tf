variable "cluster_name" {
  type = string
}

variable "kubernetes_version" {
  type        = string
  description = "Kubernetes version selected by the environment"
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "node_sg_id" {
  type = string
}

variable "additional_sg_id" {
  type        = string
  description = "Additional SG for cluster endpoint (e.g. eks_nodes_sg)"
}

variable "api_allowed_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDRs allowed to reach EKS API server public endpoint"
}

variable "spot_instance_types" {
  type        = list(string)
  default     = ["t4g.medium", "t4g.large"]
  description = "Graviton ARM64 Spot instance types (multiple for availability fallback)"
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

variable "tags" {
  type    = map(string)
  default = {}
}
