import unittest
from scripts.finish_endurance import cycle_complete


class FinishCycle(unittest.TestCase):
    def test_requires_every_completed_cell_in_requested_cycle_at_ten_users(self):
        variants=['none','packbits','cog_jpeg80']
        rows=[{'controller_complete':True,'job':{'variant':v,'scenario':s,
                'cycle':1,'phase':'endurance','plan_id':'overnight-10','users':10}}
              for v in variants for s in ('hit','miss','mixed','wms')]
        self.assertTrue(cycle_complete(rows,1,variants))
        self.assertFalse(cycle_complete(rows[:-1],1,variants))
        self.assertFalse(cycle_complete(rows,2,variants))
        rows[-1]['controller_complete']=False
        self.assertFalse(cycle_complete(rows,1,variants))
        rows[-1]['controller_complete']=True;rows[-1]['job']['users']=100
        self.assertFalse(cycle_complete(rows,1,variants))
