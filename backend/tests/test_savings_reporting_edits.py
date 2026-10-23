"""Regression tests for savings edits and report refresh inputs.

All values are synthetic. No personal statement or model API is required.
"""
from pathlib import Path
import sys
import tempfile
import unittest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.database import Database
from app.reporting import ReportingService
from app.repositories import DatasetRepository


class SavingsEditingReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / 'test.sqlite3')
        self.repo = DatasetRepository(self.db)
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO statements(id, filename, bank, processed_at, file_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                ('sample', 'test.pdf', 'FNB', '2026-08-31', 'fake'),
            )
            transactions = [
                # Categorised only (historical edit) but still marked expense.
                ('old', '2026-08-02', -500, 'Expense', 'Savings', 'expense'),
                # Clearly an ordinary transfer, not a savings transfer.
                ('ordinary', '2026-08-03', -200, 'Rent split', 'Transfer', 'transfer'),
                # Savings out and money moved back in the same month.
                ('out', '2026-08-04', -250, 'To savings', 'Transfer', 'transfer'),
                ('back', '2026-08-05', 100, 'From savings', 'Transfer', 'transfer'),
                # July savings should not change August's savings figure.
                ('july', '2026-07-18', -300, 'To savings', 'Savings', 'transfer'),
                ('salary', '2026-08-01', 4000, 'Salary payment', 'Salary', 'income'),
            ]
            for index, (id_, day, amount, name, category, role) in enumerate(transactions):
                connection.execute(
                    'INSERT INTO transactions '
                    '(id,statement_id,position,date,raw_description,display_name,category,financial_role,amount,transaction_type) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (id_, 'sample', index, day, name, name, category, role, amount,
                     'debit' if amount < 0 else 'credit'),
                )

    def report(self):
        return ReportingService.build(self.repo.list_transactions())

    def august(self, report):
        return next(row for row in report['monthly'] if row['month'] == '2026-08')

    def test_reports_explain_an_old_savings_category_edit(self):
        report = self.report()
        self.assertEqual(report['savings']['netSavingsMovement'], 450)
        self.assertEqual(report['savings']['savingsTransactionCount'], 3)
        self.assertEqual(report['savings']['savingsUncountedCount'], 1)
        self.assertEqual(report['savings']['savingsCorrectableIds'], ['old'])
        self.assertEqual(self.august(report)['netSavingsMovement'], 150)
        self.assertEqual(self.august(report)['savingsUncountedCount'], 1)

    def test_explicit_reclassification_updates_overall_and_selected_period(self):
        before = self.report()
        self.repo.update_transactions(['old'], {
            'financialRole': 'transfer', 'humanVerified': True,
        })
        after = self.report()
        self.assertEqual(after['savings']['netSavingsMovement'] - before['savings']['netSavingsMovement'], 500)
        self.assertEqual(self.august(after)['netSavingsMovement'] - self.august(before)['netSavingsMovement'], 500)
        self.assertEqual(after['savings']['savingsUncountedCount'], 0)
        self.assertEqual(after['summary']['spending'], before['summary']['spending'] - 500)
        self.assertEqual(after['savings']['savingsTransactionCount'], 4)

    def test_category_only_edit_defaults_to_transfer_and_recalculates(self):
        updated = self.repo.update_transaction('old', {'category': 'Savings'})
        self.assertEqual(updated['financialRole'], 'transfer')
        self.assertEqual(self.august(self.report())['netSavingsMovement'], 650)
        self.assertEqual(self.report()['savings']['savingsUncountedCount'], 0)

    def test_explicit_expense_role_not_silently_changed(self):
        updated = self.repo.update_transaction('old', {
            'category': 'Savings', 'financialRole': 'expense',
        })
        self.assertEqual(updated['financialRole'], 'expense')
        self.assertEqual(self.report()['savings']['savingsUncountedCount'], 1)

    def test_edited_july_transaction_does_not_change_august(self):
        before = self.report()
        self.repo.update_transaction('july', {'financialRole': 'expense', 'category': 'Groceries'})
        after = self.report()
        self.assertEqual(self.august(before)['netSavingsMovement'], self.august(after)['netSavingsMovement'])
        self.assertEqual(after['savings']['netSavingsMovement'], 150)

    def test_incoming_income_category_savings_is_flagged_not_auto_corrected(self):
        # Converting salary to a transfer must never happen as an unapproved
        # side effect of a report repair button.
        self.repo.update_transaction('salary', {'category': 'Savings', 'financialRole': 'income'})
        data = self.report()['savings']
        self.assertEqual(data['savingsUncountedCount'], 2)
        self.assertEqual(data['savingsCorrectableIds'], ['old'])


if __name__ == '__main__':
    unittest.main()
