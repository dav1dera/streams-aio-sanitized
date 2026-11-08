"""Homepage auto-allocation must not consume the stack's fixed Docker subnets."""
import ipaddress
from pathlib import Path
import unittest

import yaml


class HomepageNetworkAllocation(unittest.TestCase):
    def test_homepage_pools_are_explicit_and_disjoint_from_every_declared_pool(self):
        root = Path(__file__).resolve().parents[1]
        if not (root / 'docker-compose.yml').is_file():
            root = Path(__file__).resolve().parent
        model = yaml.safe_load((root / 'docker-compose.yml').read_text(encoding='utf-8'))
        networks = model['networks']
        pools = []
        for name, network in networks.items():
            for entry in (network or {}).get('ipam', {}).get('config', []):
                if entry.get('subnet'):
                    pools.append((name, ipaddress.ip_network(entry['subnet'])))
        for name in ('homepage-frontend', 'homepage-monitoring'):
            self.assertTrue(any(key == name and pool.version == 4 for key, pool in pools),
                            name + ' needs explicit IPv4 IPAM to avoid stealing the stack subnet')
        for index, (left_name, left) in enumerate(pools):
            for right_name, right in pools[index+1:]:
                if left.version == right.version:
                    self.assertFalse(left.overlaps(right), left_name + ' overlaps ' + right_name)
        self.assertIs(networks['homepage-monitoring']['internal'], True)
        for service in model['services'].values():
            attached = service.get('networks') or {}
            if not isinstance(attached, dict):
                continue
            for name, options in attached.items():
                address = (options or {}).get('ipv4_address')
                if not address or '$' in str(address):
                    continue
                self.assertTrue(any(key == name and ipaddress.ip_address(address) in pool
                                    for key, pool in pools if pool.version == 4),
                                'Static service address must remain in its declared network')


if __name__ == '__main__':
    unittest.main()

