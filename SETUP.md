# Deployment and operation

## Native Kubernetes deployment and kind verification

`ansible/playbooks/deploy.yml` deploys the application to an **existing cluster**.
It needs an explicit, dedicated `kubeconfig` and ordinary Ansible variables;
it does not require Terraform state, AWS credentials, EBS, or AWS controllers.
AWS/NLB and native Kubernetes both use `playbooks/deploy.yml`. The inventory
selects environment settings; the Terraform inventory retains the existing AWS behavior.
The common playbook retains the existing Terraform-managed NLB infrastructure;
it does not migrate public routing to an EIP.

### Local kind environment

Start Docker Desktop. Install kind, Helm, kubectl, Ansible and the collections in
`ansible/requirements.yml`. On macOS with Homebrew, use `python3` to create a
virtual environment and install the Python test dependencies. Run from the
repository root, except for the indicated Ansible step:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/requirements.txt
```

The commands below use that interpreter explicitly; activation is not required.
Installing Ansible alone does not install test dependencies into Homebrew's
`python3`. For an existing kind cluster, skip `kind create cluster`.

```sh
kind create cluster --name authentik-dev \
  --config kubernetes/kind/cluster.yaml \
  --kubeconfig /tmp/authentik-kind.kubeconfig
.venv/bin/python tests/prepare_kind.py --kubeconfig /tmp/authentik-kind.kubeconfig
cd ansible
ansible-playbook -i /tmp/authentik-kind/inventory.yml playbooks/deploy.yml \
  -e @/tmp/authentik-kind/vars.json
cd ..
.venv/bin/python tests/kind_integration.py --rollout --restart-db
```

The kind node publishes **localhost TCP 80/443 and UDP 1812** to NodePorts
30080/30443/31812. Those host ports must be free before creating the cluster.
Application Pods do not use `hostPort` or `hostNetwork`; new and old Pods can run
on the same node during a rollout. Kubernetes is pinned in the kind config.
The kubeconfig is separate from EKS; the test scripts also require the explicit
`kind-authentik-dev` context and its named test node.

Install the collections once from `ansible/` with
`ansible-galaxy collection install -r requirements.yml` before running the playbook.

The preparation script generates disposable application secrets and a test CA,
server certificate and client certificates under `/tmp/authentik-kind` with
private file permissions. It provisions only the local TLS Secret. Never use
these credentials in production. Test certificates are short-lived (one day);
for a new test environment, create a fresh state directory with `--state` and
pass the same `--state` to the integration script. Reuse the application settings
for an existing database rather than replacing its secret key or password.

The test uses `auth.kind.test` with process-local resolution to 127.0.0.1;
it does not modify `/etc/hosts` or install a CA into the OS trust store. It checks
mTLS acceptance/rejection, forged identity headers, certificate login, RADIUS
Access-Accept/Access-Reject, shared file persistence, rolling updates and manual
replica changes and PostgreSQL Pod replacement. Transient errors during updates are counted, not treated as a
failure; successful recovery and a new login after the update are required.
The local test runs without Traefik or cert-manager, using `tls_mode: existing_secret`.
The local TLS Secret bypasses public ACME issuance, so **public DNS, HTTP01
issuance/renewal, and real client source-IP preservation need an external test**.
Docker Desktop's port mapping is not a test of AWS's public routing.

The local PostgreSQL has one instance and local-path PVCs. File storage uses a
shared `ReadWriteOnce` PVC on the one node. This verifies Pod replacement, not
node-loss recovery or highly available storage. The `--restart-db` check causes
a database outage and can take several minutes while PostgreSQL shuts down and
Authentik recovers. The test respects the database Pod's termination grace period.
No real user data is copied.

### Open the kind deployment in a browser

The default login card contains username and password on one page and enables
Passkey autofill through the WebAuthn validation stage. Focus the username field
to select a saved passkey in a supporting browser; this path does not require
a username or password. Password fields remain available as an alternative.
The card says “クライアント証明書をお持ちですか？” and its
“こちらからアクセスしてください。” link opens `/if/flow/cert-onboarding/`.
The same entry point handles both first-time enrollment and existing users.
The identification stage runs the password stage inline; its separate flow
binding is removed to avoid a second password page. MFA remains a separate step
when applicable. The old onboarding footer link is removed; other footer links
are preserved.

Client certificates are optional on every public HTTPS route. nginx validates
supplied certificates and strips forged identity headers; certificate-free
requests reach Authentik, where normal application authentication still applies.
An invalid supplied certificate is rejected. RADIUS uses a separate Service.
The test server certificate is for `auth.kind.test`, not `localhost`.

For a command-line check without changing system trust or DNS, run:

```sh
curl --resolve auth.kind.test:443:127.0.0.1 \
  --cacert /tmp/authentik-kind/ca/ca.crt \
  --cert /tmp/authentik-kind/client/client.crt \
  --key /tmp/authentik-kind/client/client.key \
  https://auth.kind.test/if/flow/cert-onboarding/
```

For browser access, add `127.0.0.1 auth.kind.test` to `/etc/hosts`,
trust the test server CA, and package the
client certificate and private key for import. OpenSSL asks for an export password:

```sh
(umask 077; openssl pkcs12 -export \
  -in /tmp/authentik-kind/client/client.crt \
  -inkey /tmp/authentik-kind/client/client.key \
  -certfile /tmp/authentik-kind/ca/ca.crt \
  -name 'kind test user 1001' \
  -out /tmp/authentik-kind/client/client.p12)
```

For Safari/Chrome on macOS, import `client.p12` into the login keychain using
Keychain Access and the export password. Import `ca/ca.crt` as the local test CA
and trust it for SSL during this test. Other browsers may use their own
certificate store. Open `https://auth.kind.test/if/flow/cert-onboarding/` and
select the `1001 Kind Test 01` client certificate when prompted. Restart the
browser if it has cached a connection without a client certificate.

These test certificates expire after one day. Remove the test identity, test CA
trust and hosts entry when finished. Never import or distribute `ca/ca.key`.
Let's Encrypt is not involved in this local browser test.

TLS certificate negotiation happens before the URL is sent. nginx requests an
optional certificate on each connection, so a browser may show a certificate
picker on ordinary pages too. Certificate identity is forwarded only on requests
whose connection supplied it; it is not transferred to other connections or
stored in an application session by nginx. If certificate onboarding fails,
check the executor API request as well as the page request for the certificate.
Access logs include `mtls=SUCCESS`, `NONE`, or a verification failure. Certificate
onboarding also requires fresh flow planning; the deployment disables flow and
policy caches to prevent reuse of another request's certificate decision.

### Deploy the same application elsewhere

Copy `ansible/native-inventory.example.yml` outside the auto-loaded inventory
directory. Set the explicit kubeconfig, domain, StorageClass and client CA path.
Supply application secrets using Ansible Vault or a separate protected variables
file, then run from `ansible/`:

```sh
ansible-playbook -i /path/to/native-inventory.yml playbooks/deploy.yml \
  -e @/path/to/application-secrets.yml
```

Required secrets are `authentik_secret_key`, `authentik_pg_password`,
`authentik_bootstrap_password`, `authentik_bootstrap_email`, `authentik_api_token`
and, when native RADIUS is enabled, `radius_shared_secret` (at least 16 characters).
`radius_client_networks` must cover the intended RADIUS clients; also restrict UDP
1812 at the network boundary. Native RADIUS is deployed explicitly with its own
outpost token, Deployment and Service. Existing managed outposts must be detached
from their service connection before selecting this native deployment.
Blueprints containing the RADIUS secret are mounted from a Kubernetes Secret.

The principal configuration inputs are:

| Variable | Purpose |
|---|---|
| `service_type` | `NodePort` (default), `ClusterIP`, or `LoadBalancer` supplied by the environment |
| `http_node_port`, `https_node_port`, `radius_node_port` | Defaults 30080, 30443, 31812 |
| `external_traffic_policy` | Defaults to `Local` to preserve source addresses |
| `edge_node_selector` | Place nginx, HTTP controller and RADIUS on nodes receiving external traffic |
| `proxy_protocol_enabled`, `proxy_protocol_trusted_cidrs` | Optional PROXY protocol, only from explicitly trusted proxies; disabled by default |
| `allowed_cidrs_json` | HTTPS client CIDRs, encoded as a JSON list |
| `storage_class`, `postgres_instances`, `postgres_storage_size` | CNPG storage and instance count; class must already exist |
| `postgres_mode` | `cnpg` (default) or `external` |
| `tls_mode` | `existing_secret` (default) or `acme` |
| `media_pvc_enabled`, `media_storage_class`, `media_access_modes` | Optional persistent `/data` volume shared by server and worker |
| `authentik_helm_overrides` | Additional Helm values, including replicas, HPA and placement |

With `Local`, each node receiving traffic must have a ready local endpoint for
that Service. On one node this happens naturally. On multiple nodes, configure
placement and the upstream routing together. `Cluster` can forward to endpoints
on other nodes but may replace the source address; do not use that mode blindly
with nginx CIDR filtering or RADIUS client-network restrictions.

`LoadBalancer` does not itself provide a public IP in an arbitrary cluster. The
infrastructure must provide the implementation. NodePort also needs the external
network to route/forward the standard public ports to the selected node ports.
For EKS, allocating an EIP alone is insufficient: associating it with a routable
node and delivering 80/443/1812 to the Services remain infrastructure work. This
branch's native application path does not remove the existing NLB or switch DNS.
Use `deploy.yml` with the Terraform inventory for the live NLB deployment until
the new route and cutover are ready: native nginx expects plain TLS, while the current NLB
sends PROXY protocol. Applying native defaults to that live listener would
break the old route.

For optional production ACME, set `tls_mode: acme`, `acme_email` and `acme_server`, route public TCP 80 to
30080 (or the HTTP Service), and route TCP 443 to 30443 (or the nginx Service).
The HTTP-only controller serves challenge Ingresses and returns 404 otherwise.
The default `existing_secret` mode installs neither Traefik nor cert-manager and
needs no public HTTP port. Provision the server certificate and key before deploying:

```sh
kubectl --kubeconfig /path/to/dedicated-kubeconfig create namespace authentik-edge
kubectl --kubeconfig /path/to/dedicated-kubeconfig -n authentik-edge create secret tls authentik-web-tls \
  --cert=/path/to/server-fullchain.pem --key=/path/to/server.key
```

Use a certificate valid for `authentik_domain`; clients must trust its issuer.
nginx accepts optional, verified client certificates on all public HTTPS routes. Renewal of a supplied server
certificate is the operator's responsibility. Switching to `existing_secret`
skips ACME provisioning but does not delete previously installed controllers or
Certificate resources; retire those separately if changing an existing deployment.
The Terraform inventory defaults to `tls_mode: acme` in
`ansible/inventory/group_vars/x509-authentik.yml`. It also selects NLB integration,
gp3 storage, PROXY protocol, and the dedicated EKS kubeconfig. External native
inventories do not load these settings. All environments use `deploy.yml`.

For an external/managed PostgreSQL database, set `postgres_mode: external`,
`postgres_host`, `postgres_port`, `postgres_database`, `postgres_user`,
`authentik_pg_password` and the provider's required `postgres_sslmode`. Provision
the database, role and CA configuration separately. CNPG and its credential
Secret/PVCs are not created in this mode. This is a connection configuration,
not a data migration from an existing CNPG database.

For uploads, enable the media PVC or supply an existing shared backend through
Helm overrides. `ReadWriteOnce` permits multiple Pods on **one node**, not general
multi-node scaling. Use an RWX-capable StorageClass (or shared object storage)
for server/worker replicas across nodes. `media_pvc_enabled` defaults to false
so an existing deployment's storage is not silently replaced. PostgreSQL and
file backups/restores must be designed for the selected storage provider.

Example HPA override (requires a working metrics API; not installed here):

```yaml
authentik_helm_overrides:
  server:
    autoscaling:
      enabled: true
      minReplicas: 1
      maxReplicas: 3
      targetCPUUtilizationPercentage: 70
  worker:
    autoscaling:
      enabled: true
      minReplicas: 1
      maxReplicas: 2
      targetCPUUtilizationPercentage: 70
```

Choose replica limits against PostgreSQL connection capacity and the available
storage/node capacity. HPA does not provision EC2 instances. The native tests
exercise manual scaling; automatic HPA decisions need a metrics-enabled test.

## Existing AWS/NLB architecture

EKS nodes use Graviton Spot instances (`t4g.medium` / `t4g.large`) with the
`AL2023_ARM_64_STANDARD` AMI. Any additional container images must support ARM64.

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
With the Terraform inventory, the common playbook writes to `~/.kube/x509-authentik-eks`, not the default
kubeconfig. For the manual kubectl commands below, explicitly select that file:

```sh
export KUBECONFIG="$HOME/.kube/x509-authentik-eks"
```

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
completed. Without a certificate, the normal login page must remain available,
but certificate onboarding must not authenticate the user. An untrusted supplied
certificate must be rejected. Check `kubectl get pods -n authentik-edge` and
target group health.

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
  --start-at-task "Check local API forwarding port is unused"
```

This requires the deployment's dedicated kubeconfig to already exist and its
`api_forward_port` to be free. It allocates and cleans up its own port-forward;
it does not stop unrelated processes. It skips Helm, blueprint Secrets and nginx
deployment. Do not use this shortcut for a blueprint or nginx change that still
needs to be deployed.

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
the current workers and retries. Old workers drain for up to 45 seconds, after
which long-lived connections may need to reconnect. Monitor Certificate
Ready/expiry and nginx logs.
CA bundle changes are applied by rerunning Ansible and follow the same reload path.

```sh
kubectl get certificate,certificaterequest,order,challenge -n authentik-edge
kubectl describe certificate authentik-web -n authentik-edge
kubectl logs -n cert-manager deployment/cert-manager --tail=100
kubectl logs -n authentik-edge deployment/authentik-edge --tail=100
```

Enrollment on/off remains `-e enrollment_enabled=false` / `true` on the selected
deployment entry point.
Certificate onboarding requires a password for new users and users who have not
finished password setup. No authenticated session is created before it is saved.
Users who already have a password keep it. Next, the onboarding card offers
Passkey (selected by default), an authenticator app (TOTP), or “今回はスキップ”. Registration is optional;
when TOTP is registered, ordinary Web password login requests the second factor.
RADIUS PAP continues to use the password. Additional authenticators can be added
from user settings. Passkeys are scoped to the hostname used during registration,
so a passkey registered on `auth.kind.test` is for that test site.
See `kubernetes/authentik/blueprints/onboarding-flow.yaml` for the stages and
`ansible/roles/authentik/tasks/configure-api.yml` for their policy bindings.
`credential-enrollment-flow.yaml` remains the authenticated password setup flow. Native RADIUS settings are rendered through
`ansible/templates/radius-blueprint.yaml.j2`. Supply its shared secret through
the protected Ansible variables file. The legacy environment also has
`ansible/playbooks/update-radius-secret.yml`.

## Local verification

Create the `.venv` environment and install `tests/requirements.txt` as shown above, then run from the repository root:

```sh
.venv/bin/python -m unittest discover -s tests -v
# Docker integration test (downloads nginx image if necessary):
.venv/bin/python tests/nginx_integration.py
```

The integration test uses generated disposable certificates, a loopback-published
Docker port and a local mock upstream. It does not contact the EKS cluster.

Certificate onboarding evaluates request-specific certificate headers and sets
the pending user during flow planning. Flow-plan and policy-result caches are
disabled (`authentik.cache.timeout_flows: 0`, `timeout_policies: 0`) so a previous
certificate-free request cannot supply an empty plan to a certificate login.
This affects all flows/policies and trades caching for correct request evaluation.

The public `kind_integration.py` test expects the generated test client CA to
remain trusted. If you switch `client_ca_bundle_path` to your real CA for browser
testing, that script's generated client certificates will correctly be rejected.
To test the onboarding state machine while kind trusts your real client CA:

```sh
.venv/bin/python tests/kind_onboarding.py
```

This test opens a private port-forward to the dedicated kind cluster and simulates
nginx's verified certificate header. It creates and removes temporary test users,
checks mandatory password setup, optional skip/TOTP registration, and the Passkey
registration challenge. It does not test TLS verification or actual hardware
Passkey registration; verify those through the browser.

The login card labels the certificate enrollment link as
“クライアント証明書をお持ちですか？ こちらからアクセスしてください。”
The scoped brand CSS is managed in
`ansible/roles/authentik/files/certificate-login.css`; unrelated brand CSS is
preserved. This is a display override for Authentik 2026.8's fixed enrollment
wording, so check the link display when upgrading Authentik.
