import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from publisher import PublicationHTTPError, prepare_subscription, publish


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.blob = bytes.fromhex('28b52ffd') + b'already validated upstream MRS'
        self.digest = hashlib.sha256(self.blob).hexdigest()
        (self.directory / 'cn.mrs').write_bytes(self.blob)
        (self.directory / 'manifest.json').write_text(json.dumps({'providers': {'cn': {'sha256': self.digest, 'bytes': len(self.blob)}}}))
        self.source = {'proxies': [{'name': 'OCI-IPv6', 'server': '2001:db8::1', 'password': 'keep-me'},
                                   {'name': 'Google-IPv4', 'server': '192.0.2.1', 'uuid': 'keep-me-too'}],
                       'proxy-groups': [{'name': 'all', 'type': 'select', 'proxies': ['OCI-IPv6', 'Google-IPv4']}],
                       'rules': ['MATCH,all'], 'dns': {'ipv6': True},
                       'hosts': {'oci.lfdou.dpdns.org': '147.224.244.198', 'custom.test': '192.0.2.2'},
                       'rule-providers': {'cn': {'type': 'http', 'format': 'mrs', 'url': 'https://oci.lfdou.dpdns.org/rules/cn.mrs'}}}

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, source=None):
        return prepare_subscription(yaml.safe_dump(source or self.source), self.directory, 'https://sub.example.test', 100, 'download=12')

    def test_only_distribution_addresses_change(self):
        snapshot, blobs = self.prepare()
        actual = yaml.safe_load(snapshot['yaml'])
        for key in ['proxies', 'proxy-groups', 'rules', 'dns']:
            self.assertEqual(actual[key], self.source[key])
        self.assertEqual(actual['hosts'], {'custom.test': '192.0.2.2'})
        self.assertEqual(actual['rule-providers']['cn']['url'], f'https://sub.example.test/rules/{self.digest}/cn.mrs')
        self.assertEqual(blobs[self.digest], self.blob)

    def test_corrupted_rules_cannot_be_published(self):
        (self.directory / 'cn.mrs').write_bytes(self.blob + b'corrupted')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_missing_node_family_rejected(self):
        source = copy.deepcopy(self.source)
        source['proxies'] = source['proxies'][:1]
        with self.assertRaises(ValueError):
            self.prepare(source)

    def test_failed_rule_upload_does_not_commit_snapshot_or_local_state(self):
        config = self.config()
        seen = []
        def transport(url, **kwargs):
            seen.append(url)
            if kwargs:
                raise OSError('publish failed')
            return yaml.safe_dump(self.source).encode(), {}
        with self.assertRaises(OSError):
            publish(config, transport)
        self.assertFalse(Path(config['state_file']).exists())
        self.assertFalse(any(url.endswith('/__publish/main') for url in seen))

    def config(self):
        return {'publish_base': 'https://worker.example.test', 'public_base': 'https://sub.example.test',
                'source_url': 'http://127.0.0.1:3000/share/file/main?token=test', 'publish_token': 'private',
                'rules_directory': str(self.directory), 'state_file': str(self.directory / 'state.json')}

    def test_successful_publication_then_only_refreshes_snapshot(self):
        config = self.config()
        calls = []
        def transport(url, **kwargs):
            calls.append(url)
            return (b'ok', {}) if kwargs else (yaml.safe_dump(self.source).encode(), {'Subscription-Userinfo': 'download=12'})
        state = publish(config, transport)
        self.assertEqual(state['new_rules'], 1)
        self.assertTrue(calls[-1].endswith('/__publish/main'))
        self.assertEqual(Path(config['state_file']).stat().st_mode & 0o777, 0o600)
        calls.clear()
        self.assertEqual(publish(config, transport)['new_rules'], 0)
        self.assertEqual(len(calls), 2)

    def test_non_local_sources_and_insecure_publication_rejected(self):
        for key, value in [('source_url', 'http://169.254.169.254/'), ('publish_base', 'http://insecure.test')]:
            config = self.config()
            config[key] = value
            with self.assertRaises(ValueError):
                publish(config, lambda *_args, **_kwargs: self.fail('Unexpected network request'))

    def test_missing_cloud_assets_are_repaired_even_with_a_local_upload_record(self):
        config = self.config()
        Path(config['state_file']).write_text(json.dumps({'uploaded': [self.digest]}))
        calls = []
        failed_once = False
        def transport(url, **kwargs):
            nonlocal failed_once
            calls.append(url)
            if not kwargs:
                return yaml.safe_dump(self.source).encode(), {}
            if url.endswith('/__publish/main') and not failed_once:
                failed_once = True
                raise PublicationHTTPError(409)
            return b'ok', {}
        with patch('publisher.time.sleep') as sleep:
            publish(config, transport)
        sleep.assert_called_once_with(65)
        self.assertEqual(sum('/__publish/rules/' in url for url in calls), 1)
        self.assertEqual(sum(url.endswith('/__publish/main') for url in calls), 2)


if __name__ == '__main__':
    unittest.main()
