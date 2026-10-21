import { Pencil, Plus, Save, Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { api } from '../services/api';

const blankNaming = {
  matchText: '', displayName: '', category: 'Uncategorised',
  matchType: 'contains', scope: 'global', active: true,
};
const blankCategory = {
  matchText: '', category: 'Uncategorised', matchType: 'contains',
  confidence: 'high', priority: 50, active: true,
};

export default function RulesPage({
  rules, setRules, categoryRules, setCategoryRules, categories, refreshTransactions,
}) {
  const [tab, setTab] = useState('category');
  const [draft, setDraft] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const isCategory = tab === 'category';
  const items = isCategory ? categoryRules : rules;

  const beginCreate = () => {
    setEditingId(null);
    setDraft({ ...(isCategory ? blankCategory : blankNaming) });
    setError('');
  };
  const beginEdit = (rule) => {
    setEditingId(rule.id);
    setDraft({ ...rule });
    setError('');
  };

  const save = async () => {
    if (!draft.matchText.trim() || (isCategory ? !draft.category.trim() : !draft.displayName.trim())) return;
    setSaving(true);
    setError('');
    try {
      let saved;
      if (isCategory) {
        saved = editingId
          ? await api.updateCategoryRule(editingId, draft)
          : await api.createCategoryRule(draft);
        setCategoryRules((current) => editingId
          ? current.map((rule) => rule.id === editingId ? { ...rule, ...saved } : rule)
          : [saved, ...current]);
      } else {
        saved = editingId
          ? await api.updateRule(editingId, draft)
          : await api.createRule(draft);
        setRules((current) => editingId
          ? current.map((rule) => rule.id === editingId ? { ...rule, ...saved } : rule)
          : [saved, ...current]);
      }
      await refreshTransactions?.();
      setDraft(null);
      setEditingId(null);
    } catch (err) {
      setError(err.message || 'Could not save this rule.');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id) => {
    setError('');
    try {
      if (isCategory) {
        await api.deleteCategoryRule(id);
        setCategoryRules((current) => current.filter((rule) => rule.id !== id));
      } else {
        await api.deleteRule(id);
        setRules((current) => current.filter((rule) => rule.id !== id));
      }
    } catch (err) {
      setError(err.message || 'Could not delete this rule.');
    }
  };

  return (
    <div className="page">
      <div className="desktop-page-title page-title-with-action">
        <div>
          <span className="eyebrow">WalletWise knowledge</span>
          <h1>Rules</h1>
          <p>Naming rules decide the friendly name. Category rules decide what the spending means.</p>
        </div>
        <button className="primary-button" onClick={beginCreate}><Plus size={17} /> Add rule</button>
      </div>

      <div className="rule-tabs">
        <button className={tab === 'category' ? 'active' : ''} onClick={() => { setTab('category'); setDraft(null); }}>Category rules</button>
        <button className={tab === 'naming' ? 'active' : ''} onClick={() => { setTab('naming'); setDraft(null); }}>Naming rules</button>
      </div>

      {error && <div className="inline-alert error">{error}</div>}

      {draft && (
        <section className="content-card rule-editor">
          <div className="section-heading">
            <div><span className="eyebrow">{editingId ? 'Edit rule' : 'New rule'}</span><h2>{isCategory ? 'Categorisation rule' : 'Naming convention'}</h2></div>
            <button className="icon-button" onClick={() => setDraft(null)}><X size={19} /></button>
          </div>
          <div className="rule-form-grid">
            <label><span>Match text / pattern</span><input value={draft.matchText} onChange={(event) => setDraft({ ...draft, matchText: event.target.value })} placeholder={isCategory ? 'e.g. airtime' : 'e.g. checkers'} /></label>
            {!isCategory && <label><span>Friendly name</span><input value={draft.displayName} onChange={(event) => setDraft({ ...draft, displayName: event.target.value })} placeholder="e.g. Checkers" /></label>}
            <label><span>Category</span><select value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })}>{categories.map((item) => <option key={item}>{item}</option>)}</select></label>
            <label><span>Match type</span><select value={draft.matchType} onChange={(event) => setDraft({ ...draft, matchType: event.target.value })}><option value="contains">Contains</option><option value="exact">Exact</option><option value="starts_with">Starts with</option><option value="regex">Regex</option></select></label>
            {isCategory && <>
              <label><span>Confidence</span><select value={draft.confidence} onChange={(event) => setDraft({ ...draft, confidence: event.target.value })}><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
              <label><span>Priority</span><input type="number" min="0" max="999" value={draft.priority} onChange={(event) => setDraft({ ...draft, priority: Number(event.target.value) })} /></label>
            </>}
          </div>
          <div className="review-actions"><button className="primary-button" disabled={saving} onClick={save}><Save size={17} /> {saving ? 'Saving…' : 'Save rule'}</button></div>
        </section>
      )}

      <section className="content-card rules-list-card">
        <div className="rules-list">
          {items.map((rule) => (
            <div className="rule-row" key={rule.id}>
              <div className="rule-main">
                <strong>{isCategory ? rule.category : rule.displayName}</strong>
                <span>If description {rule.matchType.replace('_', ' ')} “{rule.matchText}”{isCategory ? ` · priority ${rule.priority}` : ''}</span>
              </div>
              <span className="category-pill">{rule.category}</span>
              {isCategory
                ? <span className={`scope-pill confidence-${rule.confidence}`}>{rule.confidence}</span>
                : <span className="scope-pill">{rule.scope}</span>}
              <div className="rule-actions">
                <button className="icon-button" title="Edit" onClick={() => beginEdit(rule)}><Pencil size={17} /></button>
                <button className="icon-button danger" title="Delete" onClick={() => remove(rule.id)}><Trash2 size={17} /></button>
              </div>
            </div>
          ))}
          {!items.length && <div className="rules-empty">No rules yet.</div>}
        </div>
      </section>
      <button className="floating-add mobile-only" onClick={beginCreate}><Plus size={22} /></button>
    </div>
  );
}
