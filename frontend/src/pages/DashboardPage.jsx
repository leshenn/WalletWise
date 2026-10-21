import { ArrowDownRight, ArrowUpRight, CircleDollarSign, Eye, Repeat2, Upload } from 'lucide-react';
import StatCard from '../components/StatCard';
import ConfidenceBadge from '../components/ConfidenceBadge';
import EmptyState from '../components/EmptyState';
import { currency, shortDate } from '../utils/formatters';

export default function DashboardPage({ transactions, reporting, reviewCount, reviewReadyCount, setPage, lastStatement }) {
  const summary = reporting?.summary || {};
  const recent = [...transactions].sort((a, b) => (b.date || '').localeCompare(a.date || '')).slice(0, 6);

  return (
    <div className="page page-dashboard">
      <section className="hero-panel">
        <div>
          <span className="eyebrow">Money, made clearer.</span>
          <h1>{transactions.length ? 'See where your money is actually going.' : 'Turn a bank statement into useful financial data.'}</h1>
          <p>{transactions.length ? 'Understand your spending, track your savings, and build better money habits from the transactions you already make.' : 'Upload a bank statement and turn everyday transactions into a clearer picture of your money.'}</p>
          <div className="hero-actions">
            <button className="primary-button" onClick={() => setPage('upload')}><Upload size={17} /> {transactions.length ? 'Upload another' : 'Upload statement'}</button>
            {transactions.length > 0 && <button className="secondary-button" onClick={() => setPage('review')}><Eye size={17} /> Review {reviewCount} items</button>}
          </div>
        </div>
        <div className="hero-summary">
          <span>Your activity</span>
          <strong>{transactions.length}</strong>
          <small>transactions imported</small>
          <div className="hero-confidence"><i /> {reviewReadyCount} ready for reporting</div>
        </div>
      </section>

      {transactions.length > 0 ? (
        <>
          <section className="stat-grid">
            <StatCard label="Income" value={currency(summary.income || 0)} helper="Confirmed income" icon={ArrowUpRight} tone="positive" />
            <StatCard label="Spending" value={currency(summary.spending || 0)} helper="Confirmed expenses" icon={ArrowDownRight} tone="negative" />
            <StatCard label="Net cash flow" value={currency(summary.netCashFlow || 0)} helper="Income + refunds − expenses" icon={CircleDollarSign} tone={(summary.netCashFlow || 0) >= 0 ? 'positive' : 'negative'} />
            <StatCard label="Transfers moved" value={currency(summary.transfers || 0)} helper="Excluded from income/spending" icon={Repeat2} tone="warning" />
            <StatCard label="Needs review" value={reviewCount} helper="Transactions that need attention" icon={Eye} tone="warning" />
          </section>

          <section className="content-card">
            <div className="section-heading">
              <div><span className="eyebrow">Latest activity</span><h2>Recent transactions</h2></div>
              <button className="text-button" onClick={() => setPage('transactions')}>View all</button>
            </div>
            <div className="transaction-list compact-list">
              {recent.map((transaction) => (
                <div className="transaction-row" key={transaction.id}>
                  <div className="transaction-icon">{transaction.displayName?.[0] || '?'}</div>
                  <div className="transaction-main">
                    <div><strong>{transaction.displayName || 'Unnamed transaction'}</strong><ConfidenceBadge level={transaction.namingConfidence} compact /></div>
                    <span>{shortDate(transaction.date)} · {transaction.financialRole || 'unknown'} · {transaction.category}</span>
                  </div>
                  <strong className={transaction.amount >= 0 ? 'amount-positive' : 'amount-negative'}>{currency(transaction.amount)}</strong>
                </div>
              ))}
            </div>
          </section>
        </>
      ) : (
        <EmptyState icon={Upload} title="No statement processed yet" body="Upload a statement and WalletWise will organise your transactions into a clear financial picture." action={<button className="primary-button" onClick={() => setPage('upload')}>Upload statement</button>} />
      )}
    </div>
  );
}
