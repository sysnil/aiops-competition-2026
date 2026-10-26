import importlib.util
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('fixed_control_load',
    ROOT/'scripts/fixed_control_load.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FixedControlLoadTest(unittest.TestCase):
    def test_only_verified_business_routes_are_configured(self):
        self.assertEqual(module.PATHS,{
            'image_asset':'/images/products/OpticalTubeAssembly.jpg',
            'product_detail':'/api/products/2ZYFJ3GM2N',
            'cart_view':'/api/cart'})

    def test_saturation_records_missing_arrival_without_burst(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)
            def slow(*args):
                time.sleep(.15)
                return {'index':args[2],'success':True,'trace_id':'trace'}
            with patch.object(module,'request',side_effect=slow):
                result=module.run_stream('http://unused',
                    {'name':'image_asset','rate_per_second':30},10,1,1,1,output)
            self.assertEqual(result['planned_requests'],30)
            self.assertEqual(result['recorded_requests'],30)
            self.assertGreater(result['missed_arrivals'],0)


if __name__=='__main__':unittest.main()
