"""Regression checks for dynamic multi-bank filters and safe global category operations."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.database import Database
from app.repositories import CategoryRepository, DatasetRepository
from app import api as backend
from walletwise.models import Transaction


class BankSelectionAndCategoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'test.db')
        self.transactions = DatasetRepository(self.db)
        self.categories = CategoryRepository(self.db)
        self.patcher_one = patch.object(backend, 'dataset_repository', self.transactions)
        self.patcher_two = patch.object(backend, 'category_repository', self.categories)
        self.patcher_one.start(); self.addCleanup(self.patcher_one.stop)
        self.patcher_two.start(); self.addCleanup(self.patcher_two.stop)
        self.client = TestClient(backend.app)
        self.add('FNB', 'Groceries', -100, 'g1')
        self.add('Capitec', 'Groceries', -200, 'g2')
        self.add('Absa', 'Clothing & Shoes', -300, 'g3')

    def add(self, bank, category, amount, slug):
        tx = Transaction(date=datetime(2025,10,1), raw_description='Retail ' + slug,
                         category=category, bank=bank, financial_role='expense',
                         amount=amount, balance=1000 + amount, transaction_type='debit')
        self.transactions.append_statement(statement_id=slug, filename=bank+'.pdf', bank=bank,
            file_hash='unique-'+slug, transactions=[('t-'+slug,tx)],
            statement_period={'verified':True,'start':'2025-10-01','end':'2025-10-31','source':'test'})

    def test_dynamic_banks_are_imported_only(self):
        banks = self.client.get('/api/dataset').json()['availableBanks']
        self.assertEqual(banks, ['Absa', 'Capitec', 'FNB'])

    def test_subset_across_three_banks_excludes_other_bank(self):
        result = self.client.get('/api/transactions', params={'bank':'Absa,Capitec'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual({row['bank'] for row in result.json()}, {'Absa', 'Capitec'})
        self.assertEqual(len(result.json()), 2)
        self.assertEqual(len(self.client.get('/api/transactions', params={'bank':'FNB'}).json()), 1)
        self.assertEqual(len(self.client.get('/api/transactions').json()), 3)
        self.assertEqual(self.client.get('/api/transactions', params={'bank':'Absa,unknown'}).status_code, 400)

    def test_subset_reporting_and_coverage(self):
        report = self.client.get('/api/reports', params={'bank':'Absa,Capitec'}).json()
        self.assertEqual(report['summary']['transactionCount'], 2)
        coverage = self.client.get('/api/coverage', params={'bank':'Absa,Capitec'}).json()
        self.assertEqual(coverage['start'], '2025-10-01')
        self.assertEqual(coverage['end'], '2025-10-31')

    def test_category_counts_respect_bank_filter(self):
        overview = self.client.get('/api/categories/overview', params={'bank':'Capitec'}).json()
        grocery = next(item for item in overview if item['name'] == 'Groceries')
        self.assertEqual(grocery['transactionCount'], 1)
        self.assertEqual(grocery['totalTransactionCount'], 2)
        clothing = next(item for item in overview if item['name'] == 'Clothing & Shoes')
        self.assertEqual(clothing['transactionCount'], 0)
        self.assertEqual(clothing['totalTransactionCount'], 1)

    def test_rename_updates_transactions_and_both_kinds_of_rules(self):
        self.categories.create('Clothing & Shoes')
        with self.db.connect() as connection:
            connection.execute("INSERT INTO naming_rules(match_text,display_name,category) VALUES('fashion','Fashion','Clothing & Shoes')")
            connection.execute("INSERT INTO category_rules(match_text,category) VALUES('boutique','Clothing & Shoes')")
        result = self.client.patch('/api/categories/Clothing%20%26%20Shoes', json={'name':'Apparel'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['transactionsUpdated'], 1)
        self.assertEqual(self.transactions.list_transactions('Absa')[0]['category'], 'Apparel')
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT category FROM naming_rules WHERE match_text='fashion'").fetchone()[0], 'Apparel')
            self.assertEqual(connection.execute("SELECT category FROM category_rules WHERE match_text='boutique'").fetchone()[0], 'Apparel')

    def test_merge_moves_across_all_banks_not_just_sidebar_filter(self):
        self.categories.create('Clothing & Shoes')
        result = self.client.post('/api/categories/merge', json={'source':'Clothing & Shoes','target':'Groceries'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['transactionsUpdated'], 1)
        self.assertEqual(self.transactions.list_transactions('Absa')[0]['category'], 'Groceries')
        self.assertNotIn('Clothing & Shoes', self.client.get('/api/categories').json())

    def test_delete_preserves_transaction_history(self):
        self.categories.create('Clothing & Shoes')
        result = self.client.delete('/api/categories/Clothing%20%26%20Shoes')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['transactionsUpdated'], 1)
        self.assertEqual(len(self.transactions.list_transactions()), 3)
        self.assertEqual(self.transactions.list_transactions('Absa')[0]['category'], 'Uncategorised')
        self.assertNotIn('Clothing & Shoes', self.client.get('/api/categories').json())

    def test_renamed_builtin_is_not_recreated_on_restart(self):
        result = self.categories.rename('Groceries', 'Food shopping')
        self.assertEqual(result['transactionsUpdated'], 2)
        Database(self.db.path)  # Re-run default seeding against same database.
        self.assertNotIn('Groceries', self.client.get('/api/categories').json())
        self.assertIn('Food shopping', self.client.get('/api/categories').json())

    def test_core_categories_cannot_be_destroyed(self):
        self.assertEqual(self.client.delete('/api/categories/Bank%20Fees').status_code, 400)
        self.assertEqual(self.client.post('/api/categories/merge', json={'source':'Savings','target':'Groceries'}).status_code, 400)
        self.assertEqual(self.client.patch('/api/categories/Uncategorised', json={'name':'Lost'}).status_code, 400)

    def test_create_restores_previous_deleted_category(self):
        self.categories.create('Clothing & Shoes')
        self.categories.delete('Clothing & Shoes')
        result = self.client.post('/api/categories', json={'name':'Clothing & Shoes'})
        self.assertEqual(result.status_code, 201)
        self.assertIn('Clothing & Shoes', self.client.get('/api/categories').json())

if __name__ == '__main__':
    unittest.main()
