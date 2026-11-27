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
    def test_missing_expected_edge_fails(self):
        t=Validation(lambda _:[{'metric':{'client':'a','server':'b'},'value':[0,'1']}])
        t.expected_edges('edges','x',{('a','b'),('b','c')},lambda v:v>=0)
        self.assertTrue(t.failures)
    def test_nonfinite_edge_fails(self):
        t=Validation(lambda _:[{'metric':{'client':'a','server':'b'},'value':[0,'NaN']}])
        t.expected_edges('edges','x',{('a','b')},lambda v:v>=0)
        self.assertTrue(t.failures)
    def test_missing_unexpected_edge_fails_even_without_histogram(self):
        row={'metric':{'client':'web','server':'api'},'value':[0,'2']}
        t=Validation(lambda _: [row]); t.no_unexpected_missing_edges('missing','x',{('api','db')})
        self.assertEqual(t.failures,['missing'])
    def test_documented_non_http_edge_is_exempt(self):
        row={'metric':{'client':'api','server':'db'},'value':[0,'2']}
        t=Validation(lambda _: [row]); t.no_unexpected_missing_edges('missing','x',{('api','db')})
        self.assertFalse(t.failures)
    def test_no_missing_edges(self):
        t=Validation(lambda _: []); t.no_unexpected_missing_edges('missing','x',set())
        self.assertFalse(t.failures)
if __name__=='__main__': unittest.main()

