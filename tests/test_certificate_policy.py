"""Execute the actual blueprint expressions with an in-memory user store."""
import datetime
import sys
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from nginx_integration import issue

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]

class BlueprintLoader(yaml.SafeLoader):
    pass
BlueprintLoader.add_constructor('!Find', lambda loader, node: loader.construct_sequence(node))

PRIVATE_CA = issue("Private test CA")

def certificate(cn, ca=PRIVATE_CA):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn),
                      x509.NameAttribute(NameOID.EMAIL_ADDRESS, 'alice+test@example.org')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(ca[1].subject)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1)).sign(ca[0], hashes.SHA256()))
    return quote(cert.public_bytes(serialization.Encoding.PEM).decode(), safe='+=/')

class PolicyTests(unittest.TestCase):
    def setUp(self):
        doc = yaml.load((ROOT / 'kubernetes/authentik/blueprints/onboarding-flow.yaml').read_text(), Loader=BlueprintLoader)
        self.expressions = {e['identifiers'].get('name'): e['attrs']['expression']
                            for e in doc['entries'] if 'expression' in e.get('attrs', {})}
        self.users = {}
        def create(username, defaults):
            if username in self.users:
                return self.users[username], False
            user = types.SimpleNamespace(username=username, **defaults,
                                         ak_groups=types.SimpleNamespace(add=lambda group: None))
            self.users[username] = user
            return user, True
        manager = types.SimpleNamespace(
            filter=lambda **kw: types.SimpleNamespace(first=lambda: self.users.get(kw['username'])),
            get_or_create=create)
        models = types.ModuleType('authentik.core.models')
        models.User = types.SimpleNamespace(objects=manager)
        models.Group = types.SimpleNamespace(objects=types.SimpleNamespace(get_or_create=lambda **kw: (object(), True)))
        self.models = models
        self.plan = types.SimpleNamespace(context={})

    def run_policy(self, kind, leaf='', subject=''):
        request = types.SimpleNamespace(
            http_request=types.SimpleNamespace(META={
                'HTTP_X_AMZN_MTLS_CLIENTCERT_LEAF': leaf,
                'HTTP_X_AMZN_MTLS_CLIENTCERT_SUBJECT': subject}),
            context={'flow_plan': self.plan})
        env = {'request': request, 'ak_logger': types.SimpleNamespace(warning=lambda *a: None),
               'ak_create_event': lambda *a, **kw: None}
        body = self.expressions['cert-onboarding-' + kind + '-policy']
        with patch.dict(sys.modules, {'authentik.core.models': self.models}):
            exec('def evaluate():\n' + textwrap.indent(body, '    '), env)
            return env['evaluate']()

    def test_subject_header_cannot_create_user(self):
        self.assertFalse(self.run_policy('create', subject='CN=1234 Alice Example 01'))
        self.assertFalse(self.users)

    def test_malformed_leaf_fails_closed(self):
        self.assertFalse(self.run_policy('create', 'not-a-certificate'))

    def test_enrollment_preserves_escaped_name_and_email(self):
        self.assertTrue(self.run_policy('create', certificate('1234 Alice, Example+ 01')))
        user = self.users['1234']
        self.assertEqual(user.name, 'Alice, Example+')
        self.assertEqual(user.email, 'alice+test@example.org')
        self.assertTrue(self.plan.context['is_new_user'])

    def test_lookup_does_not_create_unknown_user(self):
        self.assertFalse(self.run_policy('lookup', certificate('1234 Alice Example 01')))
        self.assertFalse(self.users)

    def test_existing_user_resolves_without_creation(self):
        user = types.SimpleNamespace(username='1234')
        self.users['1234'] = user
        self.assertTrue(self.run_policy('lookup', certificate('1234 Alice Example 01')))
        self.assertIs(self.plan.context['pending_user'], user)
        self.assertNotIn('is_new_user', self.plan.context)

    def test_non_numeric_cn_is_not_enrolled(self):
        self.assertFalse(self.run_policy('create', certificate('ABC123')))
        self.assertFalse(self.users)

if __name__ == '__main__':
    unittest.main()
