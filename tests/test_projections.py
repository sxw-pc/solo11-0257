import json
import unittest
from pathlib import Path

from admissioncourt import admit, contract, projections, store


class ProjectionTests(unittest.TestCase):
    def test_compare_only_the_actual_submitted_candidate(self):
        db = store.connect(':memory:')
        self.addCleanup(db.close)
        for command in json.loads(Path('fixtures/contract_normal.json').read_text(encoding='utf-8')):
            contract.run(db,command)
        submit = json.loads(Path('fixtures/admit_normal.json').read_text(encoding='utf-8'))[0]
        admit.run(db,submit)
        fixture = json.loads(Path('fixtures/projections_normal.json').read_text(encoding='utf-8'))
        result = projections.run(db,fixture)
        self.assertEqual(result['changes'],[
            {'path':'/image','change':'replaced','before':'v0','value':'v1'},
            {'path':'/replicas','change':'removed','value':2}])
        self.assertEqual(projections.run(db,{**fixture,'candidate':{'image':'other'}})['decision'],'request_content_mismatch')

    def test_json_pointer_escape_and_distinct_null(self):
        result = list(projections.changes({'a/b':None},{'a~c':None,'a/b':'null'}))
        self.assertEqual([r['path'] for r in result],['/a~1b','/a~0c'])
        self.assertEqual(result[0]['change'],'replaced')
        self.assertEqual(result[1]['change'],'added')
