# Destroy-time hooks for resources that Terraform does not create.
# Destroy order: k8s_teardown → EKS nodes/addon/IAM/target groups → CNI ENI
# cleanup → VPC. Hooks run with the local AWS profile and need ansible-playbook,
# kubectl and the aws CLI on PATH (the same tools as deployment).

# Runs the Ansible teardown while the nodes, EBS CSI driver, load balancer
# controller IAM, target groups and NAT egress still exist, so controllers can
# release target registrations, security group rules and Delete-policy volumes.
resource "terraform_data" "k8s_teardown" {
  input = {
    cluster_name = var.cluster_name
    aws_region   = var.aws_region
    aws_profile  = var.aws_profile
    # Relative to this directory (terraform -chdir=terraform/environments/prod).
    ansible_dir = "../../../ansible"
  }

  depends_on = [
    module.vpc,
    module.eks,
    aws_eks_addon.ebs_csi,
    aws_iam_role_policy_attachment.ebs_csi,
    aws_iam_role_policy_attachment.albc,
    aws_lb_target_group.nginx_https,
    aws_lb_target_group.acme_http,
    aws_lb_target_group.radius_auth,
    aws_lb_target_group.ssh_demo,
  ]

  provisioner "local-exec" {
    when        = destroy
    working_dir = self.input.ansible_dir
    command     = "ansible-playbook playbooks/teardown.yml -e cluster_name=${self.input.cluster_name} -e aws_region=${self.input.aws_region} -e aws_profile=${self.input.aws_profile}"
  }
}

# The VPC CNI can leave detached aws-K8S-* interfaces after nodes terminate;
# they block deleting the subnets and the node security group. module.eks
# depends on this resource, so this runs after the nodes are gone.
resource "terraform_data" "cni_eni_cleanup" {
  input = {
    vpc_id      = module.vpc.vpc_id
    aws_region  = var.aws_region
    aws_profile = var.aws_profile
  }

  # Run before any VPC resource (subnets, node security group) is destroyed.
  depends_on = [module.vpc]

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -eu
      for attempt in 1 2 3 4 5 6; do
        enis=$(aws ec2 describe-network-interfaces \
          --profile '${self.input.aws_profile}' --region '${self.input.aws_region}' \
          --filters Name=vpc-id,Values='${self.input.vpc_id}' Name=status,Values=available \
                    Name=description,Values='aws-K8S-*' \
          --query 'NetworkInterfaces[].NetworkInterfaceId' --output text)
        for eni in $enis; do
          echo "Deleting leftover CNI interface $eni"
          aws ec2 delete-network-interface --network-interface-id "$eni" \
            --profile '${self.input.aws_profile}' --region '${self.input.aws_region}'
        done
        in_use=$(aws ec2 describe-network-interfaces \
          --profile '${self.input.aws_profile}' --region '${self.input.aws_region}' \
          --filters Name=vpc-id,Values='${self.input.vpc_id}' Name=description,Values='aws-K8S-*' \
          --query 'length(NetworkInterfaces)' --output text)
        [ "$in_use" = 0 ] && exit 0
        sleep 20
      done
      echo "CNI interfaces still present in ${self.input.vpc_id}; check EC2." >&2
    EOT
  }
}
