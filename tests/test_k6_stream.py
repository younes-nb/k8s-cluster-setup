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

if __name__=='__main__': unittest.main()
