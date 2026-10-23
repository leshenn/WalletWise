"""Savings accuracy, migration and audit tests use ONLY synthetic transactions."""
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from app.database import Database
from app.repositories import DatasetRepository
from app.reporting import ReportingService
from walletwise.processing.role_service import FinancialRoleService
from walletwise.models import Transaction


class SavingsTruthfulTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = DatasetRepository(Database(Path(self.tmp.name) / 'data.db'))
        with self.repo.database.connect() as db:
            db.execute('INSERT INTO statements(id,filename,bank,processed_at) VALUES (?,?,?,?)',
                       ('s','synthetic.pdf','FNB','2026-08-31'))
            examples = [
                ('deposit','2026-08-02',-1000,'Transfer','Savings','transfer'),
                ('withdraw','2026-08-05',500,'Transfer','Savings','transfer'),
                ('other','2026-08-06',-750,'Transfer to friend','Transfer','transfer'),
                ('salary','2026-08-01',3000,'Salary','Salary','income'),
                ('old','2026-07-11',-150,'To savings','Transfer','transfer'),
                ('fee','2026-08-10',-15,'Savings account fee','Bank Fees','expense'),
            ]
            for i,(ident,day,amount,desc,category,role) in enumerate(examples):
                db.execute('INSERT INTO transactions(id,statement_id,position,date,raw_description,display_name,category,financial_role,amount,transaction_type) VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (ident,'s',i,day,desc,desc,category,role,amount,'credit' if amount>0 else 'debit'))

    def report(self):
        return ReportingService.build(self.repo.list_transactions())

    def august(self):
        return next(row for row in self.report()['monthly'] if row['month']=='2026-08')

    def test_net_transfer_rate_accounts_for_withdrawals(self):
        a = self.august()
        self.assertEqual(a['netSavingsMovement'],500)
        self.assertAlmostEqual(a['savingsTransferRatePct'],16.67, places=1)
        self.assertAlmostEqual(a['savingsGrossTransferRatePct'],33.33, places=1)
        self.assertEqual(a['savingsUnconfirmedCount'],2)

    def test_manual_direction_overrides_bank_sign_and_persists(self):
        saved = self.repo.update_transaction('deposit', {'savingsDirection':'out','humanVerified':True})
        self.assertEqual(saved['savingsDirection'], 'out')
        self.assertEqual(self.august()['netSavingsMovement'],-1500)
        row = next(x for x in self.august()['savingsEntries'] if x['id']=='deposit')
        self.assertTrue(row['confirmed'])
        self.assertEqual(row['savingsEffect'],-1000)

    def test_user_can_exclude_false_positive_even_if_category_savings(self):
        self.repo.update_transaction('deposit', {'savingsDirection':'none'})
        a=self.august()
        self.assertEqual(a['netSavingsMovement'],-500)
        self.assertNotIn('deposit',[x['id'] for x in a['savingsEntries']])

    def test_category_edit_reclassifies_and_confirms(self):
        row=self.repo.update_transaction('other', {'category':'Savings','categoryReason':'Edited by user','humanVerified':True})
        self.assertEqual(row['financialRole'],'transfer')
        self.assertEqual(row['savingsDirection'],'into')
        a=self.august()
        self.assertEqual(a['netSavingsMovement'],1250)
        self.assertEqual(a['savingsUnconfirmedCount'],2)
        self.assertTrue(next(x for x in a['savingsEntries'] if x['id']=='other')['confirmed'])

    def test_manual_category_change_away_removes_explicit_tag(self):
        self.repo.update_transaction('deposit', {'savingsDirection':'into'})
        self.repo.update_transaction('deposit', {'category':'Groceries','categoryReason':'Edited by user','humanVerified':True})
        self.assertEqual(self.repo.get_transaction('deposit')['savingsDirection'],'none')
        self.assertEqual(self.august()['netSavingsMovement'],-500)

    def test_manual_role_change_removes_explicit_tag(self):
        self.repo.update_transaction('deposit', {'savingsDirection':'into'})
        self.repo.update_transaction('deposit', {'financialRole':'expense'})
        self.assertEqual(self.repo.get_transaction('deposit')['savingsDirection'],'none')
        self.assertEqual(self.august()['netSavingsMovement'],-500)

    def test_inferred_movement_is_distinguished_from_manual(self):
        r=self.report()['savings']
        self.assertEqual(r['netSavingsMovement'],650)
        self.assertEqual(r['confirmedSavingsMovement'],0)
        self.assertEqual(r['inferredSavingsMovement'],650)
        self.assertEqual(sum(e['savingsEffect'] for e in r['savingsEntries']),650)

    def test_income_role_does_not_allow_savings_direction_via_api(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from app import api
        with patch.object(api, 'dataset_repository', self.repo):
            with TestClient(api.app) as client:
                resp=client.patch('/api/transactions/salary', json={'savingsDirection':'into'})
                self.assertEqual(resp.status_code,400)
                resp=client.patch('/api/transactions/salary', json={'savingsDirection':'wut'})
                self.assertEqual(resp.status_code,422)

    def test_existing_database_migrates_without_erasing_old_data(self):
        path=Path(self.tmp.name)/'older.db'
        db=Database(path)
        with db.connect() as con:
            con.execute('INSERT INTO statements(id,filename,bank,processed_at) VALUES (?,?,?,?)', ('o','old.pdf','FNB','2026-07-01'))
            con.execute("INSERT INTO transactions(id,statement_id,position,amount) VALUES ('old','o',0,-25)")
            con.execute("ALTER TABLE transactions RENAME COLUMN savings_direction TO old_savings_dir")
        migrated=Database(path)
        self.assertEqual(DatasetRepository(migrated).get_transaction('old')['savingsDirection'],'')

    def test_report_does_not_count_savings_fee(self):
        self.assertNotIn('fee',[x['id'] for x in self.report()['savings']['savingsEntries']])
        # Older imports may have incorrectly given an account fee a transfer role.
        self.repo.update_transaction('fee', {'financialRole':'transfer'})
        self.assertNotIn('fee',[x['id'] for x in self.report()['savings']['savingsEntries']])

    def test_direction_words_override_signed_amount_in_legacy_imports(self):
        with self.repo.database.connect() as connection:
            connection.execute("UPDATE transactions SET raw_description = 'From savings', display_name = 'From savings' WHERE id = 'old'")
        r=self.report()['savings']
        old=next(x for x in r['savingsEntries'] if x['id']=='old')
        # Direction words override the signed amount for provisional imports.
        self.assertEqual(old['savingsEffect'],-150)


if __name__=='__main__':
    unittest.main()
