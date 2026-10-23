import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, ArrowRight, CheckCircle2, FolderPlus, Layers3, Pencil, Search, Trash2, X } from 'lucide-react';
import { api } from '../services/api';


// Native <dialog> uses the browser top layer, so the editor remains centred
// in the visible viewport, even inside animated/transformed page containers.
function CategoryActionDialog({ action, items, selected, target, setTarget, saving, error, onClose, onRun }) {
  const dialogRef = useRef(null);
  const fieldRef = useRef(null);
  const closeRef = useRef(null);

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return undefined;

    const previouslyFocused = document.activeElement;
    const oldHtmlOverflow = document.documentElement.style.overflow;
    const oldBodyOverflow = document.body.style.overflow;

    dialog.showModal();
    document.documentElement.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';
    // Prevent focus from scrolling the page back to its top.
    (fieldRef.current || closeRef.current)?.focus({ preventScroll: true });

    return () => {
      if (dialog.open) dialog.close();
      document.documentElement.style.overflow = oldHtmlOverflow;
      document.body.style.overflow = oldBodyOverflow;
      if (previouslyFocused?.isConnected) previouslyFocused.focus({ preventScroll: true });
    };
  }, [action.mode, action.name]);

  const title = action.mode === 'rename' ? 'Rename category'
    : action.mode === 'merge' ? 'Merge categories' : 'Delete category';
  const canSubmit = !saving && (
    action.mode === 'delete'
    || (Boolean(target.trim()) && (action.mode !== 'rename' || target.trim() !== action.name))
  );

  return createPortal(
    <dialog
      ref={dialogRef}
      className="category-dialog"
      aria-labelledby="category-dialog-title"
      aria-describedby="category-dialog-subtitle"
      onCancel={event => { event.preventDefault(); if (!saving) onClose(); }}
      onMouseDown={event => {
        const bounds = event.currentTarget.getBoundingClientRect();
        const outside = event.clientX < bounds.left || event.clientX > bounds.right
          || event.clientY < bounds.top || event.clientY > bounds.bottom;
        if (outside && !saving) onClose();
      }}
    >
      <div className="category-dialog-heading">
        <h2 id="category-dialog-title">{title}</h2>
        <button ref={closeRef} type="button" aria-label="Close dialog" onClick={onClose} disabled={saving}><X size={19}/></button>
      </div>
      <p id="category-dialog-subtitle">Category: <strong>{action.name}</strong></p>
      {action.mode === 'rename' && <label className="category-dialog-field">New name
        <input ref={fieldRef} value={target} maxLength={60} onChange={event => setTarget(event.target.value)} />
      </label>}
      {action.mode === 'merge' && <>
        <p>Move every transaction from <strong>{action.name}</strong> into the category you select. Matching naming and category rules will also be updated.</p>
        <label className="category-dialog-field">Merge into
          <select ref={fieldRef} value={target} onChange={event => setTarget(event.target.value)}>
            <option value="">Choose destination…</option>
            {items.filter(item => item.name !== action.name && (!item.protected || item.name === 'Uncategorised')).map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
          </select>
        </label>
      </>}
      {action.mode === 'delete' && <p className="category-delete-warning">This will move all <strong>{selected?.totalTransactionCount || 0}</strong> transactions using this category (across every bank) to <strong>Uncategorised</strong>. Saved rules using it will also be redirected. This cannot be undone automatically.</p>}
      {error && <div className="category-feedback error" role="alert">{error}</div>}
      <div className="category-dialog-buttons">
        <button type="button" className="secondary-button" disabled={saving} onClick={onClose}>Cancel</button>
        <button type="button" className={action.mode === 'delete' ? 'category-danger-button' : 'primary-button'} disabled={!canSubmit} onClick={() => onRun(action.mode)}>
          {saving ? 'Saving…' : action.mode === 'rename' ? 'Save name' : action.mode === 'merge' ? 'Merge categories' : 'Delete category'}
          <ArrowRight size={15}/>
        </button>
      </div>
    </dialog>,
    document.body,
  );
}

export default function CategoriesPage({ bankScope = 'all', refreshTransactions, refreshCategories }) {
  const [items, setItems] = useState([]);
  const [search, setSearch] = useState('');
  const [newName, setNewName] = useState('');
  const [action, setAction] = useState(null);
  const [target, setTarget] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = async () => {
    setLoading(true);
    try { setItems(await api.getCategoryOverview()); setError(''); }
    catch (err) { setError(err.message || 'Could not load categories.'); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, [bankScope]);
  const visible = useMemo(() => items.filter(item => item.name.toLowerCase().includes(search.toLowerCase())), [items, search]);
  const total = items.reduce((sum, item) => sum + item.transactionCount, 0);
  const selected = action && items.find(item => item.name === action.name);

  const reloadAfterMutation = async (result) => {
    await refreshCategories();
    await refreshTransactions();
    await load();
    setAction(null);
    setTarget('');
    return result;
  };
  const run = async (operation) => {
    setSaving(true); setError(''); setNotice('');
    try {
      let result;
      if (operation === 'create') {
        result = await api.createCategory(newName.trim());
        setNewName('');
        await reloadAfterMutation(result);
        setNotice(`Category “${result.name}” is ready to use.`);
      } else if (operation === 'rename') {
        result = await api.renameCategory(action.name, target.trim());
        await reloadAfterMutation(result);
        setNotice(`Renamed to “${result.target}”. ${result.transactionsUpdated} transaction(s) updated.`);
      } else if (operation === 'merge') {
        result = await api.mergeCategories(action.name, target);
        await reloadAfterMutation(result);
        setNotice(`Merged into “${result.target}”. ${result.transactionsUpdated} transaction(s) moved; saved rules were updated.`);
      } else if (operation === 'delete') {
        result = await api.deleteCategory(action.name);
        await reloadAfterMutation(result);
        setNotice(`Deleted “${result.source}”. ${result.transactionsUpdated} transaction(s) reassigned to Uncategorised.`);
      }
    } catch (err) { setError(err.message || 'The category could not be updated.'); }
    finally { setSaving(false); }
  };
  const open = (mode, item) => { setAction({ mode, name: item.name }); setTarget(mode === 'rename' ? item.name : ''); setError(''); setNotice(''); };

  return <div className="page category-management-page">
    <div className="page-heading desktop-page-title"><p>MANAGE YOUR DATA</p><h1>Categories</h1><span>Keep your financial reports organised. Create, rename, merge or remove categories in one place.</span></div>
    <div className="category-manager-intro">
      <div className="category-stat"><span>Categories</span><strong>{items.length}</strong></div>
      <div className="category-stat"><span>Transactions in selection</span><strong>{total.toLocaleString()}</strong></div>
      <div className="category-stat"><span>Viewing</span><strong className="category-scope-label">{bankScope === 'all' ? 'All imported banks' : bankScope.split(',').join(' + ')}</strong></div>
    </div>
    <div className="content-card category-management-card">
      <div className="category-manager-bar">
        <div><h2>Your categories</h2><p>Counts follow your sidebar bank selection. Changes to categories and rules apply to all banks.</p></div>
        <div className="category-search"><Search size={17}/><input aria-label="Search categories" placeholder="Find a category…" value={search} onChange={event => setSearch(event.target.value)}/></div>
      </div>
      <form className="category-create-form" onSubmit={event => { event.preventDefault(); if (newName.trim()) run('create'); }}>
        <FolderPlus size={19}/><input aria-label="New category name" placeholder="New category name" maxLength={60} value={newName} onChange={event => setNewName(event.target.value)} />
        <button className="primary-button" disabled={!newName.trim() || saving} type="submit">Add category</button>
      </form>
      {notice && <div className="category-feedback success" role="status"><CheckCircle2 size={17}/>{notice}</div>}
      {error && <div className="category-feedback error" role="alert"><AlertCircle size={17}/>{error}</div>}
      {loading ? <p className="category-manager-empty">Loading categories…</p> : <div className="category-records">
        {visible.map(item => <div className="category-record" key={item.name}>
          <div className="category-icon"><Layers3 size={18}/></div>
          <div className="category-record-details"><strong>{item.name}</strong>{item.protected && <small>Core category</small>}</div>
          <div className="category-count"><strong>{item.transactionCount.toLocaleString()}</strong><span>transactions</span>{bankScope !== 'all' && <small>{item.totalTransactionCount.toLocaleString()} across all banks</small>}</div>
          <div className="category-actions">
            <button title={`Rename ${item.name}`} aria-label={`Rename ${item.name}`} disabled={item.protected || saving} onClick={() => open('rename', item)}><Pencil size={17}/></button>
            <button title={`Merge ${item.name}`} aria-label={`Merge ${item.name}`} disabled={item.protected || saving} onClick={() => open('merge', item)}><Layers3 size={17}/></button>
            <button className="delete" title={`Delete ${item.name}`} aria-label={`Delete ${item.name}`} disabled={item.protected || saving} onClick={() => open('delete', item)}><Trash2 size={17}/></button>
          </div>
        </div>)}
        {!visible.length && <p className="category-manager-empty">No categories match your search.</p>}
      </div>}
    </div>
    {action && <CategoryActionDialog
      action={action}
      items={items}
      selected={selected}
      target={target}
      setTarget={setTarget}
      saving={saving}
      error={error}
      onClose={() => setAction(null)}
      onRun={run}
    />}
  </div>;
}
