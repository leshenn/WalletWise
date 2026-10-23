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
import CategoriesPage from './pages/CategoriesPage';
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
  const [bankFilter, setBankFilter] = useState(() => window.localStorage.getItem('walletwise.bankScope') || 'all');
  const [availableBanks, setAvailableBanks] = useState([]);
  const [banksLoaded, setBanksLoaded] = useState(false);
  // Configure the API before any fetches, including initial page effects.
  api.setBankScope(bankFilter);
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
    const requestedBank = api.getBankScope();
    const [reportsResponse, reviewResponse] = await Promise.all([
      api.getReports(),
      api.getReviewGroups(),
    ]);
    if (api.getBankScope() !== requestedBank) return reportsResponse;
    setReporting(reportsResponse || emptyReporting);
    setReviewCount(Number(reviewResponse?.totalCount || 0));
    setReviewReadyCount(Number(reviewResponse?.readyCount || 0));
    return reportsResponse;
  };

  const loadBackendState = async () => {
    const requestedBank = api.getBankScope();
    setLoading(true);
    setConnectionError('');
    try {
      // A bank saved in localStorage may no longer exist after its statement
      // was deleted. Reset the scope before requesting bank-scoped endpoints.
      const imported = await api.getStatements();
      if (requestedBank !== 'all' && requestedBank.split(',').some(bank => !imported.some(item => item.bank === bank))) {
        api.setBankScope('all');
        setBankFilter('all');
        return;
      }
      const [dataset, rulesResponse, categoryRulesResponse, categoriesResponse, reportsResponse, reviewResponse] = await Promise.all([
        api.getDataset(),
        api.getRules(),
        api.getCategoryRules(),
        api.getCategories(),
        api.getReports(),
        api.getReviewGroups(),
      ]);
      if (api.getBankScope() !== requestedBank) return;
      setTransactions(dataset.transactions || []);
      setAvailableBanks(dataset.availableBanks || []);
      setBanksLoaded(true);
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
      if (api.getBankScope() === requestedBank) setConnectionError(error.message || 'Could not connect to WalletWise.');
    } finally {
      if (api.getBankScope() === requestedBank) setLoading(false);
    }
  };

  useEffect(() => {
    if (banksLoaded && bankFilter !== 'all' && bankFilter.split(',').some(bank => !availableBanks.includes(bank))) {
      setBankFilter('all');
    }
  }, [availableBanks.join(','), banksLoaded]);

  useEffect(() => {
    api.setBankScope(bankFilter);
    window.localStorage.setItem('walletwise.bankScope', bankFilter);
    loadBackendState();
  }, [bankFilter]);

  useEffect(() => {
    if (page !== 'reports') return;
    // Navigating to Reports always checks the current database, not a stale
    // snapshot from when another screen was last opened.
    loadDerivedState().catch((error) => {
      console.error('Could not refresh WalletWise reports:', error);
    });
  }, [page, bankFilter]);

  const refreshTransactions = async () => {
    const requestedBank = api.getBankScope();
    const [current, currentStatements, currentCoverage] = await Promise.all([
      api.getTransactions(),
      api.getStatements(),
      api.getCoverage(),
    ]);
    if (api.getBankScope() !== requestedBank) return { transactions: [], reporting: emptyReporting };
    setTransactions(current || []);
    setStatements(currentStatements || []);
    setAvailableBanks([...new Set((currentStatements || []).map(item => item.bank).filter(Boolean))].sort());
    setBanksLoaded(true);
    setLastStatement((currentStatements || []).find((item) => requestedBank === 'all' || requestedBank.split(',').includes(item.bank)) || null);
    setCoverage(currentCoverage || { months: [], missingMonths: [] });
    const refreshedReport = await loadDerivedState();
    return { transactions: current || [], reporting: refreshedReport };
  };

  const refreshCategoryManagement = async () => {
    const [names, namingRules, categoryRulesResponse] = await Promise.all([
      api.getCategories(), api.getRules(), api.getCategoryRules(),
    ]);
    setCategories(names || []);
    setRules(namingRules || []);
    setCategoryRules(categoryRulesResponse || []);
  };

  const handleProcessed = async (result) => {
    // Show the imported bank immediately; changing the selector triggers a
    // bank-scoped reload. If already selected, refresh in place.
    if (bankFilter === result.bank) {
      await refreshTransactions();
    } else {
      api.setBankScope(result.bank);
      setBankFilter(result.bank);
    }
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
    transactions: <TransactionsPage key={`transactions-${bankFilter}`} transactions={transactions} refreshTransactions={refreshTransactions} categories={categories} />,
    review: <ReviewPage key={`review-${bankFilter}`} transactions={transactions} refreshTransactions={refreshTransactions} categories={categories} setCategories={setCategories} />,
    categories: <CategoriesPage key={`categories-${bankFilter}`} bankScope={bankFilter} refreshTransactions={refreshTransactions} refreshCategories={refreshCategoryManagement} />,
    rules: <RulesPage rules={rules} setRules={setRules} categoryRules={categoryRules} setCategoryRules={setCategoryRules} categories={categories} refreshTransactions={refreshTransactions} />,
    reports: <ReportsPage key={`reports-${bankFilter}`} transactions={transactions} reporting={reporting} refreshReports={loadDerivedState} refreshTransactions={refreshTransactions} setPage={setPage} />,
    budget: <BudgetPage key={`budget-${bankFilter}`} reporting={reporting} refreshReports={loadDerivedState} />,
    ask: <AskPage key={`ask-${bankFilter}`} bankScope={bankFilter} />,
  };

  return (
    <Layout page={page} setPage={setPage} reviewCount={reviewCount}
      selectedBank={bankFilter} onBankChange={setBankFilter} availableBanks={availableBanks}>
      {pages[page] || pages.dashboard}
    </Layout>
  );
}
