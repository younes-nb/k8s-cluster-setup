#!/usr/bin/env python3
import unittest
from check_live_metrics import Validation, finite_rows

class NegativeTests(unittest.TestCase):
    def test_missing_is_not_zero(self):
        t=Validation(lambda _:[])
        t.values('zero may be valid only if present','x',lambda v:v>=0)
        self.assertEqual(t.failures,['zero may be valid only if present'])
    def test_nonfinite(self):
        for v in ('NaN','+Inf','-Inf'):
            with self.assertRaises(ValueError): finite_rows([{'value':[0,v]}])
    def test_zero_present(self):
        t=Validation(lambda _:[{'value':[0,'0']}]);t.values('zero','x',lambda v:v==0);self.assertFalse(t.failures)
    def test_partial_host_coverage(self):
        t=Validation(lambda _:[{'metric':{'instance':'master1'},'value':[0,'1']}])
        t.identities('hosts','x','instance',{'master1','worker1'});self.assertTrue(t.failures)
    def test_stale(self):
        t=Validation(lambda _:[{'value':[0,'60']}]);t.fresh('age','x',15);self.assertTrue(t.failures)
    def test_duplicate_scrapes_fail_exact_identities(self):
        row={'metric':{'pod':'nacos-0'},'value':[0,'1']}
        t=Validation(lambda _:[row,row]); t.exact_identities('duplicate','x','pod',{'nacos-0'},lambda v:v==1)
        self.assertTrue(t.failures)
    def test_unexpected_scrape_fails_exact_identities(self):
        rows=[{'metric':{'pod':p},'value':[0,'1']} for p in ('nacos-0','nacos-1')]
        t=Validation(lambda _:rows); t.exact_identities('unexpected','x','pod',{'nacos-0'},lambda v:v==1)
        self.assertTrue(t.failures)
if __name__=='__main__': unittest.main()

