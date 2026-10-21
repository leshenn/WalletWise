import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Plus,
  Tags,
  FileSearch,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import EmptyState from '../components/EmptyState';
import ConfidenceBadge from '../components/ConfidenceBadge';
import { api } from '../services/api';
import { currency, fullDate } from '../utils/formatters';

const ROLE_OPTIONS = [
  { value: 'unknown', label: 'Not sure yet' },
  { value: 'expense', label: 'Expense' },
  { value: 'income', label: 'Income' },
  { value: 'transfer', label: 'Transfer' },
  { value: 'refund', label: 'Refund / reimbursement' },
];

const ROLE_LABELS = Object.fromEntries(ROLE_OPTIONS.map((item) => [item.value, item.label]));

function sortCategoryNames(values) {
  return Array.from(new Set(values)).sort((a, b) => {
    if (a === 'Uncategorised') return 1;
    if (b === 'Uncategorised') return -1;
    return a.localeCompare(b);
  });
}

export default function ReviewPage({
  transactions,
  refreshTransactions,
  categories,
  setCategories,
}) {
  const [buckets, setBuckets] = useState({
    roleReview: [],
    uncategorised: [],
    categoryReview: [],
    nameReview: [],
    counts: { roleReview: 0, uncategorised: 0, categoryReview: 0, nameReview: 0 },
  });
  const [loading, setLoading] = useState(true);
  const [savingGroupId, setSavingGroupId] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [skippedGroupIds, setSkippedGroupIds] = useState(() => new Set());

  const loadGroups = async () => {
    setLoading(true);
    setError('');
    try {
      setBuckets(await api.getReviewGroups());
    } catch (err) {
      setError(err.message || 'Could not load review groups.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadGroups();
  }, [transactions]);

  const createCategory = async (name) => {
    const cleaned = name.trim();
    if (!cleaned) throw new Error('Category name is required.');

    const created = await api.createCategory(cleaned);
    const categoryName = created.name;
    setCategories?.((current) => sortCategoryNames([...current, categoryName]));
    return categoryName;
  };

  const confirmGroup = async (group, form) => {
    if (!form.selectedIds.length || form.financialRole === 'unknown') return;
    setSavingGroupId(group.groupId);
    setError('');
    setNotice('');

    try {
      const result = await api.confirmReview({
        transactionIds: form.selectedIds,
        displayName: form.displayName.trim() || group.suggestedDisplayName,
        financialRole: form.financialRole,
        category: form.category,
      });
      setNotice(result.message || 'Saved.');
      setSkippedGroupIds((current) => {
        const next = new Set(current);
        next.delete(group.groupId);
        return next;
      });
      await refreshTransactions();
    } catch (err) {
      setError(err.message || 'Could not save these transactions.');
    } finally {
      setSavingGroupId(null);
    }
  };

  const skipGroup = (groupId) => {
    setSkippedGroupIds((current) => new Set([...current, groupId]));
  };

  const allGroups = useMemo(() => [
    ...(buckets.roleReview || []),
    ...(buckets.uncategorised || []),
    ...(buckets.categoryReview || []),
    ...(buckets.nameReview || []),
  ], [buckets]);

  const visibleTotal = allGroups
    .filter((group) => !skippedGroupIds.has(group.groupId))
    .reduce((sum, group) => sum + group.size, 0);

  if (loading && !allGroups.length) {
    return (
      <div className="page narrow-page">
        <div className="review-summary"><span>Finding transactions that need attention…</span></div>
      </div>
    );
  }

  const total = Object.values(buckets.counts || {}).reduce((sum, value) => sum + Number(value || 0), 0);
  if (!total) {
    return (
      <div className="page narrow-page">
        <div className="desktop-page-title"><span className="eyebrow">Human-in-the-loop</span><h1>Review</h1></div>
        <EmptyState icon={CheckCircle2} title="Everything important is confirmed" body="WalletWise has a financial role and usable interpretation for every transaction in this dataset." />
      </div>
    );
  }

  return (
    <div className="page narrow-page">
      <div className="desktop-page-title">
        <span className="eyebrow">Human-in-the-loop</span>
        <h1>Review transactions</h1>
        <p>Confirm what the money movement actually is first, then categorise it. Transfers are kept out of income and spending reports.</p>
      </div>

      <div className="review-progress-summary">
        <div><strong>{visibleTotal}</strong><span>transactions still visible this session</span></div>
        <small>Use “Skip for now” when you do not recognise something. It will come back after a refresh.</small>
      </div>

      {error && <div className="inline-alert error">{error}</div>}
      {notice && <div className="inline-alert success">{notice}</div>}

      <ReviewSection
        title="Confirm money movement"
        body="These could be income, transfers, refunds, or expenses. Confirming this first prevents transfers from distorting your reports."
        icon={CircleDollarSign}
        tone="priority"
        groups={buckets.roleReview || []}
        categories={categories}
        savingGroupId={savingGroupId}
        onConfirm={confirmGroup}
        onCreateCategory={createCategory}
        onSkip={skipGroup}
        skippedGroupIds={skippedGroupIds}
      />

      <ReviewSection
        title="Needs categorisation"
        body="WalletWise knows what kind of money movement this is, but still needs a useful category."
        icon={AlertTriangle}
        tone="priority"
        groups={buckets.uncategorised || []}
        categories={categories}
        savingGroupId={savingGroupId}
        onConfirm={confirmGroup}
        onCreateCategory={createCategory}
        onSkip={skipGroup}
        skippedGroupIds={skippedGroupIds}
      />

      <ReviewSection
        title="Category suggestions — verify"
        body="These already have a category suggestion, but WalletWise is not fully confident yet."
        icon={Tags}
        tone="secondary"
        groups={buckets.categoryReview || []}
        categories={categories}
        savingGroupId={savingGroupId}
        onConfirm={confirmGroup}
        onCreateCategory={createCategory}
        onSkip={skipGroup}
        skippedGroupIds={skippedGroupIds}
      />

      <ReviewSection
        title="Name / extraction review"
        body="Lowest priority: reporting can already use these, but the friendly name or extraction deserves a quick look."
        icon={FileSearch}
        tone="secondary"
        groups={buckets.nameReview || []}
        categories={categories}
        savingGroupId={savingGroupId}
        onConfirm={confirmGroup}
        onCreateCategory={createCategory}
        onSkip={skipGroup}
        skippedGroupIds={skippedGroupIds}
      />
    </div>
  );
}

function ReviewSection({
  title,
  body,
  icon: Icon,
  tone,
  groups,
  categories,
  savingGroupId,
  onConfirm,
  onCreateCategory,
  onSkip,
  skippedGroupIds,
}) {
  const visibleGroups = groups.filter((group) => !skippedGroupIds.has(group.groupId));
  if (!visibleGroups.length) return null;
  const count = visibleGroups.reduce((sum, group) => sum + group.size, 0);

  return (
    <section className={`review-priority-section ${tone}`}>
      <div className="review-section-heading">
        <span className="review-section-icon"><Icon size={18} /></span>
        <div><h2>{title}</h2><p>{body}</p></div>
        <strong>{count}</strong>
      </div>
      <div className="review-stack">
        {visibleGroups.map((group) => (
          <ReviewGroupCard
            key={group.groupId}
            group={group}
            categories={categories}
            saving={savingGroupId === group.groupId}
            onConfirm={onConfirm}
            onCreateCategory={onCreateCategory}
            onSkip={() => onSkip(group.groupId)}
          />
        ))}
      </div>
    </section>
  );
}

function ReviewGroupCard({ group, categories, saving, onConfirm, onCreateCategory, onSkip }) {
  const [displayName, setDisplayName] = useState(group.suggestedDisplayName || '');
  const [category, setCategory] = useState(group.suggestedCategory || 'Uncategorised');
  const [financialRole, setFinancialRole] = useState(group.suggestedFinancialRole || 'unknown');
  const [expanded, setExpanded] = useState(false);
  const [selectedIds, setSelectedIds] = useState(() => group.transactions.map((item) => item.id));
  const [showCategoryCreator, setShowCategoryCreator] = useState(false);
  const [newCategory, setNewCategory] = useState('');
  const [addingCategory, setAddingCategory] = useState(false);
  const [categoryError, setCategoryError] = useState('');

  useEffect(() => {
    setDisplayName(group.suggestedDisplayName || '');
    setCategory(group.suggestedCategory || 'Uncategorised');
    setFinancialRole(group.suggestedFinancialRole || 'unknown');
    setSelectedIds(group.transactions.map((item) => item.id));
    setShowCategoryCreator(false);
    setNewCategory('');
    setCategoryError('');
  }, [
    group.groupId,
    group.suggestedDisplayName,
    group.suggestedCategory,
    group.suggestedFinancialRole,
    group.transactions,
  ]);

  const toggleTransaction = (id) => {
    setSelectedIds((current) => current.includes(id)
      ? current.filter((item) => item !== id)
      : [...current, id]);
  };

  const handleRoleChange = (event) => {
    setFinancialRole(event.target.value);
  };

  const handleCategoryChange = (event) => {
    const value = event.target.value;
    if (value === '__add_category__') {
      setShowCategoryCreator(true);
      setCategoryError('');
      return;
    }
    setCategory(value);
    setShowCategoryCreator(false);
    setCategoryError('');
  };

  const addCategory = async () => {
    const name = newCategory.trim();
    if (!name) return;
    setAddingCategory(true);
    setCategoryError('');
    try {
      const createdName = await onCreateCategory(name);
      setCategory(createdName);
      setNewCategory('');
      setShowCategoryCreator(false);
    } catch (err) {
      setCategoryError(err.message || 'Could not add this category.');
    } finally {
      setAddingCategory(false);
    }
  };

  const allSelected = selectedIds.length === group.transactions.length;
  const total = group.transactions.reduce((sum, item) => sum + Math.abs(Number(item.amount) || 0), 0);

  return (
    <article className="review-card review-group-card">
      <div className="review-card-header">
        <div>
          <div className="review-group-kicker">
            <span>{group.size === 1 ? 'Single transaction' : `${group.size} similar transactions`}</span>
            {group.groupingConfidence !== 'single' && (
              <span className={`group-confidence ${group.groupingConfidence}`}>{group.groupingConfidence} grouping confidence</span>
            )}
          </div>
          <h3>{group.suggestedDisplayName || group.transactions[0]?.rawDescription}</h3>
          <small>{group.groupingReason}</small>
        </div>
        <strong>{currency(total)}</strong>
      </div>

      <div className="review-form review-form-three">
        <label><span>Friendly name</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
        <div className="review-category-field">
          <span className="review-field-label">What kind of money movement is this?</span>
          <div className="select-wrap">
            <select value={financialRole} onChange={handleRoleChange}>
              {ROLE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <ChevronDown size={16} />
          </div>
          {financialRole === 'transfer' && (
            <small className="role-helper">Transfers are excluded from income and spending totals.</small>
          )}
        </div>
        <div className="review-category-field">
          <span className="review-field-label">Category</span>
          <div className="select-wrap">
            <select value={category} onChange={handleCategoryChange}>
              {categories.map((item) => <option key={item} value={item}>{item}</option>)}
              <option value="__add_category__">＋ Add new category…</option>
            </select>
            <ChevronDown size={16} />
          </div>
          {showCategoryCreator && (
            <div className="inline-category-create">
              <input
                autoFocus
                value={newCategory}
                onChange={(event) => setNewCategory(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    addCategory();
                  }
                  if (event.key === 'Escape') {
                    setShowCategoryCreator(false);
                    setNewCategory('');
                  }
                }}
                placeholder="New category name"
              />
              <button type="button" className="secondary-button compact-button" disabled={addingCategory || !newCategory.trim()} onClick={addCategory}>
                <Plus size={15} /> {addingCategory ? 'Adding…' : 'Add'}
              </button>
              <button type="button" className="icon-button" title="Cancel" onClick={() => {
                setShowCategoryCreator(false);
                setNewCategory('');
                setCategoryError('');
              }}>
                <X size={16} />
              </button>
            </div>
          )}
          {categoryError && <small className="field-error">{categoryError}</small>}
        </div>
      </div>

      <button className="explain-toggle review-group-toggle" onClick={() => setExpanded(!expanded)}>
        {expanded ? 'Hide transactions' : `View ${group.size} transaction${group.size === 1 ? '' : 's'}`}
        <ChevronDown size={15} className={expanded ? 'rotated' : ''} />
      </button>

      {expanded && (
        <div className="review-group-details">
          {group.size > 1 && (
            <label className="group-select-all">
              <input type="checkbox" checked={allSelected} onChange={() => setSelectedIds(allSelected ? [] : group.transactions.map((item) => item.id))} />
              <span>Select all transactions in this group</span>
            </label>
          )}
          <div className="group-transaction-list">
            {group.transactions.map((transaction) => (
              <label className={`group-transaction-row ${selectedIds.includes(transaction.id) ? 'selected' : ''}`} key={transaction.id}>
                <input type="checkbox" checked={selectedIds.includes(transaction.id)} onChange={() => toggleTransaction(transaction.id)} />
                <div className="group-transaction-copy">
                  <strong>{transaction.rawDescription}</strong>
                  <span>{fullDate(transaction.date)} · {ROLE_LABELS[transaction.financialRole] || 'Unknown role'} · {transaction.category}</span>
                  <div className="confidence-row compact">
                    <span>Role <ConfidenceBadge level={transaction.roleConfidence} /></span>
                    <span>Category <ConfidenceBadge level={transaction.categoryConfidence} /></span>
                    <span>Name <ConfidenceBadge level={transaction.namingConfidence} /></span>
                    <span>Extraction <ConfidenceBadge level={transaction.extractionConfidence} /></span>
                  </div>
                </div>
                <strong className={transaction.amount >= 0 ? 'amount-positive' : 'amount-negative'}>{currency(transaction.amount)}</strong>
              </label>
            ))}
          </div>
        </div>
      )}

      <div className="review-actions grouped-actions review-actions-split">
        <span>{selectedIds.length} of {group.size} selected</span>
        <div className="review-action-buttons">
          <button type="button" className="text-button" disabled={saving} onClick={onSkip}>Skip for now</button>
          <button
            className="primary-button"
            disabled={saving || !selectedIds.length || financialRole === 'unknown'}
            onClick={() => onConfirm(group, { displayName, category, financialRole, selectedIds })}
          >
            {saving ? 'Saving…' : selectedIds.length > 1 ? `Confirm ${selectedIds.length} transactions` : 'Confirm transaction'}
          </button>
        </div>
      </div>
    </article>
  );
}
