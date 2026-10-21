import {
  BarChart3,
  Home,
  ListChecks,
  MessageCircle,
  Menu,
  ReceiptText,
  PiggyBank,
  Settings2,
  Upload,
  X,
} from 'lucide-react';
import { useState } from 'react';
import { playUiSound } from '../utils/sounds';

const navItems = [
  { id: 'dashboard', label: 'Dashboard', icon: Home },
  { id: 'upload', label: 'Statements', icon: Upload },
  { id: 'transactions', label: 'Transactions', icon: ReceiptText },
  { id: 'review', label: 'Review', icon: ListChecks },
  { id: 'rules', label: 'Naming rules', icon: Settings2 },
  { id: 'reports', label: 'Reports', icon: BarChart3 },
  { id: 'budget', label: 'Budget', icon: PiggyBank },
  { id: 'ask', label: 'Wally', icon: MessageCircle },
];

export default function Layout({ page, setPage, reviewCount, children }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const active = navItems.find((item) => item.id === page) || navItems[0];

  const navigate = (id) => {
    if (id !== page) playUiSound('navigate');
    setPage(id);
    setDrawerOpen(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand" onClick={() => navigate('dashboard')}>
          <span className="brand-mark"><img src="/walletwise-logo.png" alt="" /></span>
          <span><strong>WalletWise</strong><small>Money, made clearer.</small></span>
        </button>
        <nav className="side-nav">
          {navItems.map(({ id, label, icon: Icon }) => (
            <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}>
              <Icon size={19} />
              <span>{label}</span>
              {id === 'review' && reviewCount > 0 && <em>{reviewCount}</em>}
            </button>
          ))}
        </nav>
      </aside>

      <header className="mobile-header">
        <button className="brand mobile-brand" onClick={() => navigate('dashboard')}>
          <span className="brand-mark"><img src="/walletwise-logo.png" alt="" /></span>
          <strong>WalletWise</strong>
        </button>
        <button className="icon-button" aria-label="Open menu" onClick={() => setDrawerOpen(true)}><Menu size={21} /></button>
      </header>

      {drawerOpen && (
        <div className="drawer-backdrop" onClick={() => setDrawerOpen(false)}>
          <div className="mobile-drawer" onClick={(event) => event.stopPropagation()}>
            <div className="drawer-heading">
              <span>Navigate</span>
              <button className="icon-button" onClick={() => setDrawerOpen(false)}><X size={20} /></button>
            </div>
            {navItems.map(({ id, label, icon: Icon }) => (
              <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}>
                <Icon size={19} /> {label}
                {id === 'review' && reviewCount > 0 && <em>{reviewCount}</em>}
              </button>
            ))}
          </div>
        </div>
      )}

      <main className="main-content">
        <div className="page-heading mobile-only-heading">
          <p>WalletWise</p>
          <h1>{active.label}</h1>
        </div>
        <div key={page} className="page-transition-shell">{children}</div>
      </main>

    </div>
  );
}
