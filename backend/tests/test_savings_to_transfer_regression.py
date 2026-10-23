"""Savings -> ordinary Transfer must not become 'withdrawal from savings'."""
from pathlib import Path
import tempfile
import unittest

from app.database import Database
from app.repositories import DatasetRepository
from app.reporting import ReportingService


class SavingsToTransferRegression(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = DatasetRepository(Database(Path(temp.name) / 'synthetic.db'))
        with self.repo.database.connect() as con:
            con.execute("INSERT INTO statements(id, filename, bank, processed_at) VALUES ('fnb','synthetic.pdf','FNB','2026-08-31')")
            rows = [
                ('a','2026-08-02',-200,'Transfer to savings','Savings','transfer', 'into'),
                ('b','2026-08-03',300,'From savings account','Savings','transfer', 'out'),
                ('c','2026-08-04',-700,'To savings','Savings','transfer', ''),
            ]
            for i, (ident, day, amount, label, category, role, direction) in enumerate(rows):
                con.execute('''INSERT INTO transactions
                    (id,statement_id,position,date,amount,raw_description,display_name,category,financial_role,savings_direction,transaction_type)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (ident,'fnb',i,day,amount,label,label,category,role,direction,'debit' if amount < 0 else 'credit'))

    def savings(self):
        return ReportingService.build(self.repo.list_transactions())['savings']

    def test_explicit_savings_to_transfer_never_counts_as_withdrawal(self):
        self.assertEqual(self.savings()['netSavingsMovement'], 600)
        record = self.repo.update_transaction('a', {
            'category':'Transfer','financialRole':'transfer',
            'humanVerified':True,'categoryReason':'Edited by user',
            # Simulate an older UI accidentally re-sending the stale savings tag.
            'savingsDirection':'out',
        })
        self.assertEqual(record['category'], 'Transfer')
        self.assertEqual(record['financialRole'], 'transfer')
        self.assertEqual(record['savingsDirection'], 'none')
        report = self.savings()
        self.assertEqual(report['netSavingsMovement'], 400)
        self.assertNotIn('a', [r['id'] for r in report['savingsEntries']])
        self.assertEqual(report['savingsTransfersIn'], 300)  # the real withdrawal

    def test_old_auto_detected_savings_to_transfer_does_not_reappear(self):
        self.repo.update_transaction('c', {'category':'Transfers'})
        self.assertEqual(self.repo.get_transaction('c')['savingsDirection'], 'none')
        self.assertEqual(self.repo.get_transaction('c')['financialRole'], 'transfer')
        report = self.savings()
        self.assertNotIn('c', [r['id'] for r in report['savingsEntries']])

    def test_changing_an_existing_withdrawal_to_regular_transfer_removes_it(self):
        self.repo.update_transaction('b', {'category':'Transfer','financialRole':'transfer'})
        self.assertEqual(self.repo.get_transaction('b')['savingsDirection'], 'none')
        report = self.savings()
        self.assertEqual(report['netSavingsMovement'], 900)
        self.assertEqual(report['savingsTransfersIn'], 0)

    def test_reopening_a_regular_transfer_preserves_no_savings_status(self):
        self.repo.update_transaction('a', {'category':'Transfer','humanVerified':True})
        first = self.repo.get_transaction('a')
        self.assertEqual(first['savingsDirection'], 'none')
        self.repo.update_transaction('a', {'displayName':'Rent paid', 'financialRole':'transfer'})
        after = self.repo.get_transaction('a')
        self.assertEqual(after['savingsDirection'], 'none')
        self.assertNotIn('a', [r['id'] for r in self.savings()['savingsEntries']])

    def test_batch_reclassification_clears_previous_savings_markers(self):
        self.repo.update_transactions(['a','b'], {'category':'Transfer', 'financialRole':'transfer'})
        self.assertEqual(self.repo.get_transaction('a')['savingsDirection'], 'none')
        self.assertEqual(self.repo.get_transaction('b')['savingsDirection'], 'none')
        self.assertEqual(self.savings()['netSavingsMovement'], 700)

    def test_http_patch_and_fresh_report_return_no_savings_withdrawal(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app import api
        with patch.object(api, 'dataset_repository', self.repo):
            with TestClient(api.app) as client:
                before = client.get('/api/reports')
                self.assertEqual(before.status_code, 200)
                self.assertEqual(before.json()['savings']['netSavingsMovement'], 600)
                updated = client.patch('/api/transactions/b', json={
                    'category':'Transfer', 'financialRole':'transfer',
                    'categoryReason':'Edited by user', 'humanVerified':True,
                })
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()['savingsDirection'], 'none')
                after = client.get('/api/reports', headers={'Cache-Control':'no-store'})
                self.assertEqual(after.status_code, 200)
                self.assertEqual(after.json()['savings']['netSavingsMovement'], 900)
                self.assertNotIn('b',[entry['id'] for entry in after.json()['savings']['savingsEntries']])

    def test_legacy_manually_verified_normal_transfer_is_not_savings(self):
        with self.repo.database.connect() as con:
            con.execute('''UPDATE transactions SET category='Transfer', category_reason='',
                human_verified=1, savings_direction='' WHERE id='a' ''')
        self.assertNotIn('a', [r['id'] for r in self.savings()['savingsEntries']])

if __name__ == '__main__':
    unittest.main()
