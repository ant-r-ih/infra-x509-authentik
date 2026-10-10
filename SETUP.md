# Deployment and ALB migration

## Architecture

Terraform retains the existing `aws_lb.radius` resource and adds TCP 443 to it.
The NLB uses IP targets and PROXY protocol v2. nginx terminates TLS/mTLS on 8443,
checks the client certificate against the private CA bundle, applies
`allowed_cidrs` to the original address, and proxies to Authentik. TCP 443 passes
through the NLB unchanged; there is no NLB TLS/ACM listener.
TCP 80 forwards plain HTTP to a dedicated Traefik controller on port 8000,
which routes cert-manager HTTP01 challenge Ingresses. It has no Authentik
route and returns 404 for unmatched paths. This path starts without a TLS Secret.

The NLB was originally created without a security group. This migration keeps
its identity instead of replacing it to attach one. HTTPS client CIDR filtering
therefore happens in nginx. Nodes allow the VPC/NLB traffic to the target port;
the NLB uses TCP health checks. Kubernetes probes use a separate internal HTTP
health endpoint. Existing RADIUS target registration and access policy are not
changed by this migration.

nginx runs as non-root with a read-only root filesystem, two replicas and a
PodDisruptionBudget. Increase `nginx_replicas` with Ansible if needed; an HPA and
node autoscaler are not installed. The Load Balancer Controller automatically
registers/deregisters Pod IP targets as replicas change.

## Prerequisites and settings

Use Terraform >= 1.7, Ansible with the collections in `ansible/requirements.yml`,
kubectl, Helm, and AWS CLI. Commands below use AWS profile **ANT**.

Copy `terraform/environments/prod/terraform.tfvars.example` to
`terraform.tfvars` and set the existing infrastructure values plus:

```hcl
aws_profile          = "ANT"
authentik_domain     = "auth.example.org"
acme_email           = "admin@example.org"
retain_legacy_alb    = false # NEW INSTALLS ONLY; existing ALB: start with true
```

For an existing installation, retain the original `ca_bundle_s3_bucket` and
`ca_bundle_s3_key` values. The versioned S3 archive remains managed even after
the ALB is removed; it is not force-deleted. New installations leave the bucket
variable empty and create no CA archive in S3. nginx reads its CA bundle from
`pki/ca-bundle.pem`, copied into a ConfigMap by Ansible. This file must contain
only the private CAs authorized to enroll members, not JPKI roots.

Bootstrap credentials still come from `.env` (`TF_VAR_authentik_bootstrap_email`
and `TF_VAR_authentik_bootstrap_password`). Do not replace an existing state or
regenerate application secrets during migration.

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
AWS_PROFILE=ANT ansible-playbook playbooks/deploy.yml --tags acme-http
```

After Terraform apply and this bootstrap, ask the DNS operator to point the
hostname at the NLB. A plain `http://<hostname>/` should return 404. Then run the
full playbook to install/configure cert-manager, issue the certificate, and start
nginx. The full playbook otherwise waits for Certificate Ready while DNS is wrong.
Keep production and development Terraform states separate when using ANT and ikob.

If this branch's earlier DNS01 configuration was already applied, Terraform
will remove its cert-manager IAM role/policy. The DNS-account updater role is
outside this state; arrange separate cleanup if it was created. The updated
cert-manager Helm values remove its IRSA annotation. Do not uninstall cert-manager
or delete the existing TLS Secret during this transition.

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
AWS_PROFILE=ANT ansible-playbook playbooks/deploy.yml --tags acme-http
# Point public DNS at the NLB and wait for propagation, then:
AWS_PROFILE=ANT ansible-playbook playbooks/deploy.yml
```

Point `authentik_domain`'s DNS CNAME to Terraform's `nlb_dns_name` output before
requesting the first certificate. Startup and
certificate waits default to 1800 seconds (`-e startup_timeout=3600` to adjust).

## Existing ALB: staged migration

1. Back up the Terraform state and existing DNS record. Set
   **`retain_legacy_alb=true`** and the ACME email before planning. `moved`
   blocks preserve existing ALB/ACM/S3 resource identities when adding counts.
   Confirm the plan retains the EKS cluster, node group, database volumes,
   shared RADIUS NLB and existing ALB. This branch does not switch to ARM.
2. Apply Terraform, then bootstrap HTTP with `--tags acme-http` as above.
   If the hostname still points to the ALB, HTTP01 cannot validate through the
   NLB yet. Schedule an initial issuance window: switch DNS, run the full playbook,
   and wait for certificate issuance and nginx rollout. During this window HTTPS
   on the new NLB may be unavailable. Retain the ALB for DNS rollback. For a
   zero-downtime ALB migration, a separately planned HTTP challenge forwarding
   path or an already valid TLS Secret is required.
3. Verify new NLB access with a private-CA client certificate and its private key:

   ```sh
   curl --connect-to auth.example.org:443:<nlb-dns-name>:443 \
     --cert /path/to/client.pem --key /path/to/client.key \
     https://auth.example.org/if/flow/cert-onboarding/
   ```

   A request without a certificate or with an untrusted certificate must fail. Check `kubectl get pods -n authentik-edge` and target health.
   Verify real browser enrollment and an existing user's certificate login;
   an HTTP redirect alone is not proof that the Authentik flow succeeds.
4. Keep the public DNS CNAME at `nlb_dns_name`. Keep the ALB through the old
   DNS TTL and complete application checks. To roll back at this stage, point
   DNS back to `legacy_alb_dns_name`; both paths use the same Authentik database.
5. **Before deleting the ALB target group**, remove its Kubernetes binding so
   its finalizer can deregister targets while AWS resources still exist:

   ```sh
   AWS_PROFILE=ANT kubectl delete targetgroupbinding authentik-server -n authentik
   ```

6. Set `retain_legacy_alb=false`, review `terraform plan`, then apply. This removes
   the legacy ALB, listener, target group, trust store and ACM certificate. It
   retains the versioned S3 archive. Do not remove the archive bucket variable
   casually: S3 object versions require separate deliberate cleanup.
7. After cutover, remove the retired community ingress-nginx release and unused
   manifests from the live cluster (the new Deployment uses a different namespace):

   ```sh
   helm uninstall ingress-nginx -n ingress-nginx
   kubectl delete ingress authentik -n authentik --ignore-not-found
   kubectl delete configmap custom-proxy-headers -n ingress-nginx --ignore-not-found
   ```

These cleanup commands are intentionally separate from routine deployment.
After step 6, rollback requires recreating the ALB/ACM and restoring its binding.

## Certificate identity and renewal

nginx overwrites `X-Amzn-Mtls-Clientcert-Leaf` with the verified client leaf
certificate. The legacy header name lets ALB and nginx coexist during cutover.
The flow decodes the URL-encoded PEM using `unquote`, parses X.509 with
`cryptography`, and extracts CN/email from the certificate. It never trusts an
incoming Subject header. Do not expose Authentik's HTTP Service directly to
untrusted clients; direct access would bypass proxy authentication.

The existing private-CA CN format and enrollment gate remain unchanged.
JPKI linking and certificate-free login are not part of this configuration.
The certificate CA bundle establishes trust; automatic client-certificate
revocation checking is not configured by this migration. If revocation is
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
