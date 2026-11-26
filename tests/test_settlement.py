import json
import unittest
from contextlib import closing
from pathlib import Path
from admissioncourt import admit, contract, receiver, settlement, waiver
from admissioncourt.store import connect, one


def fixture(name):
    return json.loads((Path('fixtures') / (name + '.json')).read_text(encoding='utf-8'))


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.db = self.enterContext(closing(connect(':memory:')))
        for module in (contract, waiver, admit):
            for action in fixture(module.__name__.split('.')[-1] + '_normal'):
                module.run(self.db, action)
        out = receiver.run(self.db, fixture('receiver_normal'))
        self.assertEqual(out['receipts'][0]['decision'], 'applied')
        self.invoice = fixture('settlement_normal')

    def test_issue_and_replay_lock_in_amount(self):
        first = settlement.run(self.db, self.invoice)
        self.assertEqual(first['total_cents'], 1200)
        self.assertEqual(first, settlement.run(self.db, self.invoice))
        revised = {**self.invoice, 'tariffs': {'delivery-a': 500}}
        self.assertEqual(settlement.run(self.db, revised)['decision'], 'invoice_identity_conflict')
        self.assertEqual(one(self.db, 'SELECT count(*) n FROM settlement_invoice_lines')['n'], 1)

    def test_unapplied_reservation_does_not_accrue(self):
        failed = {**self.invoice, 'deliveries': ['deliver-a', 'deliver-b']}
        self.assertEqual(settlement.run(self.db, failed), {'decision': 'receipt_missing', 'delivery': 'deliver-b'})
        self.assertEqual(one(self.db, 'SELECT count(*) n FROM settlement_invoices')['n'], 0)
        self.assertEqual(settlement.run(self.db, self.invoice)['decision'], 'issued')

    def test_receipt_cannot_be_invoiced_twice(self):
        settlement.run(self.db, self.invoice)
        retry = {**self.invoice, 'invoice': 'different-period'}
        self.assertEqual(settlement.run(self.db, retry)['decision'], 'delivery_already_invoiced')
        self.assertEqual(one(self.db, 'SELECT count(*) n FROM settlement_invoices')['n'], 1)
