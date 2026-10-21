import { useEffect, useMemo, useState } from 'react';
import { PiggyBank } from 'lucide-react';
import EmptyState from '../components/EmptyState';
import { api } from '../services/api';
import { currency } from '../utils/formatters';

const ESSENTIAL_FIELDS = [
  { key: 'housing', label: 'Housing / rent / bond', hint: 'Rent, bond or your regular household contribution' },
  { key: 'groceries', label: 'Groceries', hint: 'Food and household basics' },
  { key: 'transport', label: 'Transport & fuel', hint: 'Fuel, taxis, ride-hailing or public transport' },
  { key: 'utilities', label: 'Bills & utilities', hint: 'Electricity, water and municipal costs' },
  { key: 'mobileInternet', label: 'Mobile, data & internet', hint: 'Airtime, data, fibre and mobile contracts' },
  { key: 'insurance', label: 'Insurance', hint: 'Car, home, funeral or other insurance' },
  { key: 'healthcare', label: 'Healthcare', hint: 'Medical aid, medication and appointments' },
  { key: 'debtRepayments', label: 'Debt repayments', hint: 'Minimum loan, credit-card or account repayments' },
  { key: 'toiletries', label: 'Toiletries & personal care', hint: 'Toiletries, grooming and personal-care basics' },
  { key: 'familySupport', label: 'Family support', hint: 'Money you regularly give to family or dependants' },
  { key: 'educationChildcare', label: 'Education / childcare', hint: 'School, courses, childcare or recurring education costs' },
  { key: 'otherEssentials', label: 'Other necessities', hint: 'Regular essentials that are not covered above' },
];

const DEFAULT_PROFILE = {
  livingSituation: 'family',
  householdResponsibility: 'self',
  transportStyle: 'car',
  goal: 'emergency',
  budgetStyle: 'balanced',
  incomeStability: 'stable',
  emergencyFundLevel: 'none',
  savingsTargetAmount: null,
  expenseEstimates: {},
};

function normaliseProfile(profile) {
  const merged = { ...DEFAULT_PROFILE, ...(profile || {}) };
  return {
    ...merged,
    expenseEstimates: Object.fromEntries(
      ESSENTIAL_FIELDS.map(({ key }) => [key, merged.expenseEstimates?.[key] ?? '']),
    ),
  };
}

function toNumberOrNull(value) {
  if (value === '' || value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function statusFor(item, actual) {
  const budgeted = Number(item.budgeted ?? item.suggested ?? 0);
  if (item.kind === 'buffer') return { label: 'Planned buffer', tone: 'neutral' };
  if (item.kind === 'savings') {
    if (actual >= budgeted) return { label: 'Target met', tone: 'good' };
    if (budgeted > 0 && actual >= budgeted * 0.8) return { label: 'Close to target', tone: 'warning' };
    return { label: 'Below target', tone: 'bad' };
  }
  if (actual <= budgeted) return { label: 'Within budget', tone: 'good' };
  if (budgeted > 0 && actual <= budgeted * 1.1) return { label: 'Slightly over', tone: 'warning' };
  return { label: 'Over budget', tone: 'bad' };
}

function differenceText(item, actual) {
  const budgeted = Number(item.budgeted ?? item.suggested ?? 0);
  if (item.kind === 'buffer') return '—';
  if (item.kind === 'savings') {
    const difference = actual - budgeted;
    return difference >= 0 ? `${currency(difference)} ahead` : `${currency(Math.abs(difference))} behind`;
  }
  const difference = budgeted - actual;
  return difference >= 0 ? `${currency(difference)} left` : `${currency(Math.abs(difference))} over`;
}

export default function BudgetPage({ reporting, refreshReports }) {
  const budget = reporting?.budget || {};
  const months = reporting?.monthly || [];
  const [profile, setProfile] = useState(() => normaliseProfile(budget.profile));
  const [selectedMonth, setSelectedMonth] = useState(() => months.at(-1)?.month || '');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    setProfile(normaliseProfile(budget.profile));
  }, [budget.profile]);

  useEffect(() => {
    if (!months.length) return;
    if (!selectedMonth || !months.some((month) => month.month === selectedMonth)) {
      setSelectedMonth(months[months.length - 1].month);
    }
  }, [months, selectedMonth]);

  const selectedMonthData = useMemo(
    () => months.find((month) => month.month === selectedMonth) || months.at(-1) || null,
    [months, selectedMonth],
  );

  const estimatedTotal = useMemo(() => (
    Object.values(profile.expenseEstimates || {}).reduce((total, value) => {
      const number = toNumberOrNull(value);
      return total + (number ?? 0);
    }, 0)
  ), [profile.expenseEstimates]);

  const actualFor = (item) => Number(item.actualByMonth?.[selectedMonth] || 0);
  const expenseItems = (budget.items || []).filter((item) => item.kind === 'expense');
  const selectedActualSpend = Number(selectedMonthData?.spending ?? expenseItems.reduce((total, item) => total + actualFor(item), 0));
  const selectedActualSavings = Number((budget.items || []).find((item) => item.kind === 'savings')?.actualByMonth?.[selectedMonth] || 0);
  const budgetRemaining = Number(budget.plannedExpenses || 0) - selectedActualSpend;

  const setField = (field, value) => setProfile((current) => ({ ...current, [field]: value }));
  const setEstimate = (key, value) => setProfile((current) => ({
    ...current,
    expenseEstimates: { ...current.expenseEstimates, [key]: value },
  }));

  const saveProfile = async () => {
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const payload = {
        livingSituation: profile.livingSituation,
        householdResponsibility: profile.householdResponsibility,
        transportStyle: profile.transportStyle,
        goal: profile.goal,
        budgetStyle: profile.budgetStyle,
        incomeStability: profile.incomeStability,
        emergencyFundLevel: profile.emergencyFundLevel,
        // Savings is intentionally suggested from the essentials + lifestyle profile.
        savingsTargetAmount: null,
        expenseEstimates: Object.fromEntries(
          ESSENTIAL_FIELDS.map(({ key }) => [key, toNumberOrNull(profile.expenseEstimates?.[key])]),
        ),
      };
      await api.updateBudgetProfile(payload);
      await refreshReports?.();
      setMessage('Your budget has been rebuilt from your necessities and priorities.');
    } catch (saveError) {
      setError(saveError.message || 'Could not update your budget.');
    } finally {
      setSaving(false);
    }
  };

  if (!reporting?.summary?.transactionCount) {
    return (
      <div className="page budget-page">
        <div className="desktop-page-title"><span className="eyebrow">Plan</span><h1>Budget</h1></div>
        <EmptyState icon={PiggyBank} title="Import a statement first" body="WalletWise uses your real spending history to make the first budget suggestion more useful." />
      </div>
    );
  }

  return (
    <div className="page budget-page">
      <div className="desktop-page-title">
        <span className="eyebrow">Plan</span>
        <h1>Budget</h1>
        <p>Start with the necessities you know. WalletWise uses what is left, your recent spending and your priorities to suggest savings and flexible spending.</p>
      </div>

      <nav className="report-period-nav budget-period-nav" aria-label="Budget navigation">
        <div className="report-section-nav" aria-label="Jump to budget section">
          <button type="button" onClick={() => document.getElementById('budget-overview')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Overview</button>
          <button type="button" onClick={() => document.getElementById('budget-actual')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Budget vs actual</button>
          <button type="button" onClick={() => document.getElementById('budget-necessities')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Necessities</button>
          <button type="button" onClick={() => document.getElementById('budget-preferences')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Preferences</button>
        </div>
        <div className="report-period-nav-controls budget-period-control">
          <label>
            <span>Actual month</span>
            <select value={selectedMonth} onChange={(event) => setSelectedMonth(event.target.value)}>
              {[...months].reverse().map((month) => (
                <option key={month.month} value={month.month}>{month.label}</option>
              ))}
            </select>
          </label>
        </div>
      </nav>

      <section className="content-card budget-overview-card" id="budget-overview">
        <div className="report-section-heading-row">
          <div>
            <span className="eyebrow">Your plan</span>
            <h2>A monthly budget built around the essentials first</h2>
            <p>{budget.method || 'Your known necessities are protected first; WalletWise then suggests savings and flexible spending from what remains.'}</p>
          </div>
        </div>

        <div className="budget-overview-grid budget-overview-grid-six">
          <div><span>Typical income</span><strong>{currency(budget.incomeBasis || 0)}</strong><small>Based on imported income</small></div>
          <div><span>Necessities</span><strong>{currency(budget.essentialBudget || 0)}</strong><small>Known and inferred essentials</small></div>
          <div><span>Flexible budget</span><strong>{currency(budget.flexibleBudget || 0)}</strong><small>Eating out, shopping, entertainment and subscriptions</small></div>
          <div><span>Savings target</span><strong>{currency(budget.savingsTarget || 0)}</strong><small>{Number(budget.savingsTargetPct || 0).toFixed(1)}% of typical income</small></div>
          <div><span>Breathing room</span><strong>{currency(budget.bufferTarget || 0)}</strong><small>Money intentionally left unallocated</small></div>
          <div className={Number(budget.remainingAfterPlan || 0) < 0 ? 'budget-overview-warning' : ''}><span>Still unallocated</span><strong>{currency(budget.remainingAfterPlan || 0)}</strong><small>{budget.overBudget ? 'Necessities currently exceed the available income' : 'Extra room after the suggested plan'}</small></div>
        </div>
      </section>

      <section className="content-card budget-results-card" id="budget-actual">
        <div className="report-section-heading-row">
          <div>
            <span className="eyebrow">Budget vs actual</span>
            <h2>{selectedMonthData?.label || 'Selected month'}</h2>
            <p>Budgeted is the monthly plan. Actual is what WalletWise can see in the selected month from your categorised transactions.</p>
          </div>
        </div>

        <div className="budget-actual-summary">
          <div><span>Expense budget</span><strong>{currency(budget.plannedExpenses || 0)}</strong></div>
          <div><span>Actual expenses</span><strong>{currency(selectedActualSpend)}</strong></div>
          <div><span>Expense budget left</span><strong className={budgetRemaining >= 0 ? 'amount-positive' : 'amount-negative'}>{currency(Math.abs(budgetRemaining))}{budgetRemaining < 0 ? ' over' : ''}</strong></div>
          <div><span>Actual savings</span><strong>{currency(selectedActualSavings)}</strong></div>
        </div>

        <div className="budget-table-wrap detailed-budget-table-wrap budget-desktop-comparison">
          <table className="budget-table detailed-budget-table budget-vs-actual-table">
            <thead><tr><th>Budget area</th><th>Budgeted</th><th>Actual</th><th>Difference</th><th>Status</th></tr></thead>
            <tbody>
              {(budget.items || []).map((item) => {
                const actual = item.kind === 'buffer' ? 0 : actualFor(item);
                const status = statusFor(item, actual);
                return (
                  <tr key={item.key || `${item.kind}-${item.category}`}>
                    <td>
                      <strong>{item.category}</strong>
                      <small>{item.group === 'essential' ? 'Necessity' : item.kind === 'savings' ? 'Savings' : item.kind === 'buffer' ? 'Buffer' : 'Flexible spending'} · {item.source || 'WalletWise suggestion'}</small>
                    </td>
                    <td><strong>{currency(item.budgeted ?? item.suggested ?? 0)}</strong></td>
                    <td>{item.kind === 'buffer' ? '—' : currency(actual)}</td>
                    <td>{differenceText(item, actual)}</td>
                    <td><span className={`budget-status ${status.tone}`}>{status.label}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="budget-mobile-comparison" aria-label="Budget versus actual cards">
          {(budget.items || []).map((item) => {
            const actual = item.kind === 'buffer' ? 0 : actualFor(item);
            const budgeted = Number(item.budgeted ?? item.suggested ?? 0);
            const status = statusFor(item, actual);
            const progress = item.kind === 'buffer' || budgeted <= 0 ? 0 : Math.min(100, (actual / budgeted) * 100);
            const groupLabel = item.group === 'essential'
              ? 'Necessity'
              : item.kind === 'savings'
                ? 'Savings'
                : item.kind === 'buffer'
                  ? 'Buffer'
                  : 'Flexible';
            return (
              <article className="budget-mobile-item" key={`mobile-${item.key || `${item.kind}-${item.category}`}`}>
                <div className="budget-mobile-item-head">
                  <div>
                    <span className="budget-mobile-kicker">{groupLabel}</span>
                    <h3>{item.category}</h3>
                  </div>
                  <span className={`budget-status ${status.tone}`}>{status.label}</span>
                </div>

                <div className="budget-mobile-values">
                  <div><span>Budgeted</span><strong>{currency(budgeted)}</strong></div>
                  <div><span>Actual</span><strong>{item.kind === 'buffer' ? '—' : currency(actual)}</strong></div>
                  <div className="budget-mobile-difference"><span>Difference</span><strong>{differenceText(item, actual)}</strong></div>
                </div>

                {item.kind !== 'buffer' && budgeted > 0 && (
                  <div className="budget-mobile-progress" aria-hidden="true">
                    <div className={`budget-mobile-progress-fill ${status.tone}`} style={{ width: `${progress}%` }} />
                  </div>
                )}
                <small className="budget-mobile-source">{item.source || 'WalletWise suggestion'}</small>
              </article>
            );
          })}
        </div>
      </section>

      <section className="content-card budget-estimates-card" id="budget-necessities">
        <div className="report-section-heading-row">
          <div>
            <span className="eyebrow">Necessities</span>
            <h2>Tell WalletWise the costs you already know</h2>
            <p>Enter a rough monthly amount. Use 0 when you genuinely do not have that expense. Leave it blank when WalletWise should estimate it from your imported history.</p>
          </div>
          <div className="budget-estimate-total"><span>Known necessities entered</span><strong>{currency(estimatedTotal)}</strong></div>
        </div>

        <div className="budget-estimate-grid">
          {ESSENTIAL_FIELDS.map((field) => (
            <label key={field.key} className="budget-estimate-field">
              <span>{field.label}</span>
              <small>{field.hint}</small>
              <div className="money-input"><b>R</b><input type="number" min="0" step="50" value={profile.expenseEstimates?.[field.key] ?? ''} placeholder="Blank = use history" onChange={(event) => setEstimate(field.key, event.target.value)} /></div>
            </label>
          ))}
        </div>
      </section>

      <section className="content-card budget-questionnaire-page" id="budget-preferences">
        <div className="report-section-heading-row">
          <div>
            <span className="eyebrow">Preferences</span>
            <h2>How should WalletWise use the money left after necessities?</h2>
            <p>These answers influence the suggested savings target, flexible spending and safety buffer.</p>
          </div>
        </div>

        <div className="budget-profile-grid">
          <label><span>Living situation</span><select value={profile.livingSituation} onChange={(event) => setField('livingSituation', event.target.value)}><option value="family">Living with family</option><option value="rent">Renting</option><option value="own">Own home / bond</option></select></label>
          <label><span>Household responsibility</span><select value={profile.householdResponsibility} onChange={(event) => setField('householdResponsibility', event.target.value)}><option value="self">Mostly just myself</option><option value="share">I share household costs</option><option value="support">I support other people too</option></select></label>
          <label><span>How do you usually get around?</span><select value={profile.transportStyle} onChange={(event) => setField('transportStyle', event.target.value)}><option value="car">Own car</option><option value="public">Public transport</option><option value="ride">Ride-hailing</option><option value="remote">Mostly work / study from home</option></select></label>
          <label><span>Income stability</span><select value={profile.incomeStability} onChange={(event) => setField('incomeStability', event.target.value)}><option value="stable">Mostly stable</option><option value="variable">Changes month to month</option></select></label>
          <label><span>Main money goal</span><select value={profile.goal} onChange={(event) => setField('goal', event.target.value)}><option value="emergency">Build an emergency fund</option><option value="purchase">Save for a major purchase</option><option value="debt">Pay down debt</option><option value="breathing">More breathing room each month</option></select></label>
          <label><span>How strict should the budget feel?</span><select value={profile.budgetStyle} onChange={(event) => setField('budgetStyle', event.target.value)}><option value="balanced">Balanced</option><option value="conservative">More disciplined</option><option value="flexible">More flexible</option></select></label>
          <label><span>Emergency savings today</span><select value={profile.emergencyFundLevel} onChange={(event) => setField('emergencyFundLevel', event.target.value)}><option value="none">None yet</option><option value="under_one">Less than 1 month of expenses</option><option value="one_to_three">About 1–3 months</option><option value="three_plus">3+ months</option></select></label>
        </div>

        <div className="budget-form-footer">
          <p><strong>WalletWise will decide the first draft:</strong> necessities first, then savings and breathing room, then flexible spending such as eating out, shopping, entertainment and subscriptions.</p>
          <button className="primary-button" type="button" disabled={saving} onClick={saveProfile}>{saving ? 'Building budget…' : budget.profileCompleted ? 'Rebuild my budget' : 'Create my budget'}</button>
        </div>
        {message && <div className="inline-alert success">{message}</div>}
        {error && <div className="inline-alert error">{error}</div>}
      </section>
    </div>
  );
}
