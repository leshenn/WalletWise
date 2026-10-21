import { useEffect, useState } from 'react';
import { AlertCircle, LoaderCircle, RefreshCw } from 'lucide-react';
import Layout from './components/Layout';
import DashboardPage from './pages/DashboardPage';
import ReportsPage from './pages/ReportsPage';
import BudgetPage from './pages/BudgetPage';
import ReviewPage from './pages/ReviewPage';
import RulesPage from './pages/RulesPage';
import TransactionsPage from './pages/TransactionsPage';
import UploadPage from './pages/UploadPage';
import AskPage from './pages/AskPage';
import { api } from './services/api';

const emptyReporting = {
  summary: {
    income: 0,
    spending: 0,
    refunds: 0,
    transfers: 0,
    netCashFlow: 0,
    transactionCount: 0,
  },
  savings: { savingsTransfersOut: 0, savingsTransfersIn: 0, netSavingsMovement: 0, savingsTransferRatePct: null },
  monthly: [],
  availableMonths: [],
  payCycles: [],
  categoryTrend: { categories: [], points: [] },
  allMonthsComparison: {
    completeOnly: { monthCount: 0, months: [], categoryTrend: { categories: [], points: [] } },
    includingPartial: { monthCount: 0, months: [], categoryTrend: { categories: [], points: [] } },
    hasPartialMonths: false,
  },
  categoryBreakdown: [],
  merchantBreakdown: [],
  metrics: {},
  recurringMerchants: [],
  insights: [],
  confidence: {},
  budget: { profile: null, profileCompleted: false, incomeBasis: 0, essentialBudget: 0, flexibleBudget: 0, plannedExpenses: 0, savingsTarget: 0, savingsTargetPct: 0, bufferTarget: 0, remainingAfterPlan: 0, items: [], availableActualPeriods: [] },
};

export default function App() {
  const [page, setPage] = useState('dashboard');
  const [transactions, setTransactions] = useState([]);
  const [reporting, setReporting] = useState(emptyReporting);
  const [reviewCount, setReviewCount] = useState(0);
  const [reviewReadyCount, setReviewReadyCount] = useState(0);
  const [rules, setRules] = useState([]);
  const [categoryRules, setCategoryRules] = useState([]);
  const [categories, setCategories] = useState([]);
  const [lastStatement, setLastStatement] = useState(null);
  const [statements, setStatements] = useState([]);
  const [coverage, setCoverage] = useState({ months: [], missingMonths: [] });
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState('');

  const loadDerivedState = async () => {
    const [reportsResponse, reviewResponse] = await Promise.all([
      api.getReports(),
      api.getReviewGroups(),
    ]);
    setReporting(reportsResponse || emptyReporting);
    setReviewCount(Number(reviewResponse?.totalCount || 0));
    setReviewReadyCount(Number(reviewResponse?.readyCount || 0));
  };

  const loadBackendState = async () => {
    setLoading(true);
    setConnectionError('');
    try {
      const [dataset, rulesResponse, categoryRulesResponse, categoriesResponse, reportsResponse, reviewResponse] = await Promise.all([
        api.getDataset(),
        api.getRules(),
        api.getCategoryRules(),
        api.getCategories(),
        api.getReports(),
        api.getReviewGroups(),
      ]);
      setTransactions(dataset.transactions || []);
      setLastStatement(dataset.statement || null);
      setStatements(dataset.statements || []);
      setCoverage(dataset.coverage || { months: [], missingMonths: [] });
      setRules(rulesResponse || []);
      setCategoryRules(categoryRulesResponse || []);
      setCategories(categoriesResponse || []);
      setReporting(reportsResponse || emptyReporting);
      setReviewCount(Number(reviewResponse?.totalCount || 0));
      setReviewReadyCount(Number(reviewResponse?.readyCount || 0));
    } catch (error) {
      setConnectionError(error.message || 'Could not connect to WalletWise.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadBackendState();
  }, []);

  const refreshTransactions = async () => {
    const [current, currentStatements, currentCoverage] = await Promise.all([
      api.getTransactions(),
      api.getStatements(),
      api.getCoverage(),
    ]);
    setTransactions(current || []);
    setStatements(currentStatements || []);
    setLastStatement(currentStatements?.[0] || null);
    setCoverage(currentCoverage || { months: [], missingMonths: [] });
    await loadDerivedState();
  };

  const handleProcessed = async (result) => {
    setLastStatement({
      statementId: result.statementId,
      filename: result.filename,
      bank: result.bank,
      transactionCount: result.transactionCount,
      periodStart: result.periodStart,
      periodEnd: result.periodEnd,
    });
    setTransactions(result.transactions || []);
    setStatements(result.statements || []);
    setCoverage(result.coverage || { months: [], missingMonths: [] });
    await loadDerivedState();
  };

  if (loading) {
    return (
      <div className="boot-screen">
        <LoaderCircle className="spin" size={28} />
        <strong>Connecting to WalletWise…</strong>
        <span>Loading your financial data.</span>
      </div>
    );
  }

  if (connectionError) {
    return (
      <div className="boot-screen boot-error">
        <AlertCircle size={30} />
        <strong>WalletWise is not reachable</strong>
        <span>{connectionError}</span>
        <code>{api.baseUrl}</code>
        <button className="primary-button" onClick={loadBackendState}><RefreshCw size={17} /> Try again</button>
      </div>
    );
  }

  const pages = {
    dashboard: <DashboardPage transactions={transactions} reporting={reporting} reviewCount={reviewCount} reviewReadyCount={reviewReadyCount} setPage={setPage} lastStatement={lastStatement} />,
    upload: <UploadPage onProcessed={handleProcessed} setPage={setPage} categories={categories} setCategories={setCategories} statements={statements} coverage={coverage} refreshTransactions={refreshTransactions} />,
    transactions: <TransactionsPage transactions={transactions} refreshTransactions={refreshTransactions} categories={categories} />,
    review: <ReviewPage transactions={transactions} refreshTransactions={refreshTransactions} categories={categories} setCategories={setCategories} />,
    rules: <RulesPage rules={rules} setRules={setRules} categoryRules={categoryRules} setCategoryRules={setCategoryRules} categories={categories} refreshTransactions={refreshTransactions} />,
    reports: <ReportsPage transactions={transactions} reporting={reporting} refreshReports={loadDerivedState} setPage={setPage} />,
    budget: <BudgetPage reporting={reporting} refreshReports={loadDerivedState} />,
    ask: <AskPage />,
  };

  return (
    <Layout page={page} setPage={setPage} reviewCount={reviewCount}>
      {pages[page] || pages.dashboard}
    </Layout>
  );
}
