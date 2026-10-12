"""Render the environment boundaries without access to a Kubernetes cluster."""
import json
from pathlib import Path
import unittest

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]

class NativeTemplates(unittest.TestCase):
    def setUp(self):
        self.env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / 'ansible/templates'), undefined=jinja2.StrictUndefined)
        self.env.filters['to_json'] = json.dumps
        self.env.filters['from_json'] = json.loads
        self.values = yaml.safe_load((ROOT / 'ansible/roles/authentik/defaults/main.yml').read_text())
        self.values.update(authentik_domain='auth.test', storage_class='test-csi', authentik_secret_key='test',
                           authentik_pg_password='test', authentik_bootstrap_password='test',
                           authentik_bootstrap_email='test@example.org', authentik_api_token='test',
                           postgres_host='db.example.org', acme_email='test@example.org',
                           acme_server='https://acme.example.org/directory')

    def render(self, template, **overrides):
        return self.env.get_template(template).render(self.values | overrides)

    def test_native_edge_uses_service_not_host_ports(self):
        text = self.render('nginx.yaml.j2')
        objects = list(yaml.safe_load_all(text))
        self.assertNotIn('TargetGroupBinding', text)
        self.assertNotIn('hostPort', text)
        self.assertNotIn('hostNetwork', text)
        svc = next(o for o in objects if o['kind'] == 'Service')
        self.assertEqual(svc['spec']['type'], 'NodePort')
        self.assertEqual(svc['spec']['ports'][0]['nodePort'], 30443)
        self.assertEqual(svc['spec']['externalTrafficPolicy'], 'Local')
        config = self.render('nginx.conf.j2')
        self.assertNotIn('proxy_protocol', config)
        self.assertIn('ssl_verify_client optional;', config)
        self.assertIn('X-Amzn-Mtls-Clientcert-Leaf $ssl_client_escaped_cert', config)

    def test_proxy_protocol_is_explicit_and_trust_is_scoped(self):
        config = self.render('nginx.conf.j2', proxy_protocol_enabled=True, proxy_protocol_trusted_cidrs=['10.2.3.4/32'])
        self.assertIn('listen 8443 ssl proxy_protocol;', config)
        self.assertIn('set_real_ip_from 10.2.3.4/32;', config)
        self.assertNotIn('set_real_ip_from 0.0.0.0/0', config)

    def test_storage_and_external_database_are_configurable(self):
        pg = yaml.safe_load(self.render('postgres.yaml.j2', postgres_instances=1))
        self.assertEqual(pg['spec']['storage']['storageClass'], 'test-csi')
        self.assertEqual(pg['spec']['instances'], 1)
        values = yaml.safe_load(self.render('authentik-values.yaml.j2', postgres_sslmode='verify-full'))
        self.assertEqual(values['authentik']['postgresql']['host'], 'db.example.org')
        self.assertEqual(values['authentik']['postgresql']['sslmode'], 'verify-full')
        self.assertNotIn('redis', values)
        self.assertEqual(values['server']['lifecycle']['preStop']['exec']['command'][0], 'python')

    def test_service_modes(self):
        for mode in ['NodePort', 'ClusterIP', 'LoadBalancer']:
            for template in ['nginx.yaml.j2', 'radius.yaml.j2']:
                docs = list(yaml.safe_load_all(self.render(template, service_type=mode)))
                svc = next(d for d in docs if d['kind'] == 'Service')
                self.assertEqual(svc['spec']['type'], mode)
                self.assertEqual('nodePort' in svc['spec']['ports'][0], mode == 'NodePort')
                self.assertEqual('externalTrafficPolicy' in svc['spec'], mode != 'ClusterIP')

    def test_radius_blueprint_links_an_application_and_renders_secrets(self):
        text = self.render('radius-blueprint.yaml.j2', radius_enabled=True,
                           radius_shared_secret='test-only-radius-secret')
        self.assertTrue(list(yaml.compose_all(text)))
        self.assertIn('authentik_core.application', text)
        self.assertNotIn('CHANGE_ME_RADIUS_SECRET', text)
        self.assertIn('authentik-server.authentik.svc.cluster.local', text)

    def test_kind_port_mapping_matches_services(self):
        kind = yaml.safe_load((ROOT / 'kubernetes/kind/cluster.yaml').read_text())
        ports = kind['nodes'][0]['extraPortMappings']
        self.assertEqual({p['containerPort'] for p in ports}, {self.values[n] for n in ['http_node_port', 'https_node_port', 'radius_node_port']})
        self.assertTrue(all(p['listenAddress'] == '127.0.0.1' for p in ports))

if __name__ == '__main__':
    unittest.main()
