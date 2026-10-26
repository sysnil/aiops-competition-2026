import importlib.util
from pathlib import Path
import random
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('generator', ROOT/'scripts/workload-generator.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class WorkloadGeneratorTest(unittest.TestCase):
    def test_both_machines_have_valid_probability_graph(self):
        for name in ('workload-state-machine.yaml', 'workload-state-machine-v2.yaml'):
            generator.validate_machine(yaml.safe_load((ROOT/'design'/name).read_text()))

    def test_branch_choice_is_seeded_and_respects_weights(self):
        edges = [{'to':'a','probability':.25},{'to':'b','probability':.75}]
        first = [generator.choose(edges, random.Random(17+i)) for i in range(100)]
        second = [generator.choose(edges, random.Random(17+i)) for i in range(100)]
        self.assertEqual(first, second)
        self.assertTrue({'a','b'} <= set(first))

    def test_cart_and_checkout_payload_preserve_session(self):
        state={'product':'2ZYFJ3GM2N','quantity':2,'user_id':'known','person_index':0}
        people=[{'email':'demo@example.com','address':{},'userCurrency':'USD','creditCard':{}}]
        cart=generator.operation_payload('cart_add',state,people)
        checkout=generator.operation_payload('checkout',state,people)
        self.assertEqual(cart['userId'],checkout['userId'])
        self.assertEqual(cart['item']['productId'],state['product'])

    def test_saturated_arrivals_are_recorded_without_catch_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            profiles=root/'profiles.yaml'
            profiles.write_text(yaml.safe_dump({'profiles':{'LOW':{
                'arrival_rate_rps':30,'concurrency':2,'duration_seconds':1,
                'seed':10,'timeout_seconds':1,'max_error_rate':.02}}}))
            people=root/'people.json'
            people.write_text('[{"email":"demo@example.com","address":{},'
                              '"userCurrency":"USD","creditCard":{}}]')
            args=SimpleNamespace(machine=str(ROOT/'design/workload-state-machine.yaml'),
                profiles=str(profiles),people=str(people),profile='LOW',
                duration=1,seed=10,base_url='http://unused',
                output=str(root/'run'),allow_errors=True,virtual_users=1)
            def slow_session(index,*_):
                time.sleep(.15)
                return {'index':index,'workflow':'browse','steps':[],
                        'completed':True,'failure':None}
            with patch.object(generator,'run_session',side_effect=slow_session):
                result=generator.run(args)
            self.assertEqual(result['planned_sessions'],30)
            self.assertGreater(result['missed_arrivals'],0)
            self.assertEqual(result['status'],'LOAD_COMPLETED_WITH_ERRORS')
            rows=[__import__('json').loads(line) for line in (root/'run/sessions.jsonl').read_text().splitlines()]
            self.assertTrue(any(row.get('missed_reason')=='VIRTUAL_USER_BUSY' for row in rows))


if __name__=='__main__':
    unittest.main()
