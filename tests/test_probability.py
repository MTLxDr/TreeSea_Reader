from copy import deepcopy
import unittest

from reader.probability import conditional_marginals,fixed_terminals,known_counts,rounded_percentages,refresh_map_probabilities
from reader.catalog import Catalog


class ProbabilityTests(unittest.TestCase):
    def test_integer_display_totals_100_without_changing_values(self):
        values=[1/3]*3
        self.assertEqual(rounded_percentages(values),[34,33,33])
        self.assertEqual(values,[1/3]*3)

    def test_joint_cap_changes_other_nodes_even_before_saturation(self):
        posterior,model=conditional_marginals([{'a':.5,'b':.5},{'a':.5,'b':.5}],{'a':(0,1)})
        self.assertEqual(model['method'],'quota_exact')
        self.assertAlmostEqual(posterior[0]['a'],1/3)
        self.assertAlmostEqual(posterior[1]['a'],1/3)

    def test_saturated_type_has_zero_probability(self):
        p,_=conditional_marginals([{'a':.8,'b':.2}],{'a':(0,0)})
        self.assertEqual(p[0]['a'],0)
        self.assertEqual(p[0]['b'],1)

    def test_impossible_bounds_return_unknown(self):
        p,model=conditional_marginals([{'a':1.}],{'a':(0,0)})
        self.assertIsNone(p)
        self.assertEqual(model['status'],'inconsistent')

    def test_lower_bound_can_force_remaining_node(self):
        p,_=conditional_marginals([{'a':.1,'b':.9}],{'a':(1,1)})
        self.assertEqual(p[0]['a'],1)

    def test_large_models_sample_reproducibly_under_joint_limits(self):
        priors=[{'a':.5,'b':.5}]*18
        p,model=conditional_marginals(priors,{'a':(0,1)})
        # Very rare feasible configurations may correctly remain unknown.
        if p is not None:
            self.assertLessEqual(sum(row['a'] for row in p),1.000001)
        self.assertEqual(model,conditional_marginals(priors,{'a':(0,1)})[1])

    def test_fixed_exits_require_confirmed_template_and_only_requested_floors(self):
        base={'needs_review':False,'floor':1,'terminal_slots':[[2,0]],
              'nodes':[{'slot':[2,0],'type':'unobserved','manual':False}]}
        for floor in (1,2,4):
            result=deepcopy(base);result['floor']=floor;fixed_terminals(result)
            self.assertEqual(result['nodes'][0]['type'],'danger-end')
            self.assertEqual(result['nodes'][0]['evidence'],'template_fixed_exit')
        for patch in ({'floor':3},{'needs_review':True}):
            result=deepcopy(base);result.update(patch);fixed_terminals(result)
            self.assertEqual(result['nodes'][0]['type'],'unobserved')

    def test_start_side_and_low_confidence_marker_are_not_counted(self):
        result={'start_slot':[0,0],'nodes':[
            {'slot':[0,0],'type':'empty','label':'林间空地','manual':True},
            {'slot':[1,0],'type':'empty','label':'林间空地','evidence':'small_marker_candidate','confidence':.72}],
            'side_map':{'nodes':[{'type':'combat','label':'作战','manual':True}]}}
        self.assertEqual(known_counts(result),{})

    def test_source_distance_distribution_uses_per_type_denominator(self):
        c=Catalog();d=c.distance_distribution(2,'作战')
        self.assertGreater(d['sample_count'],0)
        self.assertAlmostEqual(sum(r['probability'] for r in d['estimates']),1)


if __name__=='__main__':unittest.main()
