# Deployment and operation

## Architecture

Terraform manages a shared NLB with TCP 80/443 and RADIUS UDP 1812 listeners.
Both HTTP target groups use IP targets; only HTTPS uses PROXY protocol v2.
nginx terminates TLS/mTLS on 8443,
checks the client certificate against the private CA bundle, applies
`allowed_cidrs` to the original address, and proxies to Authentik. TCP 443 passes
through the NLB unchanged.
TCP 80 forwards plain HTTP to a dedicated Traefik controller on port 8000,
which routes cert-manager HTTP01 challenge Ingresses. It has no Authentik
route and returns 404 for unmatched paths. This path starts without a TLS Secret.

The NLB has no security group. HTTPS client CIDR filtering happens in nginx.
Nodes allow VPC/NLB traffic to the target ports. The NLB uses TCP health checks;
Kubernetes probes use a separate internal HTTP health endpoint.

nginx runs as non-root with a read-only root filesystem, two replicas and a
PodDisruptionBudget. Increase `nginx_replicas` with Ansible if needed; an HPA and
node autoscaler are not installed. The Load Balancer Controller automatically
registers/deregisters Pod IP targets as replicas change.

## Prerequisites and settings

Use Terraform >= 1.7, Ansible with the collections in `ansible/requirements.yml`,
kubectl, Helm, and AWS CLI. Commands below use **infra-prod** as an example AWS profile name. Replace it
with your locally configured profile name in both Terraform settings and commands;
these examples do not create or rename an AWS CLI profile.
Run Terraform commands from the repository root with `-chdir` as shown;
run Ansible commands from `ansible/` so `ansible.cfg` and inventory paths resolve.

For a new installation, copy `terraform/environments/prod/terraform.tfvars.example`
to `terraform/environments/prod/terraform.tfvars` and set the values below.
For an existing installation, edit its current file instead of overwriting it:

```hcl
aws_profile          = "infra-prod"
authentik_domain     = "auth.example.org"
acme_email           = "admin@example.org"
```

nginx reads its trusted client CA bundle from `pki/ca-bundle.pem`, copied into a
ConfigMap by Ansible. Include only the private CAs authorized to enroll members.

Bootstrap credentials still come from `.env` (`TF_VAR_authentik_bootstrap_email`
and `TF_VAR_authentik_bootstrap_password`). Do not replace an existing state or
regenerate application secrets when updating a deployment.

For a new installation, copy `.env.example` to `.env`, replace its placeholders,
and run `source .env`. Terraform generates the application secret key, database
password and automation token; the operator supplies the bootstrap admin password.

Ansible reads host variables from Terraform state through
`ansible/inventory/terraform.yml`. In particular, `aws_profile` is passed explicitly
to `aws eks update-kubeconfig`; setting `AWS_PROFILE=infra-prod` alone does not override
an inventory value of `infra-dev`. Use the correct account's existing state and match
its `aws_profile` setting. Do not switch accounts by applying an existing state
with a different profile. If using separate checkouts/backends for each account,
ensure the inventory's `project_path` refers to that account's Terraform project.

## HTTP01 validation and DNS

The DNS operator only needs to point `authentik_domain` at `nlb_dns_name`
(CNAME for a subdomain, or the DNS provider's equivalent alias). No hosted-zone
ID, DNS updater role, or cross-account trust is needed. cert-manager obtains the
ACME challenge and publishes the response through temporary solver Pods and
Ingresses in `authentik-edge`, using the dedicated `acme-http` IngressClass.
Only this namespace and class are watched by the HTTP controller.

Public TCP 80 must stay reachable for both initial issuance and renewals;
HTTPS `allowed_cidrs` restrictions do not apply to the validation listener.
Ensure all public A/AAAA answers reach this NLB; remove stale AAAA records if
there is no corresponding IPv6 listener. Any CAA restriction must permit
Let's Encrypt. HTTP01 is for this explicit hostname, not wildcard certificates.

First bootstrap the HTTP path independently of certificate issuance:

```sh
cd ansible
AWS_PROFILE=infra-prod ansible-playbook playbooks/deploy.yml --tags acme-http
```

After Terraform apply and this bootstrap, ask the DNS operator to point the
hostname at the NLB. A plain `http://<hostname>/` should return 404. Then run the
full playbook to install/configure cert-manager, issue the certificate, and start
nginx. The full playbook otherwise waits for Certificate Ready while DNS is wrong.
Keep production and development Terraform states separate when using infra-prod and infra-dev.

For initial testing you may set:

```hcl
acme_server = "https://acme-staging-v02.api.letsencrypt.org/directory"
```

Staging certificates are not browser-trusted. Before production, restore the
production directory (the variable default), apply Terraform and rerun Ansible.
If the certificate has already been issued by staging, explicitly request a new
one with `cmctl renew authentik-web -n authentik-edge`, then wait for issuance
and verify the served issuer before using the endpoint in production.

## New deployment

```sh
source .env
terraform -chdir=terraform/environments/prod init
terraform -chdir=terraform/environments/prod plan
terraform -chdir=terraform/environments/prod apply
cd ansible
ansible-galaxy collection install -r requirements.yml
AWS_PROFILE=infra-prod ansible-playbook playbooks/deploy.yml --tags acme-http
# Point public DNS at the NLB and wait for propagation, then:
AWS_PROFILE=infra-prod ansible-playbook playbooks/deploy.yml
```

Point `authentik_domain`'s DNS CNAME to Terraform's `nlb_dns_name` output before
requesting the first certificate. Startup and
certificate waits default to 1800 seconds (`-e startup_timeout=3600` to adjust).

## Verify the deployment

With public DNS pointing at the NLB, open `/if/flow/cert-onboarding/` with a
trusted private-CA client certificate. Verify new-user enrollment and an
existing user's certificate login. A redirect alone does not prove the flow
completed. Requests without a certificate or with an untrusted certificate
must fail. Check `kubectl get pods -n authentik-edge` and target group health.

## Updating an existing NLB deployment

For nginx templates, the private CA bundle, or Authentik blueprints, rerun Ansible
against the existing inventory; no Terraform apply is needed if its resources and
host variables have not changed:

```sh
cd ansible
AWS_PROFILE=infra-prod ansible-playbook playbooks/deploy.yml
```

Changes to NLB listeners, IAM, EKS, or Terraform-provided host variables require a
Terraform plan/apply first. This includes `authentik_domain`, `acme_email`,
`acme_server`, and `allowed_cidrs`, since Ansible reads them from state.

If deployment failed during the REST API configuration phase, after fixing the
cause it can resume from the port-forward setup using the same profile and extra
variables as the original run:

```sh
cd ansible
AWS_PROFILE=infra-prod ansible-playbook playbooks/deploy.yml \
  --start-at-task "Kill any existing port-forward on port 19000"
```

This skips Helm, ConfigMaps and nginx deployment. Do not use this shortcut for a
blueprint or nginx change that still needs to be deployed.

## Certificate identity and renewal

nginx overwrites `X-Amzn-Mtls-Clientcert-Leaf` with the verified client leaf
certificate.
The flow decodes the URL-encoded PEM using `unquote`, parses X.509 with
`cryptography`, and extracts CN/email from the certificate. It never trusts an
incoming Subject header. Do not expose Authentik's HTTP Service directly to
untrusted clients; direct access would bypass proxy authentication.

The existing private-CA CN format and enrollment gate remain unchanged.
The certificate CA bundle establishes trust; automatic client-certificate
revocation checking is not configured. If revocation is
required, configure CRL/OCSP handling separately before relying on that property.

cert-manager renews the server certificate and updates `authentik-web-tls`.
nginx mounts the complete Secret and ConfigMap volumes (no `subPath`). Its
wrapper checks for updates every 15 seconds **after Kubernetes projects them**,
validates the configuration and gracefully reloads. Failed validation retains
the current workers and retries. Monitor Certificate Ready/expiry and nginx logs.
CA bundle changes are applied by rerunning Ansible and follow the same reload path.

```sh
kubectl get certificate,certificaterequest,order,challenge -n authentik-edge
kubectl describe certificate authentik-web -n authentik-edge
kubectl logs -n cert-manager deployment/cert-manager --tail=100
kubectl logs -n authentik-edge deployment/authentik-edge --tail=100
```

Enrollment on/off remains `-e enrollment_enabled=false` / `true` on `deploy.yml`.
See `kubernetes/authentik/blueprints/credential-enrollment-flow.yaml` for credential
setup; only password prompt/write are bound there, while OTP/passkey stages are
also defined. RADIUS shared-secret configuration remains in its blueprint and
`ansible/playbooks/update-radius-secret.yml`.

## Local verification

Install `tests/requirements.txt` in a virtual environment and run:

```sh
python -m unittest discover -s tests -v
# Docker integration test (downloads nginx image if necessary):
python tests/nginx_integration.py
```

The integration test uses generated disposable certificates, a loopback-published
Docker port and a local mock upstream. It does not contact the EKS cluster.
