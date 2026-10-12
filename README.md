# x509-authentik

Private-CA client certificates enroll members into Authentik on Kubernetes.
The same application deployment runs on kind and EKS; AWS infrastructure is separate.
The certificate CN `1234 Alice Example 01` supplies username `1234` and
name `Alice Example`; an emailAddress RDN supplies the optional email.

```text
Browser with client certificate
  → environment-provided public endpoint
  → Kubernetes Service (NodePort by default; optional external LoadBalancer)
  → nginx Deployment (TLS + optional client certificates)
  → Authentik Service
  → certificate onboarding → existing account or permitted JIT enrollment
```

Server certificates come from a supplied Kubernetes TLS Secret by default
(`tls_mode: existing_secret`). Local kind tests generate a test CA and need no
Let's Encrypt account, public DNS, or ACME controllers. Optionally select
`tls_mode: acme` for cert-manager / Let's Encrypt HTTP-01 issuance and renewal.
Only that mode installs the dedicated Traefik HTTP controller and cert-manager;
public port 80 must remain reachable for renewal.
nginx gracefully reloads projected TLS/CA/config updates, allowing old workers
to drain for up to 45 seconds. Services track Pod replacements, including rolling updates on one
node without binding host ports in application Pods. Brief errors and login
restarts during updates are acceptable; updates must recover successfully.
Authentik Helm overrides can enable HPA; a metrics provider and node autoscaler
are infrastructure responsibilities. PostgreSQL can run under CloudNativePG or
be supplied externally. Redis is not used.

| Directory | Purpose |
|---|---|
| `terraform/` | VPC, EKS, shared NLB, IAM for AWS controllers |
| `ansible/playbooks/deploy.yml` | Common Kubernetes application deployment, using an explicit kubeconfig |
| `ansible/inventory/group_vars/x509-authentik.yml` | AWS/NLB defaults for the Terraform inventory; uses the same `deploy.yml` |
| `ansible/roles/authentik/` | Shared deployment defaults and tasks |
| `kubernetes/kind/` | Single-node local verification, with TCP/UDP port mappings |
| `ansible/templates/` | nginx configuration, Kubernetes resources, certificate issuer |
| `kubernetes/authentik/blueprints/` | Certificate onboarding and credential flows |
| `kubernetes/nginx/run.sh` | Graceful reload on certificate/CA/config rotation |
| `tests/` | Template/policy tests, Docker nginx tests, and full kind integration checks |

See [SETUP.md](SETUP.md) for deployment and operation.

The current configuration implements private-CA enrollment and authentication only.
The private CA bundle is supplied locally as `pki/ca-bundle.pem`.
Client certificates are optional on all public HTTPS routes. nginx validates any
supplied certificate and replaces client-supplied identity headers. Without a
certificate, users can use ordinary username/password or registered Passkey login. Certificate-based
onboarding requires verified identity in the request processed by its policy;
nginx does not carry that identity between browser connections. RADIUS password
authentication uses its own Service.
TLS 1.2 and TLS 1.3 are enabled.

Certificate onboarding requires password setup before login for users without a
password. It then offers optional Passkey or TOTP registration, including a skip
choice. Passkey is selected by default. Existing passwords are preserved.
