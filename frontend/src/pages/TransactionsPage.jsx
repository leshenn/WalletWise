import {
  Ban,
  CheckCircle2,
  Pencil,
  RotateCcw,
  Search,
  SlidersHorizontal,
  WalletCards,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import ConfidenceBadge from '../components/ConfidenceBadge';
import { api } from '../services/api';
import { currency, shortDate } from '../utils/formatters';

function roleLabel(value) {
  return ({
    expense: 'Expense',
    income: 'Income',
    transfer: 'Transfer',
    refund: 'Refund',
    reimbursement: 'Reimbursement',
  })[value] || 'Unknown';
}

function TransactionEditModal({ transaction, categories = [], saving, onClose, onSave }) {
  const [form, setForm] = useState({ displayName: '', category: 'Uncategorised', financialRole: 'unknown' });

  useEffect(() => {
    if (!transaction) return;
    setForm({
      displayName: transaction.displayName || '',
      category: transaction.category || 'Uncategorised',
      financialRole: transaction.financialRole || 'unknown',
    });
  }, [transaction]);

  useEffect(() => {
    if (!transaction) return undefined;
    const onKey = (event) => {
      if (event.key === 'Escape' && !saving) onClose?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [transaction, saving, onClose]);

  if (!transaction) return null;

  const categoryOptions = [...new Set([
    ...categories,
    transaction.category,
    'Uncategorised',
  ].filter(Boolean))].sort((a, b) => a.localeCompare(b));

  const submit = (event) => {
    event.preventDefault();
    onSave?.(transaction, {
      displayName: form.displayName.trim() || transaction.displayName || transaction.rawDescription || 'Transaction',
      category: form.category,
      financialRole: form.financialRole,
      namingConfidence: 'high',
      nameReason: 'Edited by user',
      categoryConfidence: 'high',
      categoryReason: 'Edited by user',
      roleConfidence: 'high',
      roleReason: 'Edited by user',
      reviewReasons: [],
      humanVerified: true,
    });
  };

  return (
    <div
      className="transaction-edit-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !saving) onClose?.();
      }}
    >
      <form className="transaction-edit-modal" onSubmit={submit}>
        <div className="transaction-edit-heading">
          <div>
            <span className="eyebrow">Correct a transaction</span>
            <h2>Edit transaction</h2>
            <p>Your original bank description and extracted amount stay unchanged.</p>
          </div>
          <button type="button" className="icon-button transaction-edit-close" onClick={onClose} disabled={saving} aria-label="Close transaction editor">
            <X size={18} />
          </button>
        </div>

        <div className="transaction-edit-original">
          <div>
            <span>Date</span>
            <strong>{transaction.date ? shortDate(transaction.date) : '—'}</strong>
          </div>
          <div>
            <span>Amount</span>
            <strong className={transaction.amount >= 0 ? 'amount-positive' : 'amount-negative'}>{currency(transaction.amount)}</strong>
          </div>
          <div className="transaction-edit-raw">
            <span>Original bank description</span>
            <strong>{transaction.rawDescription || 'No raw description available'}</strong>
          </div>
        </div>

        <div className="transaction-edit-fields">
          <label>
            <span>Friendly name</span>
            <input
              value={form.displayName}
              onChange={(event) => setForm((current) => ({ ...current, displayName: event.target.value }))}
              placeholder="Transaction name"
              maxLength={120}
            />
          </label>

          <label>
            <span>Category</span>
            <select value={form.category} onChange={(event) => setForm((current) => ({ ...current, category: event.target.value }))}>
              {categoryOptions.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>

          <label>
            <span>Money movement</span>
            <select value={form.financialRole} onChange={(event) => setForm((current) => ({ ...current, financialRole: event.target.value }))}>
              <option value="expense">Expense</option>
              <option value="income">Income</option>
              <option value="transfer">Transfer</option>
              <option value="refund">Refund</option>
              <option value="reimbursement">Reimbursement</option>
              <option value="unknown">Unknown</option>
            </select>
          </label>
        </div>

        {transaction.isSalary && form.financialRole !== 'income' && (
          <div className="transaction-edit-note">
            Changing this away from Income will also remove it as a salary/pay-cycle anchor.
          </div>
        )}

        <div className="transaction-edit-actions">
          <button type="button" className="secondary-button" onClick={onClose} disabled={saving}>Cancel</button>
          <button type="submit" className="primary-button" disabled={saving}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </form>
    </div>
  );
}

function SalaryTransactionList({
  candidate,
  selectable = false,
  selectedIds = [],
  onToggle,
  onSetSalary,
  salarySavingId,
}) {
  const rows = candidate.transactions || [];
  if (!rows.length) return null;

  const actionLabel = (transaction) => {
    if (transaction.isSalary) return 'Not salary';
    if (transaction.salaryExcluded) return 'Mark salary';
    return 'Not salary';
  };

  const actionValue = (transaction) => !transaction.isSalary && transaction.salaryExcluded;

  return (
    <>
      <div className="salary-occurrence-table desktop-salary-table">
        <div className={`salary-occurrence-head ${selectable ? 'selectable' : ''} with-actions`}>
          {selectable && <span>Select</span>}
          <span>Date</span><span>Transaction</span><span>Amount</span><span>Status</span><span>Action</span>
        </div>
        {rows.map((transaction) => {
          const selected = selectedIds.includes(transaction.id);
          return (
            <div className={`salary-occurrence-row ${selectable ? 'selectable' : ''} with-actions ${selected ? '' : 'row-deselected'}`} key={transaction.id}>
              {selectable && (
                <label className="salary-row-check" aria-label={`Use ${transaction.displayName || 'payment'} on ${transaction.date || ''} as salary`}>
                  <input type="checkbox" checked={selected} onChange={() => onToggle?.(transaction.id)} />
                </label>
              )}
              <span>{transaction.date ? shortDate(transaction.date) : '—'}</span>
              <span className="salary-transaction-name">
                <strong>{transaction.displayName || 'Incoming payment'}</strong>
                <small>{transaction.rawDescription || 'No raw description available'}</small>
              </span>
              <strong className="amount-positive">{currency(transaction.amount)}</strong>
              <span className={transaction.isSalary ? 'salary-row-status confirmed' : transaction.salaryExcluded ? 'salary-row-status excluded' : 'salary-row-status'}>
                {transaction.isSalary ? 'Salary' : transaction.salaryExcluded ? 'Not salary' : 'Income'}
              </span>
              <button
                type="button"
                className={`salary-row-action ${transaction.isSalary ? 'remove' : transaction.salaryExcluded ? 'restore' : 'dismiss'}`}
                disabled={salarySavingId === transaction.id}
                onClick={() => onSetSalary?.(transaction, actionValue(transaction))}
              >
                {transaction.salaryExcluded ? <RotateCcw size={13} /> : <Ban size={13} />}
                {salarySavingId === transaction.id ? 'Saving…' : actionLabel(transaction)}
              </button>
            </div>
          );
        })}
      </div>

      <div className="salary-occurrence-mobile">
        {rows.map((transaction) => {
          const selected = selectedIds.includes(transaction.id);
          return (
            <div className={`salary-income-row ${selected ? '' : 'row-deselected'}`} key={transaction.id}>
              {selectable && (
                <label className="salary-row-check mobile" aria-label={`Use ${transaction.displayName || 'payment'} on ${transaction.date || ''} as salary`}>
                  <input type="checkbox" checked={selected} onChange={() => onToggle?.(transaction.id)} />
                </label>
              )}
              <div className="salary-income-main">
                <strong>{transaction.displayName || 'Incoming payment'}</strong>
                <span>{transaction.date ? shortDate(transaction.date) : '—'}</span>
                <small>{transaction.rawDescription || 'No raw description available'}</small>
              </div>
              <div className="salary-mobile-status-actions">
                <strong className="amount-positive">{currency(transaction.amount)}</strong>
                <span className={transaction.isSalary ? 'salary-row-status confirmed' : transaction.salaryExcluded ? 'salary-row-status excluded' : 'salary-row-status'}>
                  {transaction.isSalary ? 'Salary' : transaction.salaryExcluded ? 'Not salary' : 'Income'}
                </span>
                <button
                  type="button"
                  className={`salary-row-action ${transaction.isSalary ? 'remove' : transaction.salaryExcluded ? 'restore' : 'dismiss'}`}
                  disabled={salarySavingId === transaction.id}
                  onClick={() => onSetSalary?.(transaction, actionValue(transaction))}
                >
                  {transaction.salaryExcluded ? <RotateCcw size={13} /> : <Ban size={13} />}
                  {salarySavingId === transaction.id ? 'Saving…' : actionLabel(transaction)}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

function SalarySourceCard({
  candidate,
  confirmed = false,
  suggested = false,
  saving,
  removing,
  salarySavingId,
  onConfirm,
  onRemove,
  onSetSalary,
}) {
  const rows = candidate.transactions || [];
  const defaultSelected = candidate.dismissedAsSalary
    ? rows.map((transaction) => transaction.id)
    : rows.filter((transaction) => !transaction.salaryExcluded).map((transaction) => transaction.id);
  const transactionKey = rows.map((transaction) => `${transaction.id}:${transaction.salaryExcluded ? 1 : 0}:${transaction.isSalary ? 1 : 0}`).join('|');
  const [selectedIds, setSelectedIds] = useState(defaultSelected);

  useEffect(() => {
    setSelectedIds(defaultSelected);
  }, [candidate.sourceKey, transactionKey, candidate.dismissedAsSalary]);

  const selectable = !confirmed;
  const toggleRow = (transactionId) => {
    setSelectedIds((current) => current.includes(transactionId)
      ? current.filter((id) => id !== transactionId)
      : [...current, transactionId]);
  };

  const eyebrow = confirmed
    ? 'Confirmed salary'
    : candidate.dismissedAsSalary
      ? 'Marked as not salary'
      : suggested
        ? 'Suggested salary'
        : 'Other income';

  return (
    <article className={`salary-source-card ${confirmed ? 'confirmed' : ''} ${candidate.dismissedAsSalary ? 'dismissed' : ''}`}>
      <div className="salary-source-header">
        <div>
          <span className="eyebrow">{eyebrow}</span>
          <h3>{candidate.label}</h3>
          <p>
            {candidate.occurrences} payment{candidate.occurrences === 1 ? '' : 's'} across {candidate.months} month{candidate.months === 1 ? '' : 's'}
            {' · '}average {currency(candidate.averageAmount)}
          </p>
          {selectable && !candidate.dismissedAsSalary && <p className="salary-selection-help">Deselect any payment that should not be used as a salary/pay-cycle date.</p>}
          {candidate.dismissedAsSalary && <p className="salary-selection-help">You previously marked this source as not salary. You can add it back whenever you want.</p>}
        </div>

        {confirmed ? (
          <div className="salary-source-header-actions">
            <span className="salary-source-confirmed"><CheckCircle2 size={16} /> Confirmed</span>
            <button type="button" className="salary-source-remove" disabled={removing} onClick={() => onRemove?.(candidate)}>
              <Ban size={14} /> {removing ? 'Removing…' : 'Remove salary source'}
            </button>
          </div>
        ) : (
          <button
            className={suggested ? 'primary-button salary-source-button' : 'secondary-button salary-source-button'}
            disabled={saving || selectedIds.length === 0}
            onClick={() => onConfirm(candidate, selectedIds)}
          >
            {saving
              ? 'Saving…'
              : candidate.dismissedAsSalary
                ? 'Re-add as salary'
                : suggested
                  ? `Confirm ${selectedIds.length} selected`
                  : `Use ${selectedIds.length} as salary`}
          </button>
        )}
      </div>

      <SalaryTransactionList
        candidate={candidate}
        selectable={selectable && !candidate.dismissedAsSalary}
        selectedIds={selectedIds}
        onToggle={toggleRow}
        onSetSalary={onSetSalary}
        salarySavingId={salarySavingId}
      />

      {confirmed && (
        <div className="salary-learning-note">
          Future matching income will be marked as a salary/pay-cycle anchor automatically. Individual payments you mark as “Not salary” stay excluded.
        </div>
      )}
    </article>
  );
}

export default function TransactionsPage({ transactions, refreshTransactions, categories = [] }) {
  const [activeTab, setActiveTab] = useState('transactions');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('All');
  const [role, setRole] = useState('All');
  const [confidence, setConfidence] = useState('All');
  const [salarySavingId, setSalarySavingId] = useState('');
  const [salarySourceSavingId, setSalarySourceSavingId] = useState('');
  const [salarySourceRemovingId, setSalarySourceRemovingId] = useState('');
  const [incomeOverview, setIncomeOverview] = useState(null);
  const [incomeLoading, setIncomeLoading] = useState(true);
  const [editingTransaction, setEditingTransaction] = useState(null);
  const [transactionSaving, setTransactionSaving] = useState(false);
  const [error, setError] = useState('');

  const categoryOptions = ['All', ...new Set(transactions.map((t) => t.category).filter(Boolean))];
  const filtered = useMemo(() => transactions.filter((transaction) => {
    const haystack = `${transaction.displayName} ${transaction.rawDescription} ${transaction.category} ${transaction.financialRole}`.toLowerCase();
    return (!search || haystack.includes(search.toLowerCase()))
      && (category === 'All' || transaction.category === category)
      && (role === 'All' || transaction.financialRole === role)
      && (confidence === 'All' || transaction.namingConfidence === confidence);
  }), [transactions, search, category, role, confidence]);

  const loadIncomeOverview = async () => {
    setIncomeLoading(true);
    try {
      setIncomeOverview(await api.getIncomeOverview());
    } catch (err) {
      setError(err.message || 'Could not load income suggestions.');
    } finally {
      setIncomeLoading(false);
    }
  };

  useEffect(() => {
    loadIncomeOverview();
  }, [transactions]);

  const confirmSalarySource = async (candidate, selectedTransactionIds) => {
    setSalarySourceSavingId(candidate.sourceKey);
    setError('');
    try {
      const response = await api.confirmSalarySource(candidate.latestTransactionId, selectedTransactionIds);
      setIncomeOverview(response.overview);
      await refreshTransactions?.();
    } catch (err) {
      setError(err.message || 'Could not confirm this salary source.');
    } finally {
      setSalarySourceSavingId('');
    }
  };

  const setSalaryStatus = async (transaction, isSalary) => {
    setSalarySavingId(transaction.id);
    setError('');
    try {
      await api.setSalaryMarker(transaction.id, isSalary);
      await refreshTransactions?.();
    } catch (err) {
      setError(err.message || 'Could not update the salary marker.');
    } finally {
      setSalarySavingId('');
    }
  };

  const toggleSalary = async (transaction) => {
    await setSalaryStatus(transaction, !transaction.isSalary);
  };

  const removeSalarySource = async (candidate) => {
    if (!candidate.sourceId) return;
    const confirmed = window.confirm(`Remove ${candidate.label} as a learned salary source? The income will stay in WalletWise and you can add it back later.`);
    if (!confirmed) return;
    setSalarySourceRemovingId(candidate.sourceKey);
    setError('');
    try {
      const response = await api.removeSalarySource(candidate.sourceId);
      setIncomeOverview(response.overview);
      await refreshTransactions?.();
    } catch (err) {
      setError(err.message || 'Could not remove this salary source.');
    } finally {
      setSalarySourceRemovingId('');
    }
  };

  const saveTransaction = async (transaction, payload) => {
    setTransactionSaving(true);
    setError('');
    try {
      await api.updateTransaction(transaction.id, payload);
      await refreshTransactions?.();
      setEditingTransaction(null);
    } catch (err) {
      setError(err.message || 'Could not save the transaction changes.');
    } finally {
      setTransactionSaving(false);
    }
  };

  const suggestions = incomeOverview?.salarySuggestions || [];
  const confirmedSources = incomeOverview?.confirmedSalarySources || [];
  const otherIncome = incomeOverview?.otherIncome || [];
  const incomeCount = incomeOverview?.incomeTransactionCount || 0;

  return (
    <div className="page">
      <div className="desktop-page-title">
        <span className="eyebrow">Standardised dataset</span>
        <h1>Transactions</h1>
        <p>Browse your history, correct anything you changed by mistake, or switch to Income & Salary to manage pay-cycle anchors.</p>
      </div>

      <div className="transaction-tabs" role="tablist" aria-label="Transaction views">
        <button type="button" role="tab" aria-selected={activeTab === 'transactions'} className={activeTab === 'transactions' ? 'active' : ''} onClick={() => setActiveTab('transactions')}>
          Transactions <span>{transactions.length}</span>
        </button>
        <button type="button" role="tab" aria-selected={activeTab === 'salary'} className={activeTab === 'salary' ? 'active' : ''} onClick={() => setActiveTab('salary')}>
          Income & Salary <span>{incomeCount}</span>
        </button>
      </div>

      {error && <div className="inline-alert error">{error}</div>}

      {activeTab === 'salary' ? (
        <section className="content-card income-salary-section">
          <div className="section-heading">
            <div>
              <span className="eyebrow">Income & salary</span>
              <h2>Income history</h2>
              <p>Confirm real salary sources, exclude income that is not salary, and restore anything later if you change your mind.</p>
            </div>
          </div>

          {incomeLoading ? (
            <p className="muted-copy">Checking incoming payments…</p>
          ) : (
            <div className="salary-source-sections">
              {confirmedSources.length > 0 && (
                <div className="salary-section-block">
                  <div className="salary-section-label">
                    <strong>Confirmed salary</strong>
                    <span>{confirmedSources.length} learned source{confirmedSources.length === 1 ? '' : 's'}</span>
                  </div>
                  <div className="salary-source-list">
                    {confirmedSources.map((candidate) => (
                      <SalarySourceCard
                        key={candidate.sourceKey}
                        candidate={candidate}
                        confirmed
                        removing={salarySourceRemovingId === candidate.sourceKey}
                        salarySavingId={salarySavingId}
                        onRemove={removeSalarySource}
                        onSetSalary={setSalaryStatus}
                      />
                    ))}
                  </div>
                </div>
              )}

              {suggestions.length > 0 && (
                <div className="salary-section-block">
                  <div className="salary-section-label">
                    <strong>WalletWise thinks these may be salary</strong>
                    <span>Review the dates, names and amounts. You can also mark individual payments as not salary.</span>
                  </div>
                  <div className="salary-source-list">
                    {suggestions.map((candidate) => (
                      <SalarySourceCard
                        key={candidate.sourceKey}
                        candidate={candidate}
                        suggested
                        saving={salarySourceSavingId === candidate.sourceKey}
                        salarySavingId={salarySavingId}
                        onConfirm={confirmSalarySource}
                        onSetSalary={setSalaryStatus}
                      />
                    ))}
                  </div>
                </div>
              )}

              {otherIncome.length > 0 && (
                <div className="salary-section-block">
                  <div className="salary-section-label">
                    <strong>Other income</strong>
                    <span>Income that is not currently used as salary. You can add any source manually.</span>
                  </div>
                  <div className="salary-source-list">
                    {otherIncome.map((candidate) => (
                      <SalarySourceCard
                        key={candidate.sourceKey}
                        candidate={candidate}
                        saving={salarySourceSavingId === candidate.sourceKey}
                        salarySavingId={salarySavingId}
                        onConfirm={confirmSalarySource}
                        onSetSalary={setSalaryStatus}
                      />
                    ))}
                  </div>
                </div>
              )}

              {!confirmedSources.length && !suggestions.length && !otherIncome.length && (
                <p className="muted-copy">No incoming income transactions are available yet. Import more statement history and WalletWise will build this view automatically.</p>
              )}
            </div>
          )}
        </section>
      ) : (
        <>
          <section className="filter-bar">
            <label className="search-control"><Search size={17} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search transactions" /></label>
            <label><SlidersHorizontal size={16} /><select value={category} onChange={(e) => setCategory(e.target.value)}>{categoryOptions.map((item) => <option key={item}>{item}</option>)}</select></label>
            <label><select value={role} onChange={(e) => setRole(e.target.value)}><option>All</option><option value="expense">Expenses</option><option value="income">Income</option><option value="transfer">Transfers</option><option value="refund">Refunds</option><option value="reimbursement">Reimbursements</option><option value="unknown">Unknown role</option></select></label>
            <label><select value={confidence} onChange={(e) => setConfidence(e.target.value)}><option>All</option><option value="high">High name confidence</option><option value="medium">Medium name confidence</option><option value="low">Low name confidence</option></select></label>
          </section>

          <section className="content-card table-card">
            <div className="table-wrap desktop-table-wrap">
              <table>
                <thead><tr><th>Date</th><th>Name</th><th>Role</th><th>Category</th><th className="numeric">Amount</th><th className="numeric">Balance</th><th>Tracking</th><th>Bank</th><th>Confidence</th><th>Actions</th></tr></thead>
                <tbody>
                  {filtered.map((transaction) => (
                    <tr key={transaction.id}>
                      <td>{shortDate(transaction.date)}</td>
                      <td><strong>{transaction.displayName || '—'}</strong><small className="cell-subtext">{transaction.rawDescription}</small></td>
                      <td><span className={`role-pill role-${transaction.financialRole || 'unknown'}`}>{roleLabel(transaction.financialRole)}</span><small className="cell-subtext"><ConfidenceBadge level={transaction.roleConfidence} compact /></small></td>
                      <td><span className="category-pill">{transaction.category}</span></td>
                      <td className={`numeric ${transaction.amount >= 0 ? 'amount-positive' : 'amount-negative'}`}>{currency(transaction.amount)}</td>
                      <td className="numeric">{currency(transaction.balance)}</td>
                      <td>
                        {transaction.canBeSalaryAnchor ? (
                          <button className={`salary-marker-button ${transaction.isSalary ? 'active' : ''}`} disabled={salarySavingId === transaction.id} onClick={() => toggleSalary(transaction)}>
                            <WalletCards size={14} /> {transaction.isSalary ? 'Salary anchor' : transaction.salaryExcluded ? 'Restore salary' : 'Mark salary'}
                          </button>
                        ) : <span className="cell-subtext">—</span>}
                      </td>
                      <td>{transaction.bank}</td>
                      <td><ConfidenceBadge level={transaction.namingConfidence} /></td>
                      <td>
                        <button className="transaction-edit-button" onClick={() => setEditingTransaction(transaction)} aria-label={`Edit ${transaction.displayName || 'transaction'}`}>
                          <Pencil size={14} /> Edit
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mobile-transaction-cards">
              {filtered.map((transaction) => (
                <article key={transaction.id} className="mobile-transaction-card">
                  <div className="mobile-card-top"><div><strong>{transaction.displayName || 'Unnamed'}</strong><span>{shortDate(transaction.date)} · {transaction.bank}</span></div><strong className={transaction.amount >= 0 ? 'amount-positive' : 'amount-negative'}>{currency(transaction.amount)}</strong></div>
                  <p>{transaction.rawDescription}</p>
                  <div className="mobile-card-bottom transaction-card-tags"><span className={`role-pill role-${transaction.financialRole || 'unknown'}`}>{roleLabel(transaction.financialRole)}</span><span className="category-pill">{transaction.category}</span><ConfidenceBadge level={transaction.roleConfidence} /></div>
                  <div className="mobile-transaction-actions">
                    {transaction.canBeSalaryAnchor && (
                      <button className={`salary-marker-button mobile-salary-button ${transaction.isSalary ? 'active' : ''}`} disabled={salarySavingId === transaction.id} onClick={() => toggleSalary(transaction)}>
                        <WalletCards size={14} /> {transaction.isSalary ? 'Used as salary' : transaction.salaryExcluded ? 'Restore salary' : 'Use as salary'}
                      </button>
                    )}
                    <button className="transaction-edit-button mobile" onClick={() => setEditingTransaction(transaction)}>
                      <Pencil size={14} /> Edit transaction
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </section>
          <p className="result-count">Showing {filtered.length} of {transactions.length} transactions</p>
        </>
      )}

      <TransactionEditModal
        transaction={editingTransaction}
        categories={categories}
        saving={transactionSaving}
        onClose={() => !transactionSaving && setEditingTransaction(null)}
        onSave={saveTransaction}
      />
    </div>
  );
}
