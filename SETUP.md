% infra-x509-authentik Setup Guide
% Infrastructure Team
% 2026-10-02
%% AI: Claude Sonnet 4.6 (33%)

# x509-authentik Setup Guide

X.509 client certificate → Authentik JIT provisioning on AWS EKS.

## Prerequisites

Tools required on the operator workstation:

- `terraform` >= 1.7
- `ansible` + `kubernetes.core` collection
- `kubectl`
- `helm` >= 3
- `aws` CLI, profile `ANT` configured

Non-sensitive deployment values go in `terraform.tfvars` (gitignored):

```sh
cp terraform/environments/prod/terraform.tfvars.example \
   terraform/environments/prod/terraform.tfvars
# edit: set authentik_domain, ca_bundle_s3_bucket, aws_profile, etc.
```

Sensitive values are passed via environment variables.
Copy `.env.example` to `.env`, fill in, then load before `terraform apply`:

```sh
cp .env.example .env
# edit .env
source .env
```

| Variable | Purpose |
|----------|---------|
| `TF_VAR_authentik_bootstrap_email` | Email for the initial akadmin account |
| `TF_VAR_authentik_bootstrap_password` | Password for the initial akadmin account |

All other secrets (Authentik secret key, PostgreSQL password, API token)
are generated automatically by Terraform `random_password` resources and stored in
Terraform state. Ansible reads them via the `cloud.terraform.terraform_provider`
inventory plugin — no additional environment variables are required at the Ansible phase.

## CA Bundle

Place your CA certificate(s) (PEM format, concatenated if multiple) at:

```
pki/ca-bundle.pem
```

This file is listed in `.gitignore` and must not be committed.
`pki/ca-bundle-sample.pem` contains a dummy self-signed certificate as a format reference.

Terraform uploads `pki/ca-bundle.pem` to S3 on `apply`, which populates the ALB Trust Store
used for mTLS client certificate verification.

## Deploy

### 1. Terraform

```sh
cd terraform/environments/prod
terraform init
terraform apply
```

After `apply` completes, retrieve the two DNS values needed for the next steps:

```sh
terraform output acm_validation_cname   # add to Route 53 to validate the ACM certificate
terraform output alb_dns_name           # CNAME target for auth.example.org
```

Other outputs consumed automatically by Ansible:

| Output | Purpose |
|--------|---------|
| `vpc_id` | Used by ALB Controller Helm values |
| `albc_role_arn` | IAM role for AWS Load Balancer Controller |
| `alb_target_group_arn` | Bound to authentik-server via TargetGroupBinding |

### 2. DNS  *(manual)*

Add two records in the `example.org` Route 53 zone (separate AWS account):

| Record | Type | Value |
|--------|------|-------|
| ACM validation name from `terraform output acm_validation_cname` | CNAME | ACM validation value from same output |
| `auth.example.org` | CNAME | `terraform output alb_dns_name` |

Wait for certificate status to become `ISSUED` before proceeding:

```sh
aws acm describe-certificate \
  --certificate-arn <arn> \
  --query 'Certificate.Status'
```

### 3. Ansible

```sh
cd ansible
ansible-galaxy collection install -r requirements.yml  # first run only
AWS_PROFILE=ANT ansible-playbook playbooks/deploy.yml
```

`ANT-cached` can be substituted for `ANT` if already authenticated within the session duration.

Terraform outputs (`vpc_id`, `albc_role_arn`, `alb_target_group_arn`, secrets, etc.) are
injected automatically via the `cloud.terraform.terraform_provider` inventory plugin
(see `ansible/inventory/terraform.yml`).

The playbook:

1. Creates namespaces, StorageClass, CloudNativePG cluster
2. Installs AWS Load Balancer Controller, nginx Ingress
3. Installs Authentik (Helm) with Redis and CloudNativePG backend
4. Creates `authentik-blueprints` ConfigMap from `kubernetes/authentik/blueprints/`
5. Restarts `authentik-worker`; worker applies blueprints from the ConfigMap on startup:
   - `onboarding-flow.yaml` — cert-based enrollment flow
   - `credential-enrollment-flow.yaml` — password/OTP/passkey enrollment
   - `radius-outpost.yaml` — RADIUS outpost
6. Post-blueprint REST API steps (idempotent, via `kubectl port-forward`):
   - Sets default brand domain (suppresses base URL warning)
   - Removes legacy `cert-onboarding-parse-cert-policy` if present
   - Sets `evaluate_on_plan=True` on FlowStageBinding order=20
   - Binds `cert-onboarding-lookup-policy` (order=0) to FlowStageBinding
   - Binds `cert-onboarding-create-policy` (order=1) with `enabled` matching `enrollment_enabled`
   - Binds `cert-onboarding-new-user-gate` to FlowStageBindings order=30 and order=40
   - Enables `cert-enrollment-notify` NotificationRule with superuser group
   - Creates `cert-users` group

## Architecture

```
Browser (X.509 client certificate)
  → ALB (mTLS verify against client CA trust store)
      → nginx Ingress
          → Authentik server
              → /if/flow/cert-onboarding/
                  → cert-onboarding-lookup-policy  (order=0, always enabled)
                      parse X-Amzn-Mtls-Clientcert-Subject header
                      look up existing user by username → set pending_user
                  → cert-onboarding-create-policy  (order=1, enrollment gate)
                      if pending_user already set → pass through
                      otherwise JIT-provision new user from CN, add to cert-users
                      notify admins (in-app bell + email if SMTP configured)
                  → UserLoginStage → session created
```

Policy engine mode is ANY: the stage runs if either policy passes.
If both fail (no cert header, or create policy disabled and user unknown) the flow is denied.

ALB mTLS header format (URL-encoded RFC 4514):

```
X-Amzn-Mtls-Clientcert-Subject: C%3DXX%2CO%3DExample+Org%2CCN%3D1234+Alice+Example+01%2CemailAddress%3Duser%40example.org
```

Decoded: `C=JP,O=client CA,CN=1234 Alice Example 01,emailAddress=user@example.org`

CN parsing: `1234 Alice Example 01` → username=`1234`, name=`Alice Example`
(first token = member number, trailing numeric suffix stripped)

New users get `attributes: {enrollment_source": "x509"}` set at creation time,
visible under **Directory → Users → \<user\> → Overview → Attributes**.

## Enrollment On/Off

### GUI (recommended)

**Flows and Stages → Flows → Certificate Onboarding → Stage Bindings**

Under the `cert-onboarding-user-login` stage, find the `cert-onboarding-create-policy`
binding (order=1) and click **Edit Binding**:

- **Enabled ON** — new users with a valid X.509 client certificate are provisioned automatically
- **Enabled OFF** — only existing users can log in; unknown certs are denied

### Ansible

```sh
# Disable enrollment (uses REST API — no pod exec, no OOM risk)
AWS_PROFILE=ANT ansible-playbook playbooks/deploy.yml \
  -e enrollment_enabled=false

# Re-enable enrollment
AWS_PROFILE=ANT ansible-playbook playbooks/deploy.yml \
  -e enrollment_enabled=true
```

## Enrollment Flow

User visits: `https://auth.example.org/if/flow/cert-onboarding/`

On success for **new users**: password setup prompt is shown inline (skippable).
After that, or for **returning users**: Authentik Application Dashboard is shown.

To set up or update credentials later: visit `/if/flow/credential-enrollment/`
(password, OTP, passkey).

## CA Replacement

CA trust store is managed in ACM (`aws acm-pca` or console). The ALB annotation
`alb.ingress.kubernetes.io/mutual-authentication` references the trust store ARN
in `kubernetes/ingress/authentik-alb-ingress.yaml`. Update the ARN and reapply
the Ingress if the trust store changes; no Authentik changes required.

## Teardown

```sh
cd terraform/environments/prod
terraform destroy
```

## Notes

- **Authentik version**: 2026.8.3 — Expression Stage was removed; Expression
  Policy is used instead. `FlowStageBinding.evaluate_on_plan=True` is required
  for the policy to write `pending_user` into the live FlowPlan context.
- **PolicyBinding in blueprints**: Blueprint two-phase validation causes UUID
  mismatches for PolicyBindings; they are created via Ansible REST API calls instead.
- **RADIUS**: shared secret is set in `kubernetes/authentik/blueprints/radius-outpost.yaml`;
  change `CHANGE_ME_RADIUS_SECRET` before deploying.
