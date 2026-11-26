import unittest
from admissioncourt import admit,contract,projections,router,store


class RoutingTests(unittest.TestCase):
    def test_backend_state_changes_projection_winner(self):
        db=store.connect(':memory:'); self.addCleanup(db.close)
        for item in (
            {'event_id':'p','op':'propose','id':'guard','revision':1,'mode':'reject','mandatory':True,'owner':'alice'},
            {'event_id':'v1','op':'approve','id':'guard','revision':1,'role':'owner','actor':'alice','at':1,'expires':20},
            {'event_id':'v2','op':'approve','id':'guard','revision':1,'role':'security','actor':'bob','at':1,'expires':20},
            {'event_id':'a','op':'activate','id':'guard','revision':1,'at':2},
        ):
            contract.run(db,item)
        route={'id':'r','prefix':'/checkout','backends':['v2','v1']}
        baseline={'routes':[{**route,'backends':['v1']}]}
        candidate={'routes':[route]}
        admit.run(db,{'event_id':'p1','op':'submit','id':'p1','tenant':'t','uid':'u','at':3,'body':candidate})
        params={'request':'p1','baseline':baseline,'candidate':candidate,'probe_paths':['/checkout/eu'],'backends':{'v1':'healthy','v2':'healthy'}}
        projected=projections.run(db,params)
        self.assertEqual(projected['routes'][0]['after']['backend'],'v2')
        params['backends']['v2']='offline'
        projected=projections.run(db,params)
        self.assertEqual(projected['routes'][0]['after']['backend'],'v1')
        self.assertFalse(router.matches('/checkout','/checkout-v2'))
