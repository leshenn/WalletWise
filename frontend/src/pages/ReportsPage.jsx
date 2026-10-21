import { CalendarRange, Maximize2, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import CategoryTrendChart from '../components/CategoryTrendChart';
import EmptyState from '../components/EmptyState';
import { api } from '../services/api';
import { currency, shortDate } from '../utils/formatters';

const PIE_COLOURS = [
  '#6D28D9', '#2563EB', '#059669', '#D97706', '#DC2626', '#8B5CF6',
  '#0891B2', '#DB2777', '#4F46E5', '#65A30D', '#9333EA', '#0F766E',
];


function percentage(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function signedCurrency(value) {
  const number = Number(value || 0);
  return `${number > 0 ? '+' : ''}${currency(number)}`;
}

function titleCase(value = '') {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : '';
}

function ChangePill({ value, goodWhenLower = false }) {
  if (value === null || value === undefined) return <span className="report-change neutral">No earlier period</span>;
  if (Number(value) === 0) return <span className="report-change neutral">No change</span>;
  const good = goodWhenLower ? Number(value) < 0 : Number(value) > 0;
  return (
    <span className={`report-change ${good ? 'good' : 'bad'}`}>
      {Number(value) > 0 ? '↑' : '↓'} {Math.abs(Number(value)).toFixed(1)}%
    </span>
  );
}

function monthBounds(monthKey) {
  if (!monthKey) return null;
  const [year, month] = monthKey.split('-').map(Number);
  if (!year || !month) return null;
  const start = `${year}-${String(month).padStart(2, '0')}-01`;
  const lastDay = new Date(year, month, 0).getDate();
  const end = `${year}-${String(month).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`;
  return { start, end };
}


export default function ReportsPage({ transactions, reporting, refreshReports, setPage }) {
  const months = reporting?.monthly || [];
  const availableMonths = reporting?.availableMonths || months.map((item) => ({ month: item.month, label: item.label }));
  const payCycles = reporting?.payCycles || [];
  const overallSavings = reporting?.savings || {};

  const [reportMode, setReportMode] = useState('calendar');
  const [selectedMonth, setSelectedMonth] = useState('latest');
  const [selectedCycle, setSelectedCycle] = useState('latest');
  const [includePartialMonths, setIncludePartialMonths] = useState(false);
  const [matrixExpanded, setMatrixExpanded] = useState(false);

  const [filterCategory, setFilterCategory] = useState('');
  const [filterMerchant, setFilterMerchant] = useState('');
  const [filterRole, setFilterRole] = useState('');
  const [explorerReport, setExplorerReport] = useState(null);
  const [explorerLoading, setExplorerLoading] = useState(false);
  const [explorerError, setExplorerError] = useState('');


  useEffect(() => {
    if (selectedMonth !== 'latest' && !months.some((item) => item.month === selectedMonth)) setSelectedMonth('latest');
  }, [months, selectedMonth]);

  useEffect(() => {
    if (selectedCycle !== 'latest' && !payCycles.some((item) => item.id === selectedCycle)) setSelectedCycle('latest');
  }, [payCycles, selectedCycle]);

  const activeMonth = useMemo(() => {
    if (!months.length) return null;
    if (selectedMonth === 'latest') return months[months.length - 1];
    return months.find((item) => item.month === selectedMonth) || months[months.length - 1];
  }, [months, selectedMonth]);

  const activeCycle = useMemo(() => {
    if (!payCycles.length) return null;
    if (selectedCycle === 'latest') return payCycles[payCycles.length - 1];
    return payCycles.find((item) => item.id === selectedCycle) || payCycles[payCycles.length - 1];
  }, [payCycles, selectedCycle]);

  const summary = reportMode === 'pay_cycle' ? activeCycle : activeMonth;
  const comparison = reportMode === 'pay_cycle' ? activeCycle?.changeFromPrevious : activeMonth?.changeFromPrevious;
  const activeLabel = reportMode === 'pay_cycle' ? activeCycle?.label : activeMonth?.label;
  const activeDateRange = reportMode === 'pay_cycle' ? activeCycle?.dateRangeLabel : null;

  const periodBounds = useMemo(() => {
    if (reportMode === 'calendar') return monthBounds(activeMonth?.month);
    if (reportMode === 'pay_cycle' && activeCycle) return { start: activeCycle.startDate, end: activeCycle.endDate };
    return null;
  }, [reportMode, activeMonth?.month, activeCycle]);

  const allMonthOptions = reporting?.allMonthsComparison || {};
  const allMonths = includePartialMonths ? allMonthOptions.includingPartial : allMonthOptions.completeOnly;

  // The selected reporting method controls the entire page. Calendar mode uses
  // calendar months; pay-cycle mode uses salary-to-salary periods everywhere,
  // including the trend chart and category comparison matrix.
  const periodComparison = useMemo(() => {
    if (reportMode === 'calendar') {
      return {
        periods: allMonths?.months || [],
        trend: allMonths?.categoryTrend || { categories: [], points: [] },
        hasIncompletePeriods: Boolean(allMonthOptions.hasPartialMonths),
      };
    }

    const cycles = includePartialMonths
      ? payCycles
      : payCycles.filter((cycle) => cycle.complete);
    const categories = [...new Set(
      cycles.flatMap((cycle) => (cycle.categoryBreakdown || []).map((item) => item.name).filter(Boolean)),
    )].sort((a, b) => a.localeCompare(b));
    const points = cycles.map((cycle) => {
      const values = new Map((cycle.categoryBreakdown || []).map((item) => [item.name, Number(item.amount || 0)]));
      return {
        month: cycle.id,
        label: cycle.label,
        dateRangeLabel: cycle.dateRangeLabel,
        values: Object.fromEntries(categories.map((category) => [category, Number(values.get(category) || 0)])),
      };
    });

    return {
      periods: cycles.map((cycle) => ({
        id: cycle.id,
        month: cycle.id,
        label: cycle.label,
        dateRangeLabel: cycle.dateRangeLabel,
        complete: cycle.complete,
      })),
      trend: { categories, points },
      hasIncompletePeriods: payCycles.some((cycle) => !cycle.complete),
    };
  }, [reportMode, allMonths, allMonthOptions.hasPartialMonths, payCycles, includePartialMonths]);

  const matrixPeriods = periodComparison.periods;
  const matrixTrend = periodComparison.trend;

  const periodTransactions = useMemo(() => {
    if (!periodBounds) return [];
    return (transactions || []).filter((item) => {
      const day = item.date;
      return day && day >= periodBounds.start && day <= periodBounds.end;
    });
  }, [transactions, periodBounds]);

  const categoryOptions = useMemo(() => (
    [...new Set(periodTransactions.map((item) => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b))
  ), [periodTransactions]);

  const merchantOptions = useMemo(() => (
    [...new Set(periodTransactions.map((item) => item.displayName).filter(Boolean))].sort((a, b) => a.localeCompare(b))
  ), [periodTransactions]);

  const roleOptions = useMemo(() => (
    [...new Set(periodTransactions.map((item) => item.financialRole).filter((value) => value && value !== 'balance'))].sort((a, b) => a.localeCompare(b))
  ), [periodTransactions]);

  const hasExplorerFilters = Boolean(filterCategory || filterMerchant || filterRole);
  const selectedReport = explorerReport || summary;
  const selectedTransactions = explorerReport?.transactions || periodTransactions;
  const selectedCategories = selectedReport?.categoryBreakdown || [];

  useEffect(() => {
    if (!periodBounds) {
      setExplorerReport(null);
      return undefined;
    }
    let cancelled = false;
    setExplorerLoading(true);
    setExplorerError('');
    setExplorerReport(null);
    api.exploreReport({
      startDate: periodBounds.start,
      endDate: periodBounds.end,
      category: filterCategory || null,
      merchant: filterMerchant || null,
      financialRole: filterRole || null,
    })
      .then((result) => {
        if (!cancelled) setExplorerReport(result);
      })
      .catch((error) => {
        if (!cancelled) setExplorerError(error.message || 'Could not update the report selection.');
      })
      .finally(() => {
        if (!cancelled) setExplorerLoading(false);
      });
    return () => { cancelled = true; };
  }, [periodBounds?.start, periodBounds?.end, filterCategory, filterMerchant, filterRole]);

  const comparisonHasPartial = reportMode === 'calendar'
    ? (activeMonth?.coverageStatus === 'partial'
      || (comparison?.previousId && months.find((item) => item.month === comparison.previousId)?.coverageStatus === 'partial'))
    : Boolean(activeCycle && !activeCycle.complete);

  const categoryChanges = (comparison?.categoryChanges || []).filter((item) => Number(item.changeAmount) !== 0).slice(0, 4);
  const currentSavings = Number(summary?.netSavingsMovement || 0);

  const clearExplorerFilters = () => {
    setFilterCategory('');
    setFilterMerchant('');
    setFilterRole('');
  };

  const selectMatrixCell = (periodId, category = '') => {
    if (reportMode === 'pay_cycle') setSelectedCycle(periodId);
    else setSelectedMonth(periodId);
    setFilterCategory(category);
    setFilterMerchant('');
    setFilterRole('');
    window.setTimeout(() => document.getElementById('report-transaction-explorer')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  };


  if (!reporting?.summary?.transactionCount) {
    return (
      <div className="page">
        <div className="desktop-page-title"><span className="eyebrow">Reporting</span><h1>Reports</h1></div>
        <EmptyState icon={CalendarRange} title="No reporting data yet" body="Import a statement and WalletWise will build reports from your transaction history." />
      </div>
    );
  }

  const pieTotal = selectedCategories.reduce((total, item) => total + Number(item.amount || 0), 0);
  let pieOffset = 0;
  const pieSegments = selectedCategories.map((item, index) => {
    const share = pieTotal > 0 ? (Number(item.amount || 0) / pieTotal) * 100 : 0;
    const start = pieOffset;
    pieOffset += share;
    return { ...item, share, start, end: pieOffset, colour: PIE_COLOURS[index % PIE_COLOURS.length] };
  });
  const pieBackground = pieSegments.length
    ? `conic-gradient(${pieSegments.map((item) => `${item.colour} ${item.start}% ${item.end}%`).join(', ')})`
    : '#eef1f4';

  const latestMonth = months[months.length - 1];
  const latestCycle = payCycles[payCycles.length - 1];
  const matrixColumnTotals = matrixTrend.points.map((point) => (
    matrixTrend.categories.reduce((total, category) => total + Number(point.values?.[category] || 0), 0)
  ));
  const matrixGrandTotal = matrixColumnTotals.reduce((total, amount) => total + amount, 0);

  return (
    <div className="page reports-explorer-page reports-reordered-page">
      <div className="desktop-page-title report-page-heading">
        <span className="eyebrow">Your money</span>
        <h1>Reports</h1>
        <p>Start with the period you care about, then follow the story from trends to savings, period KPIs and the transactions behind them.</p>
      </div>

      <nav className="report-period-nav" aria-label="Report navigation">
        <div className="report-section-nav" aria-label="Jump to report section">
          <button type="button" onClick={() => document.getElementById('report-trends')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Trends</button>
          <button type="button" onClick={() => document.getElementById('report-savings')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Savings</button>
          <button type="button" onClick={() => document.getElementById('report-kpis')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>KPIs</button>
          <button type="button" disabled={!summary} onClick={() => document.getElementById('report-transaction-explorer')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Transactions</button>
        </div>

        <div className="report-period-nav-controls">
          <label>
            <span>Report by</span>
            <select value={reportMode} onChange={(event) => {
              setReportMode(event.target.value);
              clearExplorerFilters();
            }}>
              <option value="calendar">Calendar month</option>
              <option value="pay_cycle">Pay cycle</option>
            </select>
          </label>

          <label>
            <span>Period</span>
            {reportMode === 'calendar' ? (
              <select value={selectedMonth} onChange={(event) => {
                setSelectedMonth(event.target.value);
                clearExplorerFilters();
              }}>
                <option value="latest">Latest · {latestMonth?.label || 'month'}</option>
                {[...availableMonths].reverse().filter((month) => month.month !== latestMonth?.month).map((month) => (
                  <option key={month.month} value={month.month}>{month.label}</option>
                ))}
              </select>
            ) : (
              <select value={selectedCycle} disabled={!payCycles.length} onChange={(event) => {
                setSelectedCycle(event.target.value);
                clearExplorerFilters();
              }}>
                {payCycles.length ? (
                  <>
                    <option value="latest">Latest · {latestCycle?.label || 'pay cycle'}</option>
                    {[...payCycles].reverse().filter((cycle) => cycle.id !== latestCycle?.id).map((cycle) => (
                      <option key={cycle.id} value={cycle.id}>{cycle.label}</option>
                    ))}
                  </>
                ) : <option>No pay cycles yet</option>}
              </select>
            )}
          </label>
        </div>
      </nav>

      {reportMode === 'pay_cycle' && !payCycles.length && (
        <section className="content-card report-empty-action">
          <h2>No pay cycles yet</h2>
          <p>Confirm your salary source first and WalletWise will build paycheck-to-paycheck periods automatically.</p>
          <button className="primary-button" onClick={() => setPage?.('transactions')}>Go to Income & Salary</button>
        </section>
      )}

      <section className="content-card category-trend-card report-line-trend-card report-trends-section" id="report-trends">
        <div className="report-section-heading-row">
          <div>
            <span className="eyebrow">Spending trends</span>
            <h2>{reportMode === 'pay_cycle' ? 'How your categories change from pay cycle to pay cycle' : 'How your categories change month to month'}</h2>
            <p>Select all categories or focus on only the lines you want to follow.</p>
          </div>
          <label className="partial-month-toggle">
            <input type="checkbox" checked={includePartialMonths} onChange={(event) => setIncludePartialMonths(event.target.checked)} />
            <span>Show incomplete periods</span>
          </label>
        </div>
        {matrixPeriods.length > 1 ? <CategoryTrendChart trend={matrixTrend} /> : <p className="muted-copy">{reportMode === 'pay_cycle' ? 'Add another completed pay cycle to build a useful trend.' : 'Add another month of statement history to build a useful trend.'}</p>}

        <div className="trend-matrix-divider" />
        <div className="report-section-heading-row matrix-heading-row">
          <div>
            <h3>{reportMode === 'pay_cycle' ? 'Pay-cycle spending by category' : 'Monthly spending by category'}</h3>
            <p>Red means spending increased from the previous {reportMode === 'pay_cycle' ? 'pay cycle' : 'month'}. Green means it decreased. Click a value to open its transactions.</p>
          </div>
          <button className="secondary-button compact-button matrix-expand-button" type="button" onClick={() => setMatrixExpanded(true)}>
            <Maximize2 size={16} /> Expand table
          </button>
        </div>
        <p className="matrix-mobile-hint">Swipe sideways to compare periods, or expand the table for a wider view.</p>
        {matrixPeriods.length ? (
          <div className={`monthly-matrix-wrap ${matrixExpanded ? 'matrix-expanded' : ''}`}>
            {matrixExpanded && (
              <div className="matrix-expanded-toolbar">
                <div>
                  <strong>{reportMode === 'pay_cycle' ? 'Pay-cycle spending' : 'Monthly spending'}</strong>
                  <span>Rotate your phone for the widest view, or swipe horizontally.</span>
                </div>
                <button type="button" onClick={() => setMatrixExpanded(false)} aria-label="Close expanded table"><X size={19} /></button>
              </div>
            )}
            <table className="monthly-matrix-table">
              <thead>
                <tr>
                  <th>Category</th>
                  {matrixPeriods.map((period) => <th key={period.id || period.month} onClick={() => selectMatrixCell(period.id || period.month)} title={period.dateRangeLabel || undefined}>{reportMode === 'pay_cycle' ? period.label.replace(' period', '') : period.label.replace(' 20', ' ’')}</th>)}
                  <th>Total</th>
                </tr>
              </thead>
              <tbody>
                {matrixTrend.categories.map((category) => {
                  const rowTotal = matrixTrend.points.reduce((total, point) => total + Number(point.values?.[category] || 0), 0);
                  return (
                    <tr key={category}>
                      <td><button type="button" className="matrix-category-button" onClick={() => setFilterCategory(category)}>{category}</button></td>
                      {matrixTrend.points.map((point, index) => {
                        const amount = Number(point.values?.[category] || 0);
                        const previous = index > 0 ? Number(matrixTrend.points[index - 1].values?.[category] || 0) : null;
                        const change = previous && previous > 0 ? ((amount - previous) / previous) * 100 : null;
                        const changeClass = change === null || change === 0 ? 'neutral' : change < 0 ? 'good' : 'bad';
                        return (
                          <td key={`${category}-${point.month}`} className={((reportMode === 'pay_cycle' ? activeCycle?.id : activeMonth?.month) === point.month && filterCategory === category) ? 'selected' : ''} onClick={() => selectMatrixCell(point.month, category)}>
                            <strong>{currency(amount)}</strong>
                            {change !== null && <small className={`matrix-change ${changeClass}`}>{change > 0 ? '↑' : change < 0 ? '↓' : '→'} {Math.abs(change).toFixed(1)}%</small>}
                          </td>
                        );
                      })}
                      <td><strong>{currency(rowTotal)}</strong></td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <th>Total spend</th>
                  {matrixColumnTotals.map((total, index) => (
                    <th key={`column-total-${matrixTrend.points[index]?.month || index}`}>{currency(total)}</th>
                  ))}
                  <th>{currency(matrixGrandTotal)}</th>
                </tr>
              </tfoot>
            </table>
          </div>
        ) : (
          <div className="report-no-complete-months">
            <p>{reportMode === 'pay_cycle' ? 'There are no completed pay cycles to compare yet.' : 'There are no complete months to compare yet.'}</p>
            {!includePartialMonths && periodComparison.hasIncompletePeriods && <button className="secondary-button" onClick={() => setIncludePartialMonths(true)}>Show incomplete periods</button>}
          </div>
        )}
      </section>

      <section className="content-card report-savings-card" id="report-savings">
        <div className="report-section-heading-row">
          <div>
            <span className="eyebrow">Savings</span>
            <h2>What are you actually putting aside?</h2>
            <p>Overall savings uses your full imported history. The selected-period figures follow the month or pay cycle chosen above.</p>
          </div>
          <button className="secondary-button compact-button" type="button" onClick={() => setPage?.('budget')}>Open Budget</button>
        </div>

        <div className="savings-metric-grid savings-metric-grid-large">
          <div>
            <span>Overall net savings</span>
            <strong className={Number(overallSavings.netSavingsMovement || 0) >= 0 ? 'amount-positive' : 'amount-negative'}>{currency(overallSavings.netSavingsMovement || 0)}</strong>
            <small>All recognised savings transfers across your imported history</small>
          </div>
          <div>
            <span>{activeLabel || 'Selected period'} savings</span>
            <strong className={currentSavings >= 0 ? 'amount-positive' : 'amount-negative'}>{currency(currentSavings)}</strong>
            <small>Money moved into savings minus money moved back</small>
          </div>
          <div>
            <span>Overall savings rate</span>
            <strong>{overallSavings.savingsTransferRatePct === null || overallSavings.savingsTransferRatePct === undefined ? '—' : percentage(overallSavings.savingsTransferRatePct)}</strong>
            <small>Share of recognised income moved into savings overall</small>
          </div>
          <div>
            <span>Selected-period savings rate</span>
            <strong>{summary?.savingsTransferRatePct === null || summary?.savingsTransferRatePct === undefined ? '—' : percentage(summary.savingsTransferRatePct)}</strong>
            <small>{summary?.income ? "Share of this period's income moved into savings" : 'No recognised income in this period'}</small>
          </div>
        </div>
      </section>

      {summary && (
        <section className="report-period-kpis-section" id="report-kpis">
          <div className="report-section-heading-row">
            <div>
              <span className="eyebrow">Selected period</span>
              <h2>{activeLabel} KPIs</h2>
              <p>{activeDateRange ? activeDateRange : 'The key numbers for this reporting period, compared with the period before it.'}</p>
            </div>
            {comparison?.previousLabel && <span className="compare-label">Compared with {comparison.previousLabel}</span>}
          </div>

          {comparisonHasPartial && <div className="report-data-warning">{reportMode === 'pay_cycle' ? 'The selected pay cycle is still in progress, so its comparison may change before the next paycheck.' : 'One of these months has partial statement coverage, so the comparison may not represent a full month.'}</div>}

          <div className="report-key-grid report-kpi-grid-large">
            <article className="report-key-metric">
              <span>Spent</span>
              <strong>{currency(summary.spending || 0)}</strong>
              {comparison ? <div className="report-key-compare"><ChangePill value={comparison.spendingPct} goodWhenLower /><small>{currency(comparison.previous?.spending || 0)} before</small></div> : <small>Confirmed expenses</small>}
            </article>
            <article className="report-key-metric">
              <span>Income</span>
              <strong>{currency(summary.income || 0)}</strong>
              {comparison ? <div className="report-key-compare"><ChangePill value={comparison.incomePct} /><small>{currency(comparison.previous?.income || 0)} before</small></div> : <small>Money classified as income</small>}
            </article>
            <article className="report-key-metric">
              <span>After expenses</span>
              <strong className={Number(summary.netCashFlow || 0) >= 0 ? 'amount-positive' : 'amount-negative'}>{currency(summary.netCashFlow || 0)}</strong>
              <small>Income + refunds − spending</small>
            </article>
            <article className="report-key-metric">
              <span>Net saved</span>
              <strong className={currentSavings >= 0 ? 'amount-positive' : 'amount-negative'}>{currency(currentSavings)}</strong>
              <small>Transfers into savings minus money moved back</small>
            </article>
            <article className="report-key-metric">
              <span>Spent vs income</span>
              <strong>{percentage(summary.spendingToIncomePct)}</strong>
              <small>How much of this period's income went to expenses</small>
            </article>
            <article className="report-key-metric">
              <span>Flexible spending</span>
              <strong>{percentage(summary.flexibleSpendingPct)}</strong>
              <small>{currency(summary.flexibleSpending || 0)} on flexible categories</small>
            </article>
            <article className="report-key-metric">
              <span>Top category</span>
              <strong>{summary.topCategory?.name || '—'}</strong>
              <small>{summary.topCategory ? currency(summary.topCategory.amount || 0) : 'No categorised expenses'}</small>
            </article>
            <article className="report-key-metric">
              <span>Largest expense</span>
              <strong>{summary.largestExpense ? currency(Math.abs(Number(summary.largestExpense.amount || 0))) : '—'}</strong>
              <small>{summary.largestExpense?.displayName || summary.largestExpense?.rawDescription || 'No expense yet'}</small>
            </article>
          </div>

          <div className="report-kpi-detail-grid">
            <section className="content-card expense-pie-card">
              <div>
                <span className="eyebrow">Where it went</span>
                <h2>Expense breakdown</h2>
                <p>{hasExplorerFilters ? 'Showing your current transaction selection.' : 'Select a category to focus the transaction list.'}</p>
              </div>
              <div className="expense-pie-layout">
                <div className="expense-pie" style={{ background: pieBackground }}>
                  <div><strong>{currency(pieTotal)}</strong><span>expenses</span></div>
                </div>
                <div className="expense-pie-legend">
                  {pieSegments.length ? pieSegments.map((item) => (
                    <button type="button" key={item.name} className={filterCategory === item.name ? 'active' : ''} onClick={() => setFilterCategory(filterCategory === item.name ? '' : item.name)}>
                      <span><i style={{ background: item.colour }} />{item.name}</span>
                      <strong>{item.share.toFixed(1)}%</strong>
                    </button>
                  )) : <p className="muted-copy">No expenses match this selection.</p>}
                </div>
              </div>
            </section>

            <section className="content-card report-what-changed">
              <span className="eyebrow">What changed?</span>
              <h2>{comparison?.previousLabel ? `${activeLabel} vs ${comparison.previousLabel}` : 'Period comparison'}</h2>
              {comparison ? (
                <div className="what-changed-list">
                  <div><span>Overall spending</span><strong className={Number(comparison.spendingAmount) > 0 ? 'amount-negative' : 'amount-positive'}>{signedCurrency(comparison.spendingAmount)}</strong><ChangePill value={comparison.spendingPct} goodWhenLower /></div>
                  <div><span>Income</span><strong className={Number(comparison.incomeAmount) >= 0 ? 'amount-positive' : 'amount-negative'}>{signedCurrency(comparison.incomeAmount)}</strong><ChangePill value={comparison.incomePct} /></div>
                  {categoryChanges.map((item) => (
                    <div key={item.name}><span>{item.name}</span><strong className={Number(item.changeAmount) > 0 ? 'amount-negative' : 'amount-positive'}>{signedCurrency(item.changeAmount)}</strong><ChangePill value={item.changePct} goodWhenLower /></div>
                  ))}
                </div>
              ) : <p className="muted-copy">There is no earlier period to compare with yet.</p>}
            </section>
          </div>
        </section>
      )}

      {summary && (
        <section className="content-card report-transaction-explorer" id="report-transaction-explorer">
          <div className="report-section-heading-row">
            <div>
              <span className="eyebrow">Behind the numbers</span>
              <h2>Transactions</h2>
              <p>Filter the selected period, then inspect the exact transactions behind the report.</p>
            </div>
            <strong className="transaction-result-count">{selectedTransactions.length} transaction{selectedTransactions.length === 1 ? '' : 's'}</strong>
          </div>

          <div className="report-explorer-filters report-explorer-filters-inline">
            <div className="qlik-filter-grid">
              <label><span>Category</span><select value={filterCategory} onChange={(event) => setFilterCategory(event.target.value)}><option value="">All categories</option>{categoryOptions.map((value) => <option key={value}>{value}</option>)}</select></label>
              <label><span>Merchant / name</span><select value={filterMerchant} onChange={(event) => setFilterMerchant(event.target.value)}><option value="">All names</option>{merchantOptions.map((value) => <option key={value}>{value}</option>)}</select></label>
              <label><span>Money movement</span><select value={filterRole} onChange={(event) => setFilterRole(event.target.value)}><option value="">All movements</option>{roleOptions.map((value) => <option value={value} key={value}>{titleCase(value)}</option>)}</select></label>
            </div>
            <div className="transaction-filter-footer">
              <div className="active-report-selections">
                <span>{activeLabel}</span>
                {filterCategory && <span>Category: {filterCategory}</span>}
                {filterMerchant && <span>Name: {filterMerchant}</span>}
                {filterRole && <span>Movement: {titleCase(filterRole)}</span>}
                {explorerLoading && <span>Updating…</span>}
              </div>
              <button className="secondary-button compact-button" onClick={clearExplorerFilters} disabled={!hasExplorerFilters}>Clear selections</button>
            </div>
            {explorerError && <div className="inline-alert error">{explorerError}</div>}
          </div>

          <div className="explorer-key-grid explorer-key-grid-roomy">
            <div><span>Selected spending</span><strong>{currency(selectedReport?.spending || 0)}</strong></div>
            <div><span>Selected income</span><strong>{currency(selectedReport?.income || 0)}</strong></div>
            <div><span>After expenses</span><strong className={Number(selectedReport?.netCashFlow || 0) >= 0 ? 'amount-positive' : 'amount-negative'}>{currency(selectedReport?.netCashFlow || 0)}</strong></div>
            <div><span>Transactions</span><strong>{selectedTransactions.length}</strong></div>
          </div>

          <div className="report-transaction-table-wrap">
            <table className="report-transaction-table">
              <thead><tr><th>Date</th><th>Name</th><th>Raw description</th><th>Category</th><th>Movement</th><th>Amount</th></tr></thead>
              <tbody>
                {selectedTransactions.length ? selectedTransactions.map((item) => (
                  <tr key={item.id}>
                    <td>{shortDate(item.date)}</td>
                    <td><strong>{item.displayName || item.rawDescription || 'Transaction'}</strong></td>
                    <td className="raw-description-cell">{item.rawDescription || '—'}</td>
                    <td>{item.category || 'Uncategorised'}</td>
                    <td>{titleCase(item.financialRole || 'Unknown')}</td>
                    <td className={Number(item.amount || 0) >= 0 ? 'amount-positive' : ''}>{currency(item.amount || 0)}</td>
                  </tr>
                )) : <tr><td colSpan="6"><p className="muted-copy">No transactions match this selection.</p></td></tr>}
              </tbody>
            </table>
          </div>

          <div className="report-mobile-transactions">
            {selectedTransactions.length ? selectedTransactions.map((item) => (
              <article className="report-mobile-transaction" key={`mobile-${item.id}`}>
                <div className="report-mobile-transaction-top">
                  <div>
                    <span>{shortDate(item.date)}</span>
                    <strong>{item.displayName || item.rawDescription || 'Transaction'}</strong>
                  </div>
                  <strong className={Number(item.amount || 0) >= 0 ? 'amount-positive' : ''}>{currency(item.amount || 0)}</strong>
                </div>
                <p>{item.rawDescription || 'No raw description'}</p>
                <div className="report-mobile-transaction-meta">
                  <span>{item.category || 'Uncategorised'}</span>
                  <span>{titleCase(item.financialRole || 'Unknown')}</span>
                </div>
              </article>
            )) : <p className="muted-copy">No transactions match this selection.</p>}
          </div>
        </section>
      )}

    </div>
  );
}
