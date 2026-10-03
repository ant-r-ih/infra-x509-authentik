output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "alb_sg_id" {
  value = aws_security_group.alb.id
}

output "eks_nodes_sg_id" {
  value = aws_security_group.eks_nodes.id
}

output "radius_sg_id" {
  value = aws_security_group.radius.id
}
