# x509-authentik

Private-CA client certificates enroll members into Authentik on AWS EKS.
The certificate CN `1234 Alice Example 01` supplies username `1234` and
name `Alice Example`; an emailAddress RDN supplies the optional email.

```text
Browser with client certificate
  → shared NLB (TCP 443; also serves RADIUS UDP 1812)
  → nginx Deployment (TLS + mandatory mTLS, private CA verification)
  → Authentik Service
  → certificate onboarding → existing account or permitted JIT enrollment
```

Server certificates are issued and renewed by **cert-manager / Let's Encrypt
HTTP-01**. NLB port 80 reaches a dedicated Traefik controller that serves only
cert-manager challenge Ingresses; unmatched paths return 404. No DNS API access,
ACM certificate, or ALB is required. Port 80 must remain reachable for renewal.
nginx reloads projected TLS/CA/config updates without terminating established
connections. Two nginx replicas and a TargetGroupBinding let the AWS Load
Balancer Controller track Pod IP changes. This does not install a Pod or node
autoscaler; replica counts remain explicit.

| Directory | Purpose |
|---|---|
| `terraform/` | VPC, EKS, shared NLB, IAM for AWS controllers |
| `ansible/playbooks/deploy.yml` | Deploy database, Authentik, cert-manager, nginx and flow bindings |
| `ansible/templates/` | nginx configuration, Kubernetes resources, certificate issuer |
| `kubernetes/authentik/blueprints/` | Certificate onboarding, credentials, RADIUS |
| `kubernetes/nginx/run.sh` | Graceful reload on certificate/CA/config rotation |
| `tests/` | Certificate policy and local nginx integration checks |

See [SETUP.md](SETUP.md) for deployment and **staged migration from the existing
ALB**. `retain_legacy_alb=true` preserves the old ALB during verification and DNS
cutover. Only disable it after completing that procedure.

This branch implements private-CA enrollment and authentication only.
The private CA bundle is supplied locally as `pki/ca-bundle.pem`.
JPKI linking and certificate-free login are outside this change.
