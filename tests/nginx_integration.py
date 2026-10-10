"""Local Docker smoke test: mTLS, PROXY source filtering, identity headers, reload."""
import datetime
import ipaddress
import json
import socket
import ssl
import struct
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import unquote

import jinja2
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

ROOT = Path(__file__).resolve().parents[1]

def run(*args):
    return subprocess.check_output(args, text=True).strip()

def issue(cn, issuer=None, client=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(name)
               .issuer_name(issuer[1].subject if issuer else name)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(minutes=1))
               .not_valid_after(now + datetime.timedelta(days=1)))
    builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
        issuer[0].public_key() if issuer else key.public_key()), critical=False)
    builder = builder.add_extension(x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=issuer is not None,
        data_encipherment=False, key_agreement=False, key_cert_sign=issuer is None,
        crl_sign=issuer is None, encipher_only=False, decipher_only=False), critical=True)
    if issuer is None:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    else:
        builder = builder.add_extension(x509.ExtendedKeyUsage([
            ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        if not client:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
    return key, builder.sign(issuer[0] if issuer else key, hashes.SHA256())

def write_pair(path, stem, pair):
    path.mkdir(exist_ok=True)
    (path / (stem + '.key.new')).write_bytes(pair[0].private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (path / (stem + '.crt.new')).write_bytes(pair[1].public_bytes(serialization.Encoding.PEM))
    # Projected Kubernetes Secrets replace inodes. In-place writes in the same
    # second can retain nginx's inherited TLS object cache (mtime + inode).
    (path / (stem + '.key.new')).replace(path / (stem + '.key'))
    (path / (stem + '.crt.new')).replace(path / (stem + '.crt'))


def main():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / 'ansible/templates'), undefined=jinja2.StrictUndefined)
    env.filters.update(to_json=json.dumps, from_json=json.loads)
    values = dict(authentik_domain='auth.example.org', authentik_namespace='authentik',
                  ingress_namespace='authentik-edge', vpc_cidr='0.0.0.0/0',
                  allowed_cidrs_json='["198.51.100.0/24"]', nginx_replicas=2,
                  nginx_image='nginx:1.30.4-alpine', nlb_target_group_arn='arn:test',
                  acme_email='test@example.org', acme_server='https://acme.example/directory',
                  nlb_http_target_group_arn='arn:test:http')
    for template in ['certificate.yaml.j2', 'nginx.yaml.j2', 'acme-http.yaml.j2']:
        assert list(yaml.safe_load_all(env.get_template(template).render(**values)))
    issuer = next(yaml.safe_load_all(env.get_template('certificate.yaml.j2').render(**values)))
    assert issuer['spec']['acme']['solvers'] == [
        {'http01': {'ingress': {'ingressClassName': 'acme-http', 'serviceType': 'ClusterIP'}}}]
    name = 'pki-edge-test-' + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='pki-edge-test-') as tmp:
        root = Path(tmp)
        ca = issue('Test CA')
        server = issue('auth.example.org', ca)
        client = issue('1234 Alice, Example+ 01', ca, True)
        wrong = issue('Wrong CA')
        write_pair(root / 'tls', 'tls', server)
        write_pair(root / 'client', 'client', client)
        write_pair(root / 'wrong', 'client', issue('Bad client', wrong, True))
        (root / 'ca').mkdir()
        (root / 'ca/ca.crt').write_bytes(ca[1].public_bytes(serialization.Encoding.PEM))
        (root / 'config').mkdir()
        config = env.get_template('nginx.conf.j2').render(**values)
        config = config.replace('http://authentik-server.authentik.svc.cluster.local:80', 'http://127.0.0.1:18081')
        # Echo only the leaf received by the mock application.
        config = config.rsplit('}', 1)[0] + "server { listen 127.0.0.1:18081; location / { return 200 '$http_x_amzn_mtls_clientcert_leaf'; } }\n}\n"
        (root / 'config/nginx.conf').write_text(config)
        context = ssl.create_default_context(cafile=str(root / 'ca/ca.crt'))
        authenticated = ssl.create_default_context(cafile=str(root / 'ca/ca.crt'))
        authenticated.load_cert_chain(str(root / 'client/client.crt'), str(root / 'client/client.key'))
        untrusted = ssl.create_default_context(cafile=str(root / 'ca/ca.crt'))
        untrusted.load_cert_chain(str(root / 'wrong/client.crt'), str(root / 'wrong/client.key'))
        try:
            run('docker', 'run', '-d', '--name', name, '--read-only', '--user', '101:101',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:uid=101,gid=101',
                '-p', '127.0.0.1::8443',
                '-v', str(root / 'config') + ':/etc/edge:ro',
                '-v', str(root / 'tls') + ':/etc/nginx/tls:ro',
                '-v', str(root / 'ca') + ':/etc/nginx/client-ca:ro',
                '-v', str(ROOT / 'kubernetes/nginx') + ':/opt/edge:ro',
                '--entrypoint', '/bin/sh', values['nginx_image'], '/opt/edge/run.sh')
            port = int(run('docker', 'port', name, '8443/tcp').rsplit(':', 1)[1])
            def request(ctx, source='198.51.100.42'):
                with socket.create_connection(('127.0.0.1', port), timeout=5) as raw:
                    # NLB PROXY protocol v2 IPv4/TCP header precedes the TLS handshake.
                    address = ipaddress.ip_address(source).packed + socket.inet_aton('192.0.2.1') + struct.pack('!HH', 23456, 443)
                    raw.sendall(b'\r\n\r\n\x00\r\nQUIT\n' + bytes([0x21, 0x11]) + struct.pack('!H', len(address)) + address)
                    with ctx.wrap_socket(raw, server_hostname='auth.example.org') as stream:
                        cert = x509.load_der_x509_certificate(stream.getpeercert(binary_form=True))
                        stream.sendall(b'GET / HTTP/1.1\r\nHost: auth.example.org\r\nX-Amzn-Mtls-Clientcert-Leaf: forged\r\nConnection: close\r\n\r\n')
                        chunks = []
                        while True:
                            chunk = stream.recv(65536)
                            if not chunk:
                                break
                            chunks.append(chunk)
                        return b''.join(chunks), cert.serial_number
            for attempt in range(30):
                try:
                    response, serial = request(authenticated)
                    break
                except (OSError, ssl.SSLError) as error:
                    if attempt == 29:
                        raise AssertionError('nginx request failed') from error
                    time.sleep(0.5)
            else:
                raise AssertionError('nginx did not start')
            assert b'200 OK' in response.split(b'\r\n', 1)[0], response
            assert unquote(response.split(b'\r\n\r\n', 1)[1].decode()) == client[1].public_bytes(serialization.Encoding.PEM).decode()
            print('PASS: trusted client accepted; spoofed identity header overwritten')
            for ctx, label in [(context, 'no certificate'), (untrusted, 'untrusted CA')]:
                try:
                    response, _ = request(ctx)
                    assert b'200 OK' not in response.split(b'\r\n', 1)[0]
                except ssl.SSLError:
                    pass
                print('PASS: rejected ' + label)
            response, _ = request(authenticated, '203.0.113.1')
            assert b'403 Forbidden' in response.split(b'\r\n', 1)[0], response
            print('PASS: CIDR enforced on PROXY-protocol client address')
            replacement = issue('auth.example.org', ca)
            write_pair(root / 'tls', 'tls', replacement)
            # Docker Desktop bind propagation and VM scheduling can lag the host.
            for _ in range(90):
                response, serial = request(authenticated)
                if serial == replacement[1].serial_number:
                    break
                time.sleep(1)
            assert serial == replacement[1].serial_number, 'certificate was not reloaded'
            print('PASS: renewed server certificate served without container restart')
        except Exception:
            subprocess.run(['docker', 'logs', name], check=False)
            raise
        finally:
            subprocess.run(['docker', 'rm', '-f', name], check=False, stdout=subprocess.DEVNULL)

if __name__ == '__main__':
    main()
