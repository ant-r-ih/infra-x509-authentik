% infra-x509-authentik
% Infrastructure Team
% 2026-10-02
%% AI: Claude Sonnet 4.6 (33%)

# x509-authentik

Authenticate client CA members via X.509 client certificates into
[Authentik](https://goauthentik.io/) on AWS EKS — no passwords at enrollment,
just present your X.509 client certificate.

## How it works

```
Client browser (X.509 client certificate)
  │
  ▼
AWS ALB  ──  mTLS verify against client CA trust store
  │          adds X-Amzn-Mtls-Clientcert-Subject header
  ▼
nginx Ingress → Authentik (auth.example.org)
  │
  ▼
/if/flow/cert-onboarding/
  │
  ├── ExpressionPolicy (evaluate_on_plan)
  │     • gate: cert-create PolicyBinding enabled check
  │     • parse CN  →  username / full name / email
  │     • JIT-provision user, add to "cert-users" group
  │
  └── UserLoginStage  →  session established
```

CN format: `1234 Alice Example 01`
→ username `1234`, name `Alice Example`

After enrollment, the member can visit `/if/flow/credential-enrollment/`
to register a password, TOTP, or passkey for subsequent logins, and use
the RADIUS outpost for network authentication.

## Enrollment gate

Enrollment is on when the cert-create PolicyBinding is enabled (see Enrollment On/Off),
off when it is deleted. No flow changes needed. See [SETUP.md](SETUP.md) for the
one-liner commands.

## Stack

| Layer | Component |
|-------|-----------|
| Cloud | AWS EKS (Terraform) |
| Ingress | AWS ALB (mTLS) + nginx |
| Identity | Authentik 2026.8.3 |
| Database | CloudNativePG (PostgreSQL) |
| Cache | Redis |

## Repository layout

```
terraform/          Infrastructure (EKS, ALB, ACM, mTLS trust store)
kubernetes/
  authentik/
    blueprints/     Authentik flow blueprints (applied by Ansible)
    values.yaml     Helm values
  ingress/          ALB + nginx Ingress manifests
ansible/
  playbooks/
    deploy.yml      End-to-end deploy playbook
```

## Getting started

See **[SETUP.md](SETUP.md)** for full deployment steps including the one manual
step (ACM certificate DNS validation).
