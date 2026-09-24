import copy
import datetime as dt
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from rasterbench.common import load_config, percentile
from rasterbench.controller import estimate, jobs, finalists
from rasterbench.geoserver import check_image
from rasterbench.report import confidence, recommendations
from rasterbench.workload import mercator, view_tiles, tile_bbox, make_traces, seed_tiles, metatile_key


class Contracts(unittest.TestCase):
    def test_concurrent_checkpoint_writes_are_atomic(self):
        from concurrent.futures import ThreadPoolExecutor
        from rasterbench.common import write_json,read_json
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'checkpoint.json'
            with ThreadPoolExecutor(8) as pool:
                list(pool.map(lambda i:write_json(p,{'sequence':i}),range(32)))
            self.assertIn(read_json(p)['sequence'],range(32))
            self.assertFalse(list(Path(td).glob('*.tmp')))
    def setUp(self):
        self.c=load_config('/app/configs/full.yaml' if Path('/app/configs/full.yaml').exists() else 'configs/full.yaml')

    def test_percentiles_include_tails(self):
        self.assertEqual(percentile([1,2,3,4,100],.5),3)
        self.assertGreater(percentile([1,2,3,4,100],.99),95)
        self.assertIsNone(percentile([],.95))

    def test_matrix_has_unique_stable_cells(self):
        a=jobs(self.c,['none','jpeg80'],'screening')
        self.assertEqual(a,jobs(self.c,['none','jpeg80'],'screening'))
        self.assertEqual(len(a),32)
        self.assertEqual(len({j['id'] for j in a}),len(a))

    def test_world_tile_boundaries(self):
        for tile in ((0,0,0),(9,301,206),(14,9789,6580)):
            self.assertEqual(view_tiles(tile[0],tile_bbox(*tile)),[tile])

    def test_navigation_replays(self):
        c=copy.deepcopy(self.c); c['workload']['max_views_per_user']=8
        extent=[3800000,3500000,4300000,4300000]
        a=make_traces(c,extent,3,'hit')
        self.assertEqual(a,make_traces(c,extent,3,'hit'))
        for trace in a:
            for v in trace:
                self.assertGreaterEqual(v['bbox'][0],extent[0])
                self.assertLessEqual(v['bbox'][2],extent[2])
                self.assertTrue(3<=v['think']<=8)

    def test_miss_metatiles_do_not_repeat(self):
        c=copy.deepcopy(self.c); c['workload']['max_views_per_user']=12
        traces=make_traces(c,[3800000,3500000,4300000,4300000],4,'miss')
        keys=[]
        for trace in traces:
            for v in trace:
                ks={metatile_key(t) for t in v['tiles']}
                self.assertEqual(len(ks),1); keys.extend(ks)
        self.assertEqual(len(keys),len(set(keys)))

    def test_seed_keeps_whole_metatiles(self):
        c=copy.deepcopy(self.c); c['workload']['max_views_per_user']=8
        traces=make_traces(c,[3800000,3500000,4300000,4300000],3,'mixed')
        full=seed_tiles(traces,'hit',.8,123)
        part=seed_tiles(traces,'mixed',.8,123)
        keys={metatile_key(t) for t in part}
        self.assertEqual(set(part),{t for t in full if metatile_key(t) in keys})
        self.assertEqual(seed_tiles(traces,'miss',.8,123),[])

    def test_ogc_exception_is_not_success(self):
        with self.assertRaises(ValueError): check_image(b'<ServiceException>bad</ServiceException>','image/png',256,256)

    def test_corrupt_image_is_not_success(self):
        with self.assertRaises(Exception): check_image(b'\x89PNG\r\n\x1a\n','image/png',256,256)

    def test_decoded_image_shape_checked(self):
        from PIL import Image
        f=io.BytesIO(); Image.new('RGB',(16,16)).save(f,format='PNG')
        self.assertEqual(check_image(f.getvalue(),'image/png',16,16).size,(16,16))
        with self.assertRaises(ValueError): check_image(f.getvalue(),'image/png',256,256)

    def test_incomplete_capacity_cannot_recommend(self):
        p=self.c['study']['profiles'][0]
        row={'job':{'stage':'capacity','variant':'none','profile':p,'users':100,'scenario':'hit','repetition':0},
             'valid':True,'synthetic':False,'view_latency':{'p95':.1},'view_error_fraction':0,
             'resources':{'cpu_utilization_p95':.1,'memory_utilization_peak':.1}}
        self.assertFalse(recommendations(self.c,[row])['smallest_profiles'])

    def test_synthetic_never_qualifies(self):
        rows=[]; p=self.c['study']['profiles'][0]
        for scenario in self.c['study']['scenarios']:
            for rep in range(self.c['study']['capacity_repetitions']):
                rows.append({'job':{'stage':'capacity','variant':'none','profile':p,'users':100,'scenario':scenario,'repetition':rep},
                  'valid':True,'synthetic':True,'view_latency':{'p95':.1},'view_error_fraction':0,
                  'resources':{'cpu_utilization_p95':.1,'memory_utilization_peak':.1}})
        self.assertFalse(recommendations(self.c,rows)['smallest_profiles'])

    def test_interval_requires_repeats(self):
        self.assertIsNone(confidence([.1]))
        self.assertEqual(confidence([1,1,1]),[1,1])

    def test_fast_failed_views_cannot_win_shortlist(self):
        rows=[]
        for variant,error,latency in [('jpeg70',1.0,.01),('jpeg80',0.0,1.0)]:
            for scenario in ('wms','miss'):
                for rep in range(2):
                    rows.append({'job':{'variant':variant,'users':100,'scenario':scenario,'repetition':rep},
                                 'valid':True,'view_error_fraction':error,'view_latency':{'p95':latency}})
        selected,_=finalists(self.c,rows)
        self.assertIn('jpeg80',selected)
        self.assertNotIn('jpeg70',selected)

    def test_source_selection_adds_complementary_acquisition(self):
        from shapely.geometry import box
        from shapely.ops import unary_union
        from rasterbench.fetch import select
        date=dt.datetime(2026,7,1,tzinfo=dt.timezone.utc)
        def p(name,tile,geom,cloud):
            return SimpleNamespace(id=name,tile_id=tile,footprint=geom,online=True,cloud_cover=cloud,
                                   acquisition_start=date)
        # Two same-tile acquisitions overlap but neither covers the entire tile.
        ps=[p('a','T1',box(0,0,.8,1),1),p('b','T1',box(.2,0,1,1),2),p('sliver','T1',box(0,0,.01,1),0)]
        chosen=select(ps,box(0,0,1,1),4)
        self.assertEqual({x.id for x in chosen},{'a','b'})
        self.assertAlmostEqual(unary_union([x.footprint for x in chosen]).area,1)

    def test_insufficient_catalogue_is_rejected(self):
        from shapely.geometry import box
        from rasterbench.fetch import select
        with self.assertRaises(RuntimeError): select([],box(0,0,1,1),48)


if __name__=='__main__': unittest.main()
