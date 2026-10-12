"""Create isolated local test credentials and TLS secret (never uses real PKI)."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess

from nginx_integration import issue, write_pair

parser = argparse.ArgumentParser()
parser.add_argument('--kubeconfig', required=True, type=Path)
parser.add_argument('--state', type=Path, default=Path('/tmp/authentik-kind'))
args = parser.parse_args()
os.umask(0o077)
root = args.state.resolve()
root.mkdir(parents=True, exist_ok=True)
kube = ['kubectl', '--kubeconfig', str(args.kubeconfig.resolve()), '--context', 'kind-authentik-dev']
# Explicit context prevents accidentally provisioning test credentials in EKS.
subprocess.run(kube + ['get', 'node', 'authentik-dev-control-plane'], check=True, stdout=subprocess.DEVNULL)
if not (root / 'vars.json').exists():
    ca = issue('kind-only test CA')
    write_pair(root / 'ca', 'ca', ca)
    write_pair(root / 'tls', 'tls', issue('auth.kind.test', ca))
    write_pair(root / 'client', 'client', issue('1001 Kind Test 01', ca, True))
    wrong = issue('untrusted kind CA')
    write_pair(root / 'wrong', 'client', issue('9999 Untrusted 01', wrong, True))
    config = dict(
        kubeconfig=str(args.kubeconfig.resolve()), authentik_domain='auth.kind.test',
        client_ca_bundle_path=str(root / 'ca/ca.crt'), tls_mode='existing_secret',
        storage_class='standard', postgres_instances=1, postgres_storage_size='2Gi',
        media_pvc_enabled=True, media_access_modes=['ReadWriteOnce'],
        api_forward_port=19001, radius_enabled=True,
        authentik_secret_key=secrets.token_hex(32), authentik_pg_password=secrets.token_hex(24),
        authentik_bootstrap_password=secrets.token_hex(24),
        authentik_bootstrap_email='admin@kind.test', authentik_api_token=secrets.token_hex(32),
        radius_shared_secret=secrets.token_hex(24), radius_client_networks='0.0.0.0/0',
    )
    (root / 'vars.json').write_text(json.dumps(config, indent=2)+'\n')
(root / 'inventory.yml').write_text('all:\n  children:\n    x509-authentik:\n      hosts:\n        kind-local:\n          ansible_connection: local\n')
objects = [dict(apiVersion='v1', kind='Namespace', metadata=dict(name='authentik-edge')),
           dict(apiVersion='v1', kind='Secret', metadata=dict(name='authentik-web-tls', namespace='authentik-edge'),
                type='kubernetes.io/tls', stringData={
                    'tls.crt': (root / 'tls/tls.crt').read_text(),
                    'tls.key': (root / 'tls/tls.key').read_text()})]
subprocess.run(kube + ['apply', '-f', '-'], input=json.dumps(dict(apiVersion='v1', kind='List', items=objects)), text=True, check=True)
print(f'Local inventory: {root / "inventory.yml"}')
print(f'Local settings: {root / "vars.json"} (contains test secrets; do not commit)')
