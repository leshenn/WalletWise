"""End-to-end-ish savings regression checks with synthetic transactions only."""
from pathlib import Path
import sys
import tempfile
import unittest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.database import Database
from app.repositories import DatasetRepository
from app.reporting import ReportingService


class SavingsRefreshV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = DatasetRepository(Database(Path(self.tmp.name) / 'data.db'))
        with self.repo.database.connect() as db:
            db.execute('INSERT INTO statements (id,filename,bank,processed_at) VALUES (?,?,?,?)',
                       ('stmt', 'dummy.pdf', 'FNB', '2026-08-30'))
            for i, (tid, dt, amount, desc, category, role) in enumerate([
                ('old-savings', '2026-08-02', -500, 'To SAVINGS A/C', 'Savings', 'transfer'),
                ('ordinary', '2026-08-03', -200, 'Regular debit', 'Groceries', 'expense'),
                ('jul', '2026-07-20', -150, 'Old transfer', 'Savings', 'transfer'),
                ('salary', '2026-08-01', 3000, 'Salary', 'Salary', 'income'),
            ]):
                db.execute('INSERT INTO transactions '
                           '(id,statement_id,position,date,raw_description,display_name,category,financial_role,amount,transaction_type) '
                           'VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (tid, 'stmt', i, dt, desc, desc, category, role, amount, 'credit' if amount > 0 else 'debit'))

    def report(self):
        return ReportingService.build(self.repo.list_transactions())

    def monthly(self, month):
        return next(row for row in self.report()['monthly'] if row['month'] == month)

    def test_manual_category_correction_overrides_old_bank_description(self):
        before = self.report()
        self.assertEqual(before['savings']['netSavingsMovement'], 650)
        self.repo.update_transaction('old-savings', {
            'category': 'Groceries', 'categoryReason': 'Edited by user',
            'financialRole': 'transfer', 'humanVerified': True,
        })
        after = self.report()
        self.assertEqual(after['savings']['netSavingsMovement'], 150)
        self.assertEqual(self.monthly('2026-08')['netSavingsMovement'], 0)
        self.assertNotIn('old-savings', [r['id'] for r in after['savings']['savingsEntries']])

    def test_category_only_change_to_savings_is_visible_in_refreshed_report(self):
        before = self.report()
        self.repo.update_transaction('ordinary', {
            'category': 'Savings', 'categoryReason': 'Edited by user',
            'humanVerified': True,
        })
        after = self.report()
        self.assertEqual(self.repo.get_transaction('ordinary')['financialRole'], 'transfer')
        self.assertEqual(after['savings']['netSavingsMovement'] - before['savings']['netSavingsMovement'], 200)
        self.assertEqual(self.monthly('2026-08')['netSavingsMovement'], 700)
        self.assertIn('ordinary', [r['id'] for r in after['savings']['savingsEntries']])

    def test_manual_edit_in_july_leaves_august_unchanged(self):
        august_before = self.monthly('2026-08')['netSavingsMovement']
        self.repo.update_transaction('jul', {
            'category': 'Groceries', 'categoryReason': 'Edited by user',
            'financialRole': 'expense', 'humanVerified': True,
        })
        self.assertEqual(self.monthly('2026-08')['netSavingsMovement'], august_before)
        self.assertEqual(self.report()['savings']['netSavingsMovement'], august_before)

    def test_category_edit_to_transfer_excludes_old_bank_description(self):
        self.repo.update_transaction('old-savings', {
            'category': 'Transfer', 'financialRole': 'transfer',
        })
        self.assertEqual(self.repo.get_transaction('old-savings')['savingsDirection'], 'none')
        self.assertEqual(self.monthly('2026-08')['netSavingsMovement'], 0)

    def test_patch_then_get_reports_uses_updated_database_state(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app import api as routes
        with patch.object(routes, 'dataset_repository', self.repo):
            with TestClient(routes.app) as client:
                before = client.get('/api/reports')
                self.assertEqual(before.status_code, 200)
                self.assertEqual(before.json()['savings']['netSavingsMovement'], 650)
                updated = client.patch('/api/transactions/ordinary', json={
                    'category': 'Savings', 'financialRole': 'transfer',
                    'categoryReason': 'Edited by user', 'humanVerified': True,
                })
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()['category'], 'Savings')
                after = client.get('/api/reports', headers={'Cache-Control': 'no-store'})
                self.assertEqual(after.status_code, 200)
                self.assertEqual(after.json()['savings']['netSavingsMovement'], 850)
                self.assertIn('ordinary', [x['id'] for x in after.json()['savings']['savingsEntries']])

    def test_ledger_reconciles_to_all_report_periods(self):
        report = self.report()
        periods = [report['savings'], *report['monthly'], *report['payCycles']]
        for item in periods:
            total = round(sum(row['savingsEffect'] for row in item['savingsEntries']), 2)
            self.assertEqual(total, item['netSavingsMovement'])
            self.assertEqual(len(item['savingsEntries']), item['savingsTransactionCount'])


if __name__ == '__main__':
    unittest.main()
