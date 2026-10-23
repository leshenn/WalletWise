import {
  BarChart3, Home, ListChecks, MessageCircle, Menu, ReceiptText,
  PiggyBank, Settings2, Upload, X, Tags, Landmark, ChevronDown, Check,
} from 'lucide-react';
import { useState } from 'react';
import { playUiSound } from '../utils/sounds';

const navItems = [
  { id: 'dashboard', label: 'Dashboard', icon: Home },
  { id: 'upload', label: 'Statements', icon: Upload },
  { id: 'transactions', label: 'Transactions', icon: ReceiptText },
  { id: 'review', label: 'Review', icon: ListChecks },
  { id: 'categories', label: 'Categories', icon: Tags },
  { id: 'rules', label: 'Naming rules', icon: Settings2 },
  { id: 'reports', label: 'Reports', icon: BarChart3 },
  { id: 'budget', label: 'Budget', icon: PiggyBank },
  { id: 'ask', label: 'Wally', icon: MessageCircle },
];

export function labelForScope(scope, availableBanks = []) {
  if (!scope || scope === 'all') return 'All banks';
  const selected = scope.split(',').filter(Boolean);
  if (selected.length === 1) return selected[0];
  return `${selected.length} banks selected`;
}

function BankPicker({ selectedBank, onBankChange, availableBanks }) {
  const [open, setOpen] = useState(false);
  const selected = selectedBank === 'all' ? [] : selectedBank.split(',').filter(Boolean);
  const choose = (bank) => {
    const next = selectedBank === 'all' ? [bank] : selected.includes(bank)
      ? selected.filter((item) => item !== bank) : [...selected, bank];
    onBankChange?.(next.length ? [...new Set(next)].sort((a, b) => a.localeCompare(b)).join(',') : 'all');
  };
  return (
    <div className="sidebar-bank-picker">
      <div className="sidebar-bank-label"><Landmark size={15} /><span>Viewing banks</span></div>
      <button type="button" className="sidebar-bank-trigger" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span>{labelForScope(selectedBank, availableBanks)}</span><ChevronDown size={16} className={open ? 'open' : ''} />
      </button>
      {open && <div className="sidebar-bank-options">
        <label className="bank-checkbox-row">
          <input type="checkbox" checked={selectedBank === 'all'} onChange={() => onBankChange?.('all')} />
          <span>All imported banks</span>{selectedBank === 'all' && <Check size={14} />}
        </label>
        {availableBanks.map(bank => <label className="bank-checkbox-row" key={bank}>
          <input type="checkbox" checked={selectedBank === 'all' || selected.includes(bank)} onChange={() => choose(bank)} />
          <span>{bank}</span>
        </label>)}
        {!availableBanks.length && <p className="bank-picker-empty">Import an FNB or Capitec statement to see it here.</p>}
        <p className="bank-picker-hint">Choose any combination. Selecting one bank from All banks starts a custom selection.</p>
      </div>}
      {!open && <small>{selectedBank === 'all' ? 'Combined reporting and Wally' : 'Only selected bank data'}</small>}
    </div>
  );
}

export default function Layout({ page, setPage, reviewCount, selectedBank = 'all', onBankChange, availableBanks = [], children }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const active = navItems.find(item => item.id === page) || navItems[0];
  const navigate = id => {
    if (id !== page) playUiSound('navigate');
    setPage(id);
    setDrawerOpen(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  return <div className="app-shell">
    <aside className="sidebar">
      <button className="brand" onClick={() => navigate('dashboard')}>
        <span className="brand-mark"><img src="/walletwise-logo.png" alt="" /></span>
        <span><strong>WalletWise</strong><small>Money, made clearer.</small></span>
      </button>
      <nav className="side-nav">
        {navItems.map(({id,label,icon:Icon}) => <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}>
          <Icon size={19}/><span>{label}</span>{id === 'review' && reviewCount > 0 && <em>{reviewCount}</em>}
        </button>)}
      </nav>
      <BankPicker selectedBank={selectedBank} onBankChange={onBankChange} availableBanks={availableBanks} />
    </aside>
    <header className="mobile-header">
      <button className="brand mobile-brand" onClick={() => navigate('dashboard')}>
        <span className="brand-mark"><img src="/walletwise-logo.png" alt="" /></span><strong>WalletWise</strong>
      </button>
      <button className="icon-button" aria-label="Open menu" onClick={() => setDrawerOpen(true)}><Menu size={21} /></button>
    </header>
    {drawerOpen && <div className="drawer-backdrop" onClick={() => setDrawerOpen(false)}>
      <div className="mobile-drawer" onClick={(event) => event.stopPropagation()}>
        <div className="drawer-heading"><span>Navigate</span><button className="icon-button" onClick={() => setDrawerOpen(false)}><X size={20}/></button></div>
        {navItems.map(({id,label,icon:Icon}) => <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}>
          <Icon size={19}/>{label}{id === 'review' && reviewCount > 0 && <em>{reviewCount}</em>}
        </button>)}
        <BankPicker selectedBank={selectedBank} onBankChange={onBankChange} availableBanks={availableBanks}/>
      </div>
    </div>}
    <main className="main-content">
      <div className="page-heading mobile-only-heading"><p>WalletWise</p><h1>{active.label}</h1></div>
      <div key={page} className="page-transition-shell">{children}</div>
    </main>
  </div>;
}
