import copy,unittest
from pathlib import Path
import yaml
from rasterbench.common import load_config
from rasterbench.overnight import resolve,batch,shortlist
from rasterbench.report import recommendations

class Overnight(unittest.TestCase):
    def setUp(self):
        root=Path('/app') if Path('/app/configs/full.yaml').exists() else Path('.')
        self.base=load_config(root/'configs/full.yaml')
        self.plan=yaml.safe_load((root/'configs/overnight-10.yaml').read_text())
        self.c=resolve(self.base,self.plan)
    def test_source_config_preserved_and_only_ten_users(self):
        self.assertEqual(self.base['workload']['max_users'],100)
        self.assertEqual(self.c['source'],self.base['source'])
        self.assertEqual(self.c['raster'],self.base['raster'])
        self.assertEqual(self.c['study']['capacity_users'],[10])
        self.assertEqual(self.c['workload']['max_users'],10)
    def test_schedule_count_and_users(self):
        p=self.c['study']['profiles'][2]
        jobs=batch(self.c,self.plan,[str(i) for i in range(10)],'source_comparison',[p],['miss','wms'],2)
        self.assertEqual(len(jobs),40)
        self.assertEqual({j['users'] for j in jobs},{10})
        self.assertEqual({j['measurement_seconds'] for j in jobs},{120})
        self.assertEqual(len({j['id'] for j in jobs}),40)
    def test_continuation_has_new_ids_without_user_escalation(self):
        c=resolve(self.base,self.plan,True);p=c['study']['profiles'][1]
        a=batch(c,self.plan,['none'],'endurance',[p],['wms'],1,cycle=0)
        b=batch(c,self.plan,['none'],'endurance',[p],['wms'],1,cycle=1)
        self.assertNotEqual(a[0]['id'],b[0]['id'])
        self.assertEqual(a[0]['measurement_seconds'],300)
        self.assertEqual(b[0]['users'],10)
        self.assertFalse(self.plan['stop_at_target'])
    def test_recommendations_scoped_to_ten_users(self):
        rows=[];p=self.c['study']['profiles'][1]
        for scenario in self.c['study']['scenarios']:
            for rep in range(2):
                rows.append({'job':{'stage':'capacity','variant':'none','profile':p,'users':10,'scenario':scenario,'repetition':rep},
                             'valid':True,'synthetic':False,'controller_complete':True,
                             'view_latency':{'p95':.1},'view_error_fraction':0,
                             'resources':{'cpu_utilization_p95':.1,'memory_utilization_peak':.1}})
        self.assertEqual(recommendations(self.c,rows)['smallest_profiles']['none']['id'],p['id'])
        self.assertFalse(recommendations(self.base,rows)['smallest_profiles'])
