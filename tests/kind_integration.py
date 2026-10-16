"""End-to-end checks through kind's published HTTPS port, never port-forward.

Uses only the throwaway cluster and credentials from prepare_kind.py.
"""
import argparse
import asyncio
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import asyncssh
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'kubernetes/ssh-demo'))
from radius_client import access_request  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument('--state', type=Path, default=Path('/tmp/authentik-kind'))
parser.add_argument('--rollout', action='store_true')
parser.add_argument('--restart-db', action='store_true')
args = parser.parse_args()
root = args.state.resolve()
config = json.loads((root / 'vars.json').read_text())
kube = ['kubectl', '--kubeconfig', config['kubeconfig'], '--context', 'kind-authentik-dev']
subprocess.run(kube + ['get', 'node', 'authentik-dev-control-plane'], check=True, stdout=subprocess.DEVNULL)
resolve = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **kw: resolve('127.0.0.1' if host == 'auth.kind.test' else host, *a, **kw)
base = 'https://auth.kind.test'

crds = json.loads(subprocess.check_output(kube + ['get', 'crd', '-o', 'json']))
assert not any('elbv2.k8s.aws' in c['metadata']['name'] for c in crds['items'])
pods = json.loads(subprocess.check_output(kube + ['get', 'pods', '-A', '-o', 'json']))
images = [c['image'] for p in pods['items'] for c in p['spec']['containers']]
assert not any('redis' in image or 'aws-load-balancer-controller' in image or 'aws-ebs-csi-driver' in image for image in images)
print('PASS: cluster runs without Redis, AWS load balancer CRDs/controller, or EBS driver')

def session(cert='client'):
    s = requests.Session()
    s.trust_env = False
    s.verify = str(root / 'ca/ca.crt')
    if cert:
        s.cert = (str(root / cert / 'client.crt'), str(root / cert / 'client.key'))
    return s

if config.get('tls_mode', 'existing_secret') == 'acme':
    with requests.Session() as http:
        http.trust_env = False
        r = http.get('http://127.0.0.1/', headers={'Host': 'auth.kind.test'}, timeout=10)
        assert r.status_code == 404, r.status_code
    print('PASS: published HTTP port serves no unauthenticated application route')
else:
    assert not any('traefik' in image or 'cert-manager' in image for image in images)
    assert not any('cert-manager.io' in c['metadata']['name'] for c in crds['items'])
    services = json.loads(subprocess.check_output(kube + ['get', 'services', '-A', '-o', 'json']))
    assert not any(p.get('nodePort') == 30080 for svc in services['items'] for p in svc['spec'].get('ports', []))
    print('PASS: existing TLS Secret works without ACME controllers, CRDs, or HTTP NodePort')

admin = session()
admin.headers['Authorization'] = 'Bearer ' + config['authentik_api_token']
r = admin.get(base + '/api/v3/core/users/me/', timeout=15)
assert r.status_code == 200, (r.status_code, r.text)
assert r.json()['user']['username'] == 'akadmin', r.text
print('PASS: HTTPS -> nginx -> Authentik API with mTLS')
for cert in ['wrong']:
    try:
        r = session(cert).get(base + '/api/v3/flows/executor/cert-onboarding/', timeout=10)
        assert r.status_code in (400, 403), r.status_code
    except requests.exceptions.SSLError:
        pass
    print('PASS: untrusted client certificate rejected')

with session(None) as public:
    public.headers['X-Amzn-Mtls-Clientcert-Leaf'] = 'forged'
    for path in ['/', '/if/flow/default-authentication-flow/', '/if/flow/cert-onboarding/']:
        public.get(base + path, timeout=15).raise_for_status()
    r = public.get(base + '/api/v3/core/users/me/', timeout=15)
    assert r.status_code in (401, 403), r.status_code
    r = public.get(base + '/api/v3/core/users/me/', headers=admin.headers, timeout=15)
    assert r.status_code == 200 and r.json()['user']['username'] == 'akadmin', r.status_code
# An anonymous attempt must not poison the next certificate user's flow plan.
with session(None) as anonymous:
    anonymous.get(base + '/api/v3/flows/executor/cert-onboarding/', timeout=15).raise_for_status()
    assert anonymous.get(base + '/api/v3/core/users/me/', timeout=15).status_code in (401, 403)
print('PASS: certificate-free Web access; application authentication still required')

browser = session()
browser.headers['X-Amzn-Mtls-Clientcert-Leaf'] = 'forged'
def complete_onboarding(browser):
    # Password is required for new/incomplete users; factor registration is optional.
    browser.get(base + '/if/flow/cert-onboarding/', timeout=15).raise_for_status()
    r = browser.get(base + '/api/v3/flows/executor/cert-onboarding/', timeout=15)
    r.raise_for_status()
    for _ in range(2):
        challenge = r.json()
        if challenge.get('component') != 'ak-stage-prompt':
            break
        fields = {f['field_key'] for f in challenge['fields']}
        if fields == {'password', 'password_repeat'}:
            data = {'password': config['authentik_bootstrap_password'],
                    'password_repeat': config['authentik_bootstrap_password']}
        else:
            assert fields == {'onboarding_authenticator'}, fields
            data = {'onboarding_authenticator': 'skip'}
        assert browser.get(base + '/api/v3/core/users/me/', timeout=15).status_code in (401, 403)
        r = browser.post(base + '/api/v3/flows/executor/cert-onboarding/',
            headers={'X-CSRFToken': browser.cookies.get('authentik_csrf', ''), 'Referer': base + '/'},
            json={'component': 'ak-stage-prompt', **data}, timeout=15)
        r.raise_for_status()

complete_onboarding(browser)
r = browser.get(base + '/api/v3/core/users/me/', timeout=15)
assert r.status_code == 200 and r.json()['user']['username'] == '1001', (r.status_code, r.text)
print('PASS: certificate flow logs in as CN user 1001; forged identity header ignored')
# Cached certificate decisions must not authenticate subsequent anonymous users.
with session(None) as anonymous:
    anonymous.get(base + '/api/v3/flows/executor/cert-onboarding/', timeout=15).raise_for_status()
    assert anonymous.get(base + '/api/v3/core/users/me/', timeout=15).status_code in (401, 403)
with session() as repeat:
    complete_onboarding(repeat)
    r = repeat.get(base + '/api/v3/core/users/me/', timeout=15)
    assert r.status_code == 200 and r.json()['user']['username'] == '1001'
print('PASS: anonymous/certificate onboarding attempts do not reuse each other’s identity or empty plan')


# Give the certificate-enrolled fixture a password for RADIUS PAP checks.
r = admin.get(base + '/api/v3/core/users/?username=1001', timeout=15)
r.raise_for_status()
user = next(u for u in r.json()['results'] if u['username'] == '1001')
r = admin.post(base + f"/api/v3/core/users/{user['pk']}/set_password/",
               json={'password': config['authentik_bootstrap_password']}, timeout=15)
assert r.status_code == 204, (r.status_code, r.text)

def radius_request(password, username='1001'):
    code, _ = access_request('127.0.0.1', 1812, config['radius_shared_secret'], username, password)
    return code

assert radius_request(config['authentik_bootstrap_password']) == 2, 'Expected Access-Accept'
assert radius_request('incorrect-kind-password') == 3, 'Expected Access-Reject'
print('PASS: published UDP 1812 accepts valid credentials and rejects invalid credentials')
assert radius_request(config['authentik_bootstrap_password'], 'akadmin') == 3, 'akadmin is not in radius-users'
print('PASS: RADIUS rejects valid credentials of users outside radius-users')
r = admin.patch(base + f"/api/v3/core/users/{user['pk']}/", json={'email': 'user1001@kind.test'}, timeout=15)
assert r.status_code == 200, (r.status_code, r.text)
assert radius_request(config['authentik_bootstrap_password'], 'user1001@kind.test') == 2, 'Expected email Access-Accept'
print('PASS: RADIUS accepts the email address as the login identifier')

if config.get('ssh_demo_enabled'):
    async def ssh_login(password):
        async with asyncssh.connect('127.0.0.1', 2222, username='1001', password=password,
                                    known_hosts=None, client_keys=None, agent_path=None) as conn:
            return (await conn.run('show users', check=True)).stdout

    assert '1001' in asyncio.run(ssh_login(config['authentik_bootstrap_password']))
    try:
        asyncio.run(ssh_login('incorrect-kind-password'))
        raise AssertionError('SSH demo accepted an invalid password')
    except asyncssh.PermissionDenied:
        pass
    print('PASS: SSH demo device on localhost:2222 authenticates the certificate-enrolled user via RADIUS')

# Marker on shared storage must remain visible to server and worker after replacement.
subprocess.run(kube + ['exec', '-n', 'authentik', 'deployment/authentik-server', '--',
    'python', '-c', "from pathlib import Path; Path('/data/kind-persistence-check').write_text('persistent')"], check=True)
subprocess.run(kube + ['exec', '-n', 'authentik', 'deployment/authentik-worker', '--',
    'python', '-c', "from pathlib import Path; assert Path('/data/kind-persistence-check').read_text() == 'persistent'"], check=True)
print('PASS: server and worker share writable PVC storage')

if args.rollout:
    stop = threading.Event()
    failures = []
    count = [0]
    def traffic():
        with session() as client:
            client.headers['Authorization'] = admin.headers['Authorization']
            while not stop.is_set():
                try:
                    r = client.get(base + '/api/v3/core/users/me/', timeout=10)
                    if r.status_code != 200:
                        failures.append(f"{time.time()}: HTTP {r.status_code}")
                except requests.RequestException as e:
                    failures.append(str(e))
                count[0] += 1
                stop.wait(.1)
    t = threading.Thread(target=traffic)
    t.start()
    try:
        for ns, name in [('authentik-edge', 'authentik-edge'), ('authentik', 'authentik-server'), ('authentik', 'authentik-worker')]:
            subprocess.run(kube + ['rollout', 'restart', f'deployment/{name}', '-n', ns], check=True)
            subprocess.run(kube + ['rollout', 'status', f'deployment/{name}', '-n', ns, '--timeout=300s'], check=True)
        subprocess.run(kube + ['scale', 'deployment/authentik-server', '-n', 'authentik', '--replicas=2'], check=True)
        subprocess.run(kube + ['rollout', 'status', 'deployment/authentik-server', '-n', 'authentik', '--timeout=300s'], check=True)
        # Force new connections to exercise both endpoints, not just one keepalive socket.
        for _ in range(20):
            with session() as fresh:
                r = fresh.get(base + '/api/v3/core/users/me/', headers=admin.headers, timeout=10)
                assert r.status_code == 200, r.status_code
        subprocess.run(kube + ['scale', 'deployment/authentik-server', '-n', 'authentik', '--replicas=1'], check=True)
        subprocess.run(kube + ['rollout', 'status', 'deployment/authentik-server', '-n', 'authentik', '--timeout=300s'], check=True)
    finally:
        stop.set()
        t.join()
    print(f'PASS: rollouts and scale 1 -> 2 -> 1 completed; {count[0]} requests, {len(failures)} transient errors')
    # Short errors/session restart during replacement are an accepted requirement.
    browser = session()
    complete_onboarding(browser)
    r = browser.get(base + '/api/v3/core/users/me/', timeout=15)
    assert r.status_code == 200 and r.json()['user']['username'] == '1001', (r.status_code, r.text)
    print('PASS: certificate user can log in again after application Pod replacement')
    subprocess.run(kube + ['exec', '-n', 'authentik', 'deployment/authentik-server', '--',
        'python', '-c', "from pathlib import Path; assert Path('/data/kind-persistence-check').read_text() == 'persistent'"], check=True)
    print('PASS: PVC content persists after application Pod replacement')

if args.restart_db:
    old = json.loads(subprocess.check_output(kube + ['get', 'pod/authentik-pg-1', '-n', 'authentik', '-o', 'json']))
    subprocess.run(kube + ['delete', 'pod/authentik-pg-1', '-n', 'authentik', '--wait=false'], check=True)
    # CNPG allows a long graceful PostgreSQL shutdown before replacing the Pod.
    deadline = time.monotonic() + old['spec'].get('terminationGracePeriodSeconds', 30) + 180
    while time.monotonic() < deadline:
        result = subprocess.run(kube + ['get', 'pod/authentik-pg-1', '-n', 'authentik', '-o', 'json'], capture_output=True)
        if result.returncode == 0:
            pod = json.loads(result.stdout)
            if pod['metadata']['uid'] != old['metadata']['uid'] and any(
                c['type'] == 'Ready' and c['status'] == 'True' for c in pod.get('status', {}).get('conditions', [])
            ):
                break
        time.sleep(2)
    else:
        raise AssertionError('PostgreSQL replacement did not become Ready')
    # Server startup/backoff may outlast database readiness after an outage.
    recovery_deadline = time.monotonic() + 300
    while time.monotonic() < recovery_deadline:
        try:
            r = admin.get(base + '/api/v3/core/users/?username=1001', timeout=10)
            if r.status_code == 200 and any(u['username'] == '1001' for u in r.json()['results']):
                break
        except requests.RequestException:
            pass
        time.sleep(2)
    else:
        raise AssertionError('Application/database did not recover with persisted user')
    print('PASS: PostgreSQL Pod replacement preserves enrolled user and application recovers')
