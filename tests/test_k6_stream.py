import unittest
from check_k6_stream import COUNTERS, snapshot, verify_progress

class TestK6Stream(unittest.TestCase):
    def query(self, expr):
        return [{'metric': {'experiment':'run1'}, 'value':[0,'3']}]
    def test_good(self):
        self.assertEqual(len(snapshot(self.query,'run1')),len(COUNTERS))
    def test_stale(self):
        def query(expr):
            if expr.startswith('time()'):
                return [{'metric':{'experiment':'run1'},'value':[0,'16']}]
            return self.query(expr)
        with self.assertRaises(ValueError): snapshot(query,'run1')
    def test_empty(self):
        with self.assertRaises(ValueError): snapshot(lambda _:[],'run1')
    def test_wrong_experiment(self):
        with self.assertRaises(ValueError): snapshot(self.query,'run2')
    def test_progress(self):
        before={k:{'total':0} for k in COUNTERS}
        after={k:{'total':1} for k in COUNTERS}
        self.assertEqual(verify_progress(before,after)[COUNTERS[0]],1)
        with self.assertRaises(ValueError): verify_progress(before,before)
        with self.assertRaises(ValueError): verify_progress(after,before)

    def test_scenario_is_selected(self):
        def query(expr):
            self.assertIn('scenario="api"', expr)
            return self.query(expr)
        snapshot(query,'run1',scenario='api')
    def test_sparse_event_is_unknown(self):
        def query(expr):
            return [] if 'k6_lab_failed_total' in expr else self.query(expr)
        result=snapshot(query,'run1')
        self.assertIsNone(result['k6_lab_failed_total']['total'])
        self.assertIsNone(result['k6_lab_failed_total']['max_age_seconds'])
    def test_present_event_missing_age_fails(self):
        def query(expr):
            return [] if expr.startswith('time()') and 'k6_lab_failed_total' in expr else self.query(expr)
        with self.assertRaises(ValueError): snapshot(query,'run1')
    def test_observed_event_disappearing_fails(self):
        before={k:{'total':1} for k in COUNTERS}
        after={k:{'total':2} for k in COUNTERS}
        after['k6_lab_failed_total']['total']=None
        with self.assertRaises(ValueError): verify_progress(before,after)

    def test_stale_event_counter_is_allowed(self):
        # A present cumulative event counter with an old sample is valid (rare events).
        def query(expr):
            if expr.startswith('time()') and 'k6_lab_failed_total' in expr:
                return [{'metric':{'experiment':'run1'},'value':[0,'999']}]
            return self.query(expr)
        snapshot(query,'run1')  # must not raise on stale event counter

if __name__=='__main__': unittest.main()
