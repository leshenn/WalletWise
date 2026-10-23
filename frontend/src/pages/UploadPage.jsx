import {
  AlertTriangle,
  CalendarDays,
  Check,
  FileText,
  Files,
  LoaderCircle,
  Plus,
  ShieldCheck,
  Trash2,
  UploadCloud,
} from 'lucide-react';
import { useRef, useState } from 'react';
import { api } from '../services/api';

function formatRange(start, end) {
  if (!start && !end) return 'Date range not available';
  const format = (value) => value
    ? new Date(`${value}T00:00:00`).toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' })
    : '—';
  return `${format(start)} – ${format(end)}`;
}

export default function UploadPage({
  onProcessed,
  setPage,
  categories,
  setCategories,
  statements = [],
  coverage = { months: [], missingMonths: [] },
  refreshTransactions,
}) {
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [newCategory, setNewCategory] = useState('');
  const [addingCategory, setAddingCategory] = useState(false);
  const [deletingId, setDeletingId] = useState('');

  const acceptFile = (candidate) => {
    if (!candidate) return;
    if (candidate.type !== 'application/pdf' && !candidate.name.toLowerCase().endsWith('.pdf')) {
      setError('Please choose a PDF bank statement.');
      return;
    }
    setError('');
    setResult(null);
    setFile(candidate);
  };

  const addCategory = async () => {
    const name = newCategory.trim();
    if (!name) return;
    setAddingCategory(true);
    setError('');
    try {
      const created = await api.createCategory(name);
      setCategories((current) => Array.from(new Set([...current, created.name])).sort((a, b) => {
        if (a === 'Uncategorised') return 1;
        if (b === 'Uncategorised') return -1;
        return a.localeCompare(b);
      }));
      setNewCategory('');
    } catch (err) {
      setError(err.message || 'Could not add category.');
    } finally {
      setAddingCategory(false);
    }
  };

  const processFile = async () => {
    if (!file) return;
    setProcessing(true);
    setError('');
    try {
      const response = await api.processStatement(file);
      setResult(response);
      setFile(null);
      if (inputRef.current) inputRef.current.value = '';
      onProcessed?.(response);
    } catch (err) {
      setError(err.message || 'The statement could not be processed.');
    } finally {
      setProcessing(false);
    }
  };

  const deleteStatement = async (statement) => {
    const ok = window.confirm(`Remove ${statement.filename} and any transactions that only came from this statement?`);
    if (!ok) return;
    setDeletingId(statement.statementId);
    setError('');
    try {
      await api.deleteStatement(statement.statementId);
      await refreshTransactions?.();
    } catch (err) {
      setError(err.message || 'Could not remove this statement.');
    } finally {
      setDeletingId('');
    }
  };

  return (
    <div className="page narrow-page statements-page">
      <div className="desktop-page-title">
        <span className="eyebrow">Statement history</span>
        <h1>Bank statements</h1>
        <p>Each new statement is appended to your history. WalletWise keeps one canonical copy of overlapping transactions and lets you remove a statement later.</p>
      </div>

      <section className="content-card statement-summary-card">
        <div className="section-heading">
          <div><span className="eyebrow">Your data</span><h2>{statements.length} imported statement{statements.length === 1 ? '' : 's'}</h2></div>
          <Files size={20} />
        </div>
        {statements.length ? (
          <div className="statement-list">
            {statements.map((statement) => (
              <article className="statement-row-card" key={statement.statementId}>
                <span className="statement-file-icon"><FileText size={18} /></span>
                <div className="statement-copy">
                  <strong>{statement.filename}</strong>
                  <span>{statement.bank} · {formatRange(statement.periodStart, statement.periodEnd)}{statement.periodVerified ? ' · Verified PDF period' : ' · Legacy date range (unverified)'}</span>
                  <small>
                    {statement.importedCount ?? statement.transactionCount} new transaction{(statement.importedCount ?? statement.transactionCount) === 1 ? '' : 's'}
                    {statement.duplicateCount ? ` · ${statement.duplicateCount} overlapping transaction${statement.duplicateCount === 1 ? '' : 's'} linked` : ''}
                  </small>
                </div>
                <button
                  className="icon-button danger-icon-button"
                  title="Remove statement"
                  disabled={deletingId === statement.statementId}
                  onClick={() => deleteStatement(statement)}
                >
                  {deletingId === statement.statementId ? <LoaderCircle className="spin" size={17} /> : <Trash2 size={17} />}
                </button>
              </article>
            ))}
          </div>
        ) : (
          <p className="muted-copy">No bank statements have been imported yet.</p>
        )}
      </section>

      {coverage.months?.length > 0 && (
        <section className="content-card coverage-card">
          <div className="section-heading">
            <div><span className="eyebrow">Data coverage</span><h2>Which months can WalletWise see?</h2></div>
            <CalendarDays size={20} />
          </div>
          <div className="coverage-month-grid">
            {coverage.months.map((month) => (
              <div className={`coverage-month coverage-${month.status}`} key={month.month}>
                <strong>{month.label}</strong>
                <span>{month.status === 'covered' ? 'Covered' : month.status === 'partial' ? 'Partial' : 'Missing'}</span>
                <small>{month.coveragePct.toFixed(0)}% estimated coverage</small>
              </div>
            ))}
          </div>
          {coverage.missingMonths?.length > 0 ? (
            <div className="coverage-warning">
              <AlertTriangle size={17} />
              <div><strong>Missing or partial months</strong><span>{coverage.missingMonths.map((item) => item.label).join(', ')}</span></div>
            </div>
          ) : (
            <div className="coverage-ok"><Check size={17} /> All months in the imported date range look covered.</div>
          )}
          <p className="coverage-note">{coverage.note}</p>
        </section>
      )}

      <section className="content-card upload-card">
        <div className="section-heading">
          <div><span className="eyebrow">Add to history</span><h2>Import another statement</h2></div>
          <UploadCloud size={20} />
        </div>
        <div
          className={`drop-zone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
          onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            acceptFile(event.dataTransfer.files?.[0]);
          }}
          onClick={() => inputRef.current?.click()}
        >
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            onChange={(event) => acceptFile(event.target.files?.[0])}
          />
          <div className="drop-icon">{file ? <FileText size={27} /> : <UploadCloud size={27} />}</div>
          {file ? (
            <>
              <strong>{file.name}</strong>
              <span>{(file.size / 1024 / 1024).toFixed(2)} MB · PDF</span>
              <small>Tap to choose a different file</small>
            </>
          ) : (
            <>
              <strong>Select a statement</strong>
              <span>It will be added to your existing WalletWise history</span>
              <small>FNB or Capitec PDF statements · max 20 MB</small>
            </>
          )}
        </div>

        <div className="upload-category-panel">
          <div>
            <span className="eyebrow">Your categories</span>
            <h3>Add categories before the import</h3>
            <p>Categories are shared across every statement and saved locally in SQLite.</p>
          </div>
          <div className="category-add-row">
            <input
              value={newCategory}
              onChange={(event) => setNewCategory(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  addCategory();
                }
              }}
              placeholder="e.g. Education"
            />
            <button className="secondary-button" disabled={addingCategory || !newCategory.trim()} onClick={addCategory}>
              <Plus size={16} /> {addingCategory ? 'Adding…' : 'Add'}
            </button>
          </div>
          <div className="category-chip-list">
            {categories.filter((category) => category !== 'Uncategorised').map((category) => (
              <span className="category-pill" key={category}>{category}</span>
            ))}
          </div>
        </div>

        {error && <div className="inline-alert error">{error}</div>}

        <button className="primary-button full-button" disabled={!file || processing} onClick={processFile}>
          {processing
            ? <><LoaderCircle className="spin" size={18} /> Reading statement…</>
            : <><UploadCloud size={18} /> Add statement</>}
        </button>

        {result && (
          <div className="success-panel">
            <span className="success-icon"><Check size={18} /></span>
            <div>
              <strong>{result.importedCount} new transactions added</strong>
              {result.periodDetection?.verified && (
                <p>Statement period verified: {formatRange(result.periodDetection.start, result.periodDetection.end)} · {result.periodDetection.source}</p>
              )}
              <p>
                {result.bank} · {result.filename}
                {result.duplicateCount ? ` · ${result.duplicateCount} overlap${result.duplicateCount === 1 ? '' : 's'} safely linked` : ''}
                {result.unassignedCount ? ` · ${result.unassignedCount} unassigned` : ''}
              </p>
            </div>
            <button className="text-button" onClick={() => setPage('review')}>Review results</button>
          </div>
        )}
      </section>

      <section className="pipeline-card">
        <div className="section-heading"><div><span className="eyebrow">Live backend</span><h2>How statement history works</h2></div></div>
        <div className="pipeline-steps">
          {[
            ['1', 'Detect bank', 'Choose the matching bank adapter.'],
            ['2', 'Verify period & extract', 'Read FNB header dates, resolve transaction years and recover visual-only descriptions.'],
            ['3', 'Normalise', 'Convert bank fields into the common WalletWise schema.'],
            ['4', 'De-duplicate', 'Link overlaps instead of counting the same transaction twice.'],
            ['5', 'Append & report', 'Keep earlier months and extend your financial history.'],
          ].map(([number, title, body]) => (
            <div key={number}><b>{number}</b><span><strong>{title}</strong><small>{body}</small></span></div>
          ))}
        </div>
        <div className="privacy-note"><ShieldCheck size={18} /><span><strong>Local MVP:</strong> PDFs are deleted after processing. Structured transactions, statement history and rules stay in local SQLite.</span></div>
      </section>
    </div>
  );
}
