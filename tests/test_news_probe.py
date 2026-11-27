"""The deployed shell command must handle wget's status under /bin/sh -e."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]

class TestNewsProbe(unittest.TestCase):
    def test_wget_exit_codes_under_errexit(self):
        cfg=yaml.safe_load((ROOT/'lab-setup/inventory/group_vars/all/postcluster.yaml').read_text())
        probes=cfg['tt_probe_overrides']['ts-news-service/ts-news-service']
        with tempfile.TemporaryDirectory() as d:
            wget=Path(d)/'wget'
            wget.write_text('#!/bin/sh\nexit "$WGET_STATUS"\n'); wget.chmod(0o700)
            for field in ('readinessProbe','startupProbe'):
                for code,expected in [(0,0),(8,0),(1,1),(4,1),(127,1)]:
                    with self.subTest(probe=field,status=code):
                        env=dict(os.environ,PATH=d+':'+os.environ['PATH'],WGET_STATUS=str(code))
                        self.assertEqual(subprocess.run(probes[field]['exec']['command'],env=env).returncode,expected)

if __name__=='__main__': unittest.main()
