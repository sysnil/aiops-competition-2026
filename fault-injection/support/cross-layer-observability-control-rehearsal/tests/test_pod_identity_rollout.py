import importlib.util
from pathlib import Path
import unittest


ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pod_identity_rollout',
    ROOT/'scripts/pod_identity_rollout.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PodIdentityRolloutTest(unittest.TestCase):
    def test_downward_api_fields_precede_otel_attributes(self):
        existing=[{'name':'OTEL_RESOURCE_ATTRIBUTES','value':'service.version=3.0.0'},
                  {'name':'OTHER','value':'value'}]
        enriched=module.enriched(existing)
        self.assertEqual([row['name'] for row in enriched[:2]],
                         ['WG_POD_UID','WG_POD_NAME'])
        self.assertEqual(enriched[0]['valueFrom']['fieldRef']['fieldPath'],'metadata.uid')
        self.assertEqual(enriched[1]['valueFrom']['fieldRef']['fieldPath'],'metadata.name')
        self.assertIn('k8s.pod.uid=$(WG_POD_UID)',enriched[2]['value'])
        self.assertEqual(existing[0]['value'],'service.version=3.0.0')

    def test_existing_pod_attribute_is_rejected(self):
        with self.assertRaises(ValueError):
            module.enriched([{'name':'OTEL_RESOURCE_ATTRIBUTES',
                              'value':'k8s.pod.uid=already-set'}])

    def test_terminating_old_pod_does_not_fail_new_rollout(self):
        wanted=[{'name':'WG_POD_UID','value':'new'}]
        def pod(uid,env,terminating=False):
            metadata={'uid':uid}
            if terminating: metadata['deletionTimestamp']='2026-10-10T00:00:00Z'
            return {'metadata':metadata,'spec':{'containers':[{'env':env}]},
                    'status':{'phase':'Running','containerStatuses':[{'ready':True}]}}
        rows=[pod('old',[],True),pod('new-1',wanted),pod('new-2',wanted)]
        self.assertEqual([item['metadata']['uid'] for item in
                          module.matching_ready_pods(rows,wanted)],['new-1','new-2'])


if __name__=='__main__':unittest.main()
