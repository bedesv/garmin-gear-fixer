import unittest
import tempfile
import shutil
import subprocess
import os
from pathlib import Path
from unittest.mock import patch
import app

SOURCE='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
TARGET=(0.0, 0.0)
REPLACEMENT='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'

class FakeGarmin:
    class ActivityDownloadFormat:
        GPX='gpx'
    def __init__(self): self.links={SOURCE,OTHER}
    def get_activity_gear_v2(self, aid): return [{'uuid':i} for i in self.links]
    def add_gear_to_activity(self, gear, aid): self.links.add(gear)
    def remove_gear_from_activity(self, gear, aid): self.links.remove(gear)
    def get_activities_by_date(self,*args,**kwargs): return [{'activityId':1,'activityName':'Walk','startTimeLocal':'2026-02-01 23:15:00'}]
    def download_activity(self,*args,**kwargs): return b'<gpx><trk><trkseg><trkpt lat="-0.0001" lon="0.0"/><trkpt lat="0.0001" lon="0.0"/></trkseg></trk></gpx>'

class Tests(unittest.TestCase):
    def setUp(self):
        app.api=FakeGarmin(); app.gear=[{'id':SOURCE,'name':'old'},{'id':REPLACEMENT,'name':'new'}]
        app.job.update(running=False,activities=[],source=SOURCE,error=None,review_id='review')
        app.app.config['SERVER_NAME']='localhost:8765'; self.c=app.app.test_client(); self.headers={'X-App-Token':app.nonce}
    def test_route_segment_and_unknown(self):
        self.assertLess(app.distance_to_route([[[-0.0001,0.0],[0.0001,0.0]]], TARGET),1)
        self.assertIsNone(app.distance_to_route([], TARGET))
        self.assertGreater(app.distance_to_route([[[0.002,0.0]]], TARGET),50)
    def test_distance_across_date_line(self):
        route = [[[0, 179.999], [0, -179.999]]]
        self.assertGreater(app.distance_to_route(route, (0, 0)), 19_000_000)
        self.assertLess(app.distance_to_route(route, (0, 180)), 0.1)
        self.assertAlmostEqual(app.distance_to_route([[[0, -179.9999]]], (0, 179.9999)), 22.24, delta=0.1)
        self.assertGreater(app.distance_to_route([[[0, 179.999], [0, -179.999]]], (0, 179.998)), 100)

    def test_run_retries_failed_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            shutil.copy(Path(app.ROOT) / 'run.sh', folder / 'run.sh')
            binary = folder / '.venv' / 'bin' / 'python'
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\nif [ \"$1\" = -m ]; then\n  if [ ! -f .failed ]; then touch .failed; exit 1; fi\n  touch .installed; exit 0\nfi\n[ -f .installed ] && touch .started\n")
            binary.chmod(0o755)
            first = subprocess.run(['sh', str(folder / 'run.sh')], capture_output=True)
            self.assertNotEqual(first.returncode, 0)
            second = subprocess.run(['sh', str(folder / 'run.sh')], capture_output=True)
            self.assertEqual(second.returncode, 0, second.stderr.decode())
            self.assertTrue((folder / '.started').exists())

    def test_load_and_batch_preserves_other_gear(self):
        app.operation_lock.acquire()
        app.load_worker(SOURCE,'2026-01-01','2026-12-31',target=TARGET)
        self.assertEqual(len(app.job['activities']),1)
        self.assertTrue(app.job['activities'][0]['near'])
        self.assertEqual(app.job['activities'][0]['date'], '2026-02-01')
        result=self.c.post('/apply',json={'review_id':'review','changes':[{'id':1,'gear':REPLACEMENT}]},headers=self.headers)
        self.assertTrue(result.json['outcomes'][0]['ok'])
        self.assertEqual(app.api.links,{REPLACEMENT,OTHER})
    def test_missing_token_and_missing_original(self):
        self.assertEqual(self.c.post('/apply',json={}).status_code,403)
        app.job['activities']=[{'id':1}]; app.api.links={OTHER}
        result=self.c.post('/apply',json={'review_id':'review','changes':[{'id':1,'gear':REPLACEMENT}]},headers=self.headers)
        self.assertFalse(result.json['outcomes'][0]['ok'])
        self.assertEqual(app.api.links,{OTHER})
    def test_cache_survives_load_and_refreshes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'TOKEN_DIR', Path(tmp)), patch.object(app, 'CACHE_FILE', Path(tmp) / 'activities.json'):
            app.api.display_name = 'test-user'
            app.operation_lock.acquire()
            app.load_worker(SOURCE, '2026-01-01', '2026-12-31')
            self.assertTrue(app.CACHE_FILE.exists())
            with patch.object(app.api, 'get_activities_by_date', side_effect=AssertionError('unexpected network fetch')):
                app.operation_lock.acquire()
                app.load_worker(SOURCE, '2026-01-01', '2026-12-31')
                self.assertIsNone(app.job['error'])
                self.assertIn('local cache', app.job['message'])
            with patch.object(app.api, 'get_activities_by_date', return_value=[]) as fetch:
                app.operation_lock.acquire()
                app.load_worker(SOURCE, '2026-01-01', '2026-12-31', True)
                fetch.assert_called_once()
                self.assertEqual(app.job['activities'], [])

    def test_old_cache_gets_dates_without_refetching_routes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'TOKEN_DIR', Path(tmp)), patch.object(app, 'CACHE_FILE', Path(tmp) / 'activities.json'):
            app.api.display_name = 'test-user'
            key = app.cache_key(SOURCE, '2026-01-01', '2026-12-31')
            old = {'id': 1, 'name': 'Walk', 'time': '23:15', 'route': [[[-0.0001, 0.0]]], 'near': True, 'route_error': None}
            app.cache_write({key: {'saved': 'old', 'activities': [old]}})
            with patch.object(app.api, 'download_activity', side_effect=AssertionError('route fetched again')):
                app.operation_lock.acquire()
                app.load_worker(SOURCE, '2026-01-01', '2026-12-31')
            self.assertIsNone(app.job['error'])
            self.assertEqual(app.job['activities'][0]['date'], '2026-02-01')
            self.assertEqual(app.cache_read()[key]['activities'][0]['date'], '2026-02-01')

    def test_cached_routes_recalculate_for_location_and_range(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'TOKEN_DIR', Path(tmp)), patch.object(app, 'CACHE_FILE', Path(tmp) / 'activities.json'):
            app.api.display_name = 'test-user'
            app.operation_lock.acquire()
            app.load_worker(SOURCE, '2026-01-01', '2026-12-31', target=TARGET, radius=50)
            self.assertTrue(app.job['activities'][0]['near'])
            with patch.object(app.api, 'download_activity', side_effect=AssertionError('route fetched again')):
                app.operation_lock.acquire()
                app.load_worker(SOURCE, '2026-01-01', '2026-12-31', target=(1.0, 1.0), radius=50)
                self.assertFalse(app.job['activities'][0]['near'])
                app.operation_lock.acquire()
                app.load_worker(SOURCE, '2026-01-01', '2026-12-31', target=None)
                self.assertIsNone(app.job['activities'][0]['near'])

    def test_optional_env_settings(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'ROOT', Path(tmp)), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app.load_settings(), {'gear':'', 'latitude':'', 'longitude':'', 'radius':'50'})
            (Path(tmp) / '.env').write_text('DEFAULT_GEAR_ID=example-id\nLOCATION_LATITUDE=12.5\nLOCATION_LONGITUDE=-34.5\nLOCATION_RADIUS_METERS=75\n')
            self.assertEqual(app.load_settings(), {'gear':'example-id', 'latitude':'12.5', 'longitude':'-34.5', 'radius':'75'})
            with patch.dict(os.environ, {'LOCATION_RADIUS_METERS':'100'}):
                self.assertEqual(app.load_settings()['radius'], '100')

    def test_location_validation(self):
        for location in ({'latitude':'0'}, {'latitude':'91','longitude':'0'}, {'latitude':'0','longitude':'0','radius':'-1'}, {'latitude':'nan','longitude':'0'}):
            result = self.c.post('/load', json={'source':SOURCE,'start':'2026-01-01','end':'2026-12-31',**location}, headers=self.headers)
            self.assertEqual(result.status_code, 400)

    def test_cache_updated_after_apply(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'TOKEN_DIR', Path(tmp)), patch.object(app, 'CACHE_FILE', Path(tmp) / 'activities.json'):
            app.api.display_name = 'test-user'
            app.operation_lock.acquire()
            app.load_worker(SOURCE, '2026-01-01', '2026-12-31')
            result = self.c.post('/apply', json={'review_id':'review','changes':[{'id':1,'gear':REPLACEMENT}]}, headers=self.headers)
            self.assertTrue(result.json['outcomes'][0]['ok'])
            app.operation_lock.acquire()
            app.load_worker(SOURCE, '2026-01-01', '2026-12-31')
            self.assertEqual(app.job['activities'], [])

    def test_failed_verification_keeps_source(self):
        app.job['activities']=[{'id':1}]
        with patch.object(app.api,'add_gear_to_activity',return_value=None):
            result=self.c.post('/apply',json={'review_id':'review','changes':[{'id':1,'gear':REPLACEMENT}]},headers=self.headers)
        self.assertFalse(result.json['outcomes'][0]['ok'])
        self.assertIn(SOURCE,app.api.links)

if __name__=='__main__': unittest.main()
