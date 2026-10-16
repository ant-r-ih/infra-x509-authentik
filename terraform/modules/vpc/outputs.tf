output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "eks_nodes_sg_id" {
  value = aws_security_group.eks_nodes.id
}

output "default_network_acl_id" {
  value = aws_vpc.main.default_network_acl_id
}
