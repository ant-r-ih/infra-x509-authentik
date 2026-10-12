"""Exercise onboarding stages in the dedicated kind cluster via a local forward.

This isolates Authentik flow logic from nginx's TLS verification. Synthetic leaf
headers are sent ONLY to the private forward; nginx TLS is tested separately by
nginx_integration.py / kind_integration.py. Real users and client CAs are untouched.
"""
import argparse
import base64
import hashlib
import hmac
import json
import secrets
import struct
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from test_certificate_policy import certificate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path('/tmp/authentik-kind'))
    parser.add_argument('--port', type=int, default=19002)
    args = parser.parse_args()
    cfg = json.loads((args.state / 'vars.json').read_text())
    assert Path(cfg['kubeconfig']).resolve() == Path('/tmp/authentik-kind.kubeconfig').resolve()
    kube = ['kubectl', '--kubeconfig', cfg['kubeconfig'], '--context', 'kind-authentik-dev']
    subprocess.run(kube + ['get', 'node', 'authentik-dev-control-plane'], check=True, stdout=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{args.port}'
    executor = '/api/v3/flows/executor/cert-onboarding/'

    def session(leaf=None):
        s = requests.Session()
        s.trust_env = False
        s.headers['Host'] = 'auth.kind.test'
        if leaf:
            s.headers['X-Amzn-Mtls-Clientcert-Leaf'] = leaf
        return s

    def get(s, path):
        r = s.get(base + path, timeout=15)
        r.raise_for_status()
        return r.json()

    def post(s, data, path=executor):
        r = s.post(base + path, json=data, headers={
            'X-CSRFToken': s.cookies.get('authentik_csrf', ''),
            'Referer': 'http://auth.kind.test/'}, timeout=15)
        r.raise_for_status()
        return r.json()

    def anonymous(s):
        assert s.get(base + '/api/v3/core/users/me/', timeout=15).status_code in (401, 403)

    def start(leaf=None):
        s = session(leaf)
        s.get(base + '/if/flow/cert-onboarding/', timeout=15).raise_for_status()
        return s, get(s, executor)

    def fields(challenge):
        return {f['field_key'] for f in challenge.get('fields', [])}

    def choice(s, challenge):
        assert challenge['component'] == 'ak-stage-prompt', challenge.get('component')
        assert fields(challenge) == {'onboarding_authenticator'}, fields(challenge)
        anonymous(s)

    forward = subprocess.Popen(kube + ['-n', 'authentik', 'port-forward',
        'service/authentik-server', f'{args.port}:80'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    admin = session()
    admin.headers['Authorization'] = 'Bearer ' + cfg['authentik_api_token']
    created = []
    try:
        for attempt in range(60):
            try:
                get(admin, '/api/v3/core/users/me/')
                break
            except requests.RequestException:
                if forward.poll() is not None:
                    raise RuntimeError('Dedicated port-forward exited')
                time.sleep(0.5)
        else:
            raise RuntimeError('Port-forward unavailable')
        for endpoint in ['flows/instances', 'policies/all']:
            r = admin.post(base + f'/api/v3/{endpoint}/cache_clear/', timeout=15)
            r.raise_for_status()

        for factor in ('skip', 'totp', 'passkey'):
            username = '98' + str(secrets.randbelow(10**12)).zfill(12)
            assert not get(admin, '/api/v3/core/users/?username=' + username)['results']
            created.append(username)
            leaf = certificate(username + ' Kind Onboarding Test 01')
            s, c = start(leaf)
            assert fields(c) == {'password', 'password_repeat'}, fields(c)
            anonymous(s)
            for data in ({}, {'password': '', 'password_repeat': ''},
                         {'password': 'valid-test-password', 'password_repeat': 'mismatch'}):
                c = post(s, {'component': 'ak-stage-prompt', **data})
                assert fields(c) == {'password', 'password_repeat'}
                assert c.get('response_errors'), 'Invalid password must be rejected'
                anonymous(s)
            # An interrupted enrollment must still require a password on retry.
            s, c = start(leaf)
            assert fields(c) == {'password', 'password_repeat'}
            password = secrets.token_urlsafe(24)
            c = post(s, {'component': 'ak-stage-prompt', 'password': password, 'password_repeat': password})
            choice(s, c)
            c = post(s, {'component': 'ak-stage-prompt', 'onboarding_authenticator': 'invalid'})
            assert c.get('response_errors')
            choice(s, c)
            c = post(s, {'component': 'ak-stage-prompt', 'onboarding_authenticator': factor})
            if factor == 'totp':
                assert c['component'] == 'ak-stage-authenticator-totp', c.get('component')
                anonymous(s)
                params = parse_qs(urlparse(c['config_url']).query)
                key = base64.b32decode(params['secret'][0])
                counter = int(time.time()) // 30
                digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
                offset = digest[-1] & 15
                code = str((struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000).zfill(6)
                c = post(s, {'component': 'ak-stage-authenticator-totp', 'code': code})
            elif factor == 'passkey':
                assert c['component'] == 'ak-stage-authenticator-webauthn', c.get('component')
                anonymous(s)
                print('PASS: Passkey registration challenge; hardware registration remains a browser check')
                # Starting over can select skip after cancelling platform registration.
                s, c = start(leaf)
                choice(s, c)
                c = post(s, {'component': 'ak-stage-prompt', 'onboarding_authenticator': 'skip'})
            assert get(s, '/api/v3/core/users/me/')['user']['username'] == username
            # Existing password must not be reset when returning via certificate.
            s, c = start(leaf)
            choice(s, c)
            post(s, {'component': 'ak-stage-prompt', 'onboarding_authenticator': 'skip'})
            assert get(s, '/api/v3/core/users/me/')['user']['username'] == username
            print(f'PASS: required password, no premature session, optional {factor}, repeat enrollment')
            # Password works without a certificate; enrolled TOTP is requested at login.
            login = session()
            login.get(base + '/if/flow/default-authentication-flow/', timeout=15).raise_for_status()
            login_path = '/api/v3/flows/executor/default-authentication-flow/'
            login_challenge = get(login, login_path)
            assert login_challenge.get('passkey_challenge'), 'Login must offer passkey authentication'
            c = post(login, {'component': 'ak-stage-identification', 'uid_field': username, 'password': password}, login_path)
            if factor == 'totp':
                assert c['component'] == 'ak-stage-authenticator-validate', c.get('component')
                anonymous(login)
            else:
                assert get(login, '/api/v3/core/users/me/')['user']['username'] == username
            print(f'PASS: certificate-free password login ({factor})')
            # Anonymous flow attempts after successful certificate login cannot inherit identity.
            anon, _ = start()
            anonymous(anon)
        print('PASS: anonymous onboarding never authenticated; real users and trust bundle untouched')
    finally:
        try:
            for username in created:
                for user in get(admin, '/api/v3/core/users/?username=' + username)['results']:
                    assert user['username'] == username
                    r = admin.delete(base + f"/api/v3/core/users/{user['pk']}/", timeout=15)
                    r.raise_for_status()
        finally:
            forward.terminate()
            forward.wait(timeout=10)


if __name__ == '__main__':
    main()
