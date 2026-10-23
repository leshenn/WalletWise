"""Synthetic FNB statement period/year integration and annual reporting regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from walletwise.config import AppConfig
from walletwise.extraction.banks.fnb.extractor import FNBStatementExtractor
from walletwise.extraction.banks.fnb.statement_period import (
    parse_statement_period_text, detect_statement_period,
)
from app.database import Database
from app.repositories import (
    DatasetRepository, NamingRuleRepository, CategoryRuleRepository, SalarySourceRepository,
)
from app.reporting import ReportingService
from app.services import StatementProcessingService

HEADER = 'Statement Period : 6 November 2025 to 6 February 2026'


def synthetic_pdf(path, *, header=HEADER, extra_year='Copyright 2027'):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=595, height=842)
        page.insert_text((40, 35), 'First National Bank', fontsize=9)
        page.insert_text((230, 61), 'Tax Invoice/Statement Number : 12', fontsize=10)
        page.insert_text((230, 79), header, fontsize=8)
        page.insert_text((230, 97), 'Statement Date : 6 February 2026', fontsize=10)
        page.insert_text((40, 119), extra_year, fontsize=8)
        for col, x in [('Date', 12), ('Description', 48), ('Amount', 425), ('Balance', 490), ('Charges', 552)]:
            page.insert_text((x, 192), col, fontsize=8)
        for y, day, name, amt, balance in [
            (210, '06 Nov', 'Purchase November', '100.00Dr', '1400.00Cr'),
            (226, '10 Dec', 'Purchase December', '50.00Dr', '1350.00Cr'),
            (242, '15 Jan', 'Purchase January', '200.00Dr', '1150.00Cr'),
            (258, '06 Feb', 'Income February', '100.00Cr', '1250.00Cr'),
        ]:
            for string, x in [(day,12), (name,48), (amt,425), (balance,490)]:
                page.insert_text((x,y), string, fontsize=8)
        page.insert_text((48, 279), 'Closing Balance', fontsize=8)
        page.insert_text((490, 279), '1250.00Cr', fontsize=8)
        pdf.save(path)


class FNBImportYearTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.pdf = self.root / 'statement.pdf'
        synthetic_pdf(self.pdf)

    def test_header_period_beats_statement_date_and_copyright_year(self):
        with pymupdf.open(self.pdf) as pdf:
            period = detect_statement_period(pdf, ocr_enabled=False)
        self.assertEqual((period.start.year, period.end.year), (2025, 2026))
        self.assertEqual(period.source, 'PDF selectable text')

    def test_live_extractor_cross_year_and_debug_artifacts(self):
        config = AppConfig(pdf_file=str(self.pdf), tesseract_cmd=None, generate_debug_pdfs=True,
                           debug_prefix=str(self.root / 'test'))
        extractor = FNBStatementExtractor(config)
        result = extractor.extract(str(self.pdf))
        self.assertEqual(result.statement_period['start'], '2025-11-06')
        self.assertEqual(result.statement_period['end'], '2026-02-06')
        dates = [item.date.date().isoformat() for item in result.transactions if item.transaction_type != 'closing_balance']
        self.assertEqual(dates, ['2025-11-06', '2025-12-10', '2026-01-15', '2026-02-06'])
        debug_path = self.root / 'test_FNB_Period_Debug.json'
        self.assertTrue(debug_path.is_file())
        trace = json.loads(debug_path.read_text())
        self.assertEqual(trace['accepted_dates'][0]['date'], '2025-11-06')
        self.assertTrue((self.root / 'test_FNB_Period_Debug.pdf').is_file())
        with pymupdf.open(self.root / 'test_FNB_Period_Debug.pdf') as doc:
            self.assertGreaterEqual(len(list(doc[0].annots() or [])), 1)

    def test_missing_header_fails_before_database_import(self):
        synthetic_pdf(self.pdf, header='Account Summary: 6 February 2026')
        extractor = FNBStatementExtractor(AppConfig(generate_debug_pdfs=False, tesseract_cmd=None))
        with self.assertRaisesRegex(ValueError, 'Statement Period could not be verified'):
            extractor.extract(str(self.pdf))

    def test_invalid_header_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Invalid FNB Statement Period'):
            parse_statement_period_text('Statement Period : 31 February 2025 to 6 March 2025')

    def test_live_upload_database_coverage_and_annual_reporting(self):
        db = Database(self.root / 'test.db')
        dataset = DatasetRepository(db)
        service = StatementProcessingService(
            dataset_repository=dataset,
            rule_repository=NamingRuleRepository(db),
            category_rule_repository=CategoryRuleRepository(db),
            salary_source_repository=SalarySourceRepository(db),
            data_directory=self.root,
        )
        with patch.dict('os.environ', {'WALLETWISE_IMPORT_DEBUG': '0'}):
            response = service.process_pdf_bytes(filename='test.pdf', contents=self.pdf.read_bytes())
        self.assertEqual(response['periodDetection']['start_year'], 2025)
        self.assertEqual(response['periodDetection']['end_year'], 2026)
        self.assertEqual(response['periodStart'], '2025-11-06')
        self.assertEqual(response['periodEnd'], '2026-02-06')
        self.assertTrue(response['periodVerified'])
        self.assertEqual(len(dataset.list_statements()), 1)
        self.assertEqual(dataset.coverage()['start'], '2025-11-06')
        self.assertEqual(dataset.coverage()['end'], '2026-02-06')
        transaction_years = {str(item['date'])[:4] for item in dataset.list_transactions() if item['transactionType'] != 'closing_balance'}
        self.assertEqual(transaction_years, {'2025', '2026'})
        reports = ReportingService.build(dataset.list_transactions(), coverage=dataset.coverage())
        self.assertEqual(reports['availableYears'], [2025, 2026])
        self.assertEqual([entry['year'] for entry in reports['yearly']], [2025, 2026])
        self.assertFalse(reports['yearly'][0]['complete'])
        self.assertFalse(reports['yearly'][1]['complete'])
        self.assertEqual([m['month'] for m in reports['monthly']], ['2025-11', '2025-12', '2026-01', '2026-02'])
        # Importing the identical file must not duplicate transactions / annual figures.
        with self.assertRaisesRegex(ValueError, 'already been imported'):
            service.process_pdf_bytes(filename='test.pdf', contents=self.pdf.read_bytes())
        self.assertEqual(len(dataset.list_statements()), 1)
        self.assertEqual(len(dataset.list_transactions()), len(response['transactions']))

    def test_header_range_is_not_shortened_to_first_and_last_transaction(self):
        synthetic_pdf(self.pdf, header='Statement Period : 1 November 2025 to 12 February 2026')
        db = Database(self.root / 'coverage.db')
        service = StatementProcessingService(
            dataset_repository=DatasetRepository(db),
            rule_repository=NamingRuleRepository(db),
            category_rule_repository=CategoryRuleRepository(db),
            salary_source_repository=SalarySourceRepository(db),
            data_directory=self.root,
        )
        with patch.dict('os.environ', {'WALLETWISE_IMPORT_DEBUG': '0'}):
            result = service.process_pdf_bytes(filename='coverage.pdf', contents=self.pdf.read_bytes())
        self.assertEqual(result['periodStart'], '2025-11-01')
        self.assertEqual(result['periodEnd'], '2026-02-12')
        self.assertEqual(result['coverage']['start'], '2025-11-01')
        self.assertEqual(result['coverage']['end'], '2026-02-12')
        self.assertEqual(result['coverage']['months'][0]['coveredDays'], 30)
        self.assertEqual(result['coverage']['months'][-1]['coveredDays'], 12)

    def test_yearly_totals_never_group_november_into_february_year(self):
        tx = [
            {'date':'2025-11-08','financialRole':'expense','amount':-100,'category':'Groceries'},
            {'date':'2025-12-08','financialRole':'income','amount':1000,'category':'Salary'},
            {'date':'2026-01-08','financialRole':'expense','amount':-300,'category':'Groceries'},
            {'date':'2026-02-03','financialRole':'income','amount':2000,'category':'Salary'},
        ]
        coverage = {'months':[
            {'month':'2025-11','status':'partial','coveredDays':25,'coveragePct':83.3},
            {'month':'2025-12','status':'covered','coveredDays':31,'coveragePct':100},
            {'month':'2026-01','status':'covered','coveredDays':31,'coveragePct':100},
            {'month':'2026-02','status':'partial','coveredDays':6,'coveragePct':21.4},
        ]}
        report = ReportingService.build(tx, coverage=coverage)
        self.assertEqual(report['availableYears'], [2025,2026])
        prior, current = report['yearly']
        self.assertEqual((prior['income'],prior['spending']), (1000, 100))
        self.assertEqual((current['income'],current['spending']), (2000, 300))
        self.assertEqual((prior['netCashFlow'],current['netCashFlow']), (900,1700))
        self.assertFalse(prior['complete'])
        self.assertFalse(current['complete'])

    def test_existing_db_migration_adds_period_source_without_dropping_data(self):
        db = Database(self.root / 'legacy.db')
        with db.connect() as con:
            con.execute("INSERT INTO statements (id, filename, bank, processed_at, period_start, period_end) VALUES ('old','old.pdf','FNB','2024-02-01','2024-01-05','2024-01-28')")
        reopened = Database(self.root / 'legacy.db')
        rows = DatasetRepository(reopened).list_statements()
        self.assertEqual(rows[0]['periodStart'], '2024-01-05')
        self.assertFalse(rows[0]['periodVerified'])


if __name__ == '__main__':
    unittest.main()
