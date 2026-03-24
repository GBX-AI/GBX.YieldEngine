import React, { useState, useEffect, useCallback } from 'react';
import {
  getHoldings, importManual, importCsv, importFromKite,
  deleteHolding, savePortfolio, getPortfolios, loadPortfolio, getStatus,
} from '../api';
import { Upload, Plus, Save, Trash2, Download, RefreshCw } from 'lucide-react';

/* ─── Design tokens ─── */
const C = {
  bg: '#0a0f1a',
  card: 'rgba(15,23,42,0.7)',
  border: 'rgba(148,163,184,0.1)',
  text: '#e2e8f0',
  muted: '#94a3b8',
  emerald: '#6ee7b7',
  red: '#f87171',
  amber: '#fcd34d',
  blue: '#38bdf8',
  purple: '#a78bfa',
};

const font = { mono: "'IBM Plex Mono', monospace", sans: "'DM Sans', sans-serif" };

const STATUS_COLORS = {
  WRITE_READY: C.emerald,
  PARTIAL: C.amber,
  COLLATERAL: C.blue,
  CASH_EQUIV: C.muted,
};

/* ─── Reusable inline styles ─── */
const cardStyle = {
  background: C.card,
  border: `1px solid ${C.border}`,
  borderRadius: 16,
  padding: 24,
};

const btnBase = {
  border: 'none',
  borderRadius: 10,
  padding: '10px 20px',
  fontFamily: font.sans,
  fontSize: 14,
  fontWeight: 600,
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 8,
  transition: 'opacity 0.15s',
};

const inputStyle = {
  background: 'rgba(15,23,42,0.9)',
  border: `1px solid ${C.border}`,
  borderRadius: 10,
  padding: '10px 14px',
  color: C.text,
  fontFamily: font.mono,
  fontSize: 14,
  outline: 'none',
  width: '100%',
};

/* ─── Formatting helpers ─── */
const fmt = (n) => {
  if (n == null) return '—';
  return Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 });
};

const fmtCur = (n) => {
  if (n == null) return '—';
  return '₹' + fmt(n);
};

const pnlColor = (v) => (v >= 0 ? C.emerald : C.red);

export default function Holdings() {
  /* ─── State ─── */
  const [holdings, setHoldings] = useState([]);
  const [stats, setStats] = useState({});
  const [cashBalance, setCashBalance] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [kiteConnected, setKiteConnected] = useState(false);

  // Import mode
  const [importMode, setImportMode] = useState(null); // 'manual' | 'csv' | 'saved' | null
  const [manualForm, setManualForm] = useState({ symbol: '', qty: '', avg: '', ltp: '' });
  const [csvFile, setCsvFile] = useState(null);
  const [portfolios, setPortfolios] = useState([]);
  const [snapshotName, setSnapshotName] = useState('');
  const [busy, setBusy] = useState(false);

  /* ─── Data fetching ─── */
  const fetchHoldings = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getHoldings();
      setHoldings(data.holdings || []);
      setStats(data.stats || {});
      if (data.cash_balance != null) setCashBalance(String(data.cash_balance));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHoldings();
    getStatus().then((s) => setKiteConnected(!!s?.kite_connected)).catch(() => {});
  }, [fetchHoldings]);

  /* ─── Handlers ─── */
  const handleImportKite = async () => {
    setBusy(true);
    try {
      await importFromKite();
      await fetchHoldings();
      setImportMode(null);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const handleManualAdd = async () => {
    const { symbol, qty, avg, ltp } = manualForm;
    if (!symbol || !qty || !avg) return;
    setBusy(true);
    try {
      await importManual({
        symbol: symbol.toUpperCase(),
        quantity: Number(qty),
        average_price: Number(avg),
        ltp: ltp ? Number(ltp) : undefined,
      });
      setManualForm({ symbol: '', qty: '', avg: '', ltp: '' });
      await fetchHoldings();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const handleCsvUpload = async () => {
    if (!csvFile) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('file', csvFile);
      await importCsv(fd);
      setCsvFile(null);
      setImportMode(null);
      await fetchHoldings();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const handleDelete = async (symbol) => {
    setBusy(true);
    try {
      await deleteHolding(symbol);
      setHoldings((h) => h.filter((x) => x.symbol !== symbol));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const handleSave = async () => {
    if (!snapshotName.trim()) return;
    setBusy(true);
    try {
      await savePortfolio(snapshotName.trim());
      setSnapshotName('');
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const handleLoadSaved = async () => {
    setImportMode('saved');
    try {
      const data = await getPortfolios();
      setPortfolios(data.portfolios || data || []);
    } catch (e) { setError(e.message); }
  };

  const handleLoadPortfolio = async (id) => {
    setBusy(true);
    try {
      await loadPortfolio(id);
      await fetchHoldings();
      setImportMode(null);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  /* ─── Stat cards data ─── */
  const statCards = [
    { label: 'Portfolio Value', value: fmtCur(stats.total_value), color: C.text },
    { label: 'Unrealized P&L', value: fmtCur(stats.unrealized_pnl), color: stats.unrealized_pnl >= 0 ? C.emerald : C.red },
    { label: 'Non-Cash Collateral', value: fmtCur(stats.non_cash_collateral), color: C.purple },
    { label: 'Cash Equivalent', value: fmtCur(stats.cash_equivalent), color: C.blue },
    { label: 'Usable Margin', value: fmtCur(stats.usable_margin), color: C.amber },
  ];

  /* ─── Render ─── */
  return (
    <div style={{ minHeight: '100vh', background: C.bg, color: C.text, fontFamily: font.sans, padding: '32px 24px' }}>
      <div style={{ maxWidth: 1280, margin: '0 auto' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 28 }}>
          <h1 style={{ fontSize: 28, fontWeight: 700, margin: 0 }}>Holdings</h1>
          <button
            onClick={fetchHoldings}
            style={{ ...btnBase, background: 'rgba(148,163,184,0.1)', color: C.muted }}
          >
            <RefreshCw size={16} /> Refresh
          </button>
        </div>

        {/* Error banner */}
        {error && (
          <div style={{ ...cardStyle, borderColor: C.red, marginBottom: 20, padding: 16, color: C.red, fontSize: 14 }}>
            {error}
            <span onClick={() => setError(null)} style={{ float: 'right', cursor: 'pointer', fontWeight: 700 }}>✕</span>
          </div>
        )}

        {/* Stats bar */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 28 }}>
          {statCards.map((s) => (
            <div key={s.label} style={cardStyle}>
              <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>{s.label}</div>
              <div style={{ fontSize: 22, fontWeight: 700, fontFamily: font.mono, color: s.color }}>{s.value}</div>
            </div>
          ))}
        </div>

        {/* Cash balance */}
        <div style={{ ...cardStyle, marginBottom: 28, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <label style={{ fontSize: 14, fontWeight: 600, whiteSpace: 'nowrap' }}>Cash Balance (₹)</label>
          <input
            type="number"
            value={cashBalance}
            onChange={(e) => setCashBalance(e.target.value)}
            placeholder="e.g. 500000"
            style={{ ...inputStyle, maxWidth: 240 }}
          />
          <span style={{ fontSize: 12, color: C.muted }}>Used by Scanner for margin calculations</span>
        </div>

        {/* Import section */}
        <div style={{ ...cardStyle, marginBottom: 28 }}>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Import Holdings</div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: importMode ? 20 : 0 }}>
            {kiteConnected && (
              <button onClick={handleImportKite} disabled={busy} style={{ ...btnBase, background: C.emerald, color: '#0a0f1a' }}>
                <Download size={16} /> From Kite
              </button>
            )}
            <button
              onClick={() => setImportMode(importMode === 'csv' ? null : 'csv')}
              style={{ ...btnBase, background: importMode === 'csv' ? C.blue : 'rgba(56,189,248,0.15)', color: importMode === 'csv' ? '#0a0f1a' : C.blue }}
            >
              <Upload size={16} /> Upload CSV
            </button>
            <button
              onClick={() => setImportMode(importMode === 'manual' ? null : 'manual')}
              style={{ ...btnBase, background: importMode === 'manual' ? C.purple : 'rgba(167,139,250,0.15)', color: importMode === 'manual' ? '#0a0f1a' : C.purple }}
            >
              <Plus size={16} /> Manual Entry
            </button>
            <button
              onClick={() => importMode === 'saved' ? setImportMode(null) : handleLoadSaved()}
              style={{ ...btnBase, background: importMode === 'saved' ? C.amber : 'rgba(252,211,77,0.15)', color: importMode === 'saved' ? '#0a0f1a' : C.amber }}
            >
              <RefreshCw size={16} /> Load Saved
            </button>
          </div>

          {/* Manual entry form */}
          {importMode === 'manual' && (
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              {[
                { key: 'symbol', label: 'Symbol', placeholder: 'RELIANCE' },
                { key: 'qty', label: 'Qty', placeholder: '100', type: 'number' },
                { key: 'avg', label: 'Avg Price', placeholder: '2450.50', type: 'number' },
                { key: 'ltp', label: 'LTP', placeholder: '2500.00', type: 'number' },
              ].map((f) => (
                <div key={f.key} style={{ flex: f.key === 'symbol' ? 2 : 1, minWidth: 120 }}>
                  <label style={{ fontSize: 12, color: C.muted, display: 'block', marginBottom: 4 }}>{f.label}</label>
                  <input
                    type={f.type || 'text'}
                    placeholder={f.placeholder}
                    value={manualForm[f.key]}
                    onChange={(e) => setManualForm((p) => ({ ...p, [f.key]: e.target.value }))}
                    style={inputStyle}
                  />
                </div>
              ))}
              <button onClick={handleManualAdd} disabled={busy} style={{ ...btnBase, background: C.emerald, color: '#0a0f1a', height: 42 }}>
                <Plus size={16} /> Add
              </button>
            </div>
          )}

          {/* CSV upload */}
          {importMode === 'csv' && (
            <div>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
                <input
                  type="file"
                  accept=".csv"
                  onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
                  style={{ ...inputStyle, maxWidth: 360, padding: '8px 14px' }}
                />
                <button onClick={handleCsvUpload} disabled={busy || !csvFile} style={{ ...btnBase, background: C.blue, color: '#0a0f1a' }}>
                  <Upload size={16} /> Upload
                </button>
              </div>
              <div style={{ fontSize: 12, color: C.muted, lineHeight: 1.6 }}>
                CSV format: <span style={{ fontFamily: font.mono, color: C.text }}>symbol, quantity, average_price, ltp</span> — header row optional.
              </div>
            </div>
          )}

          {/* Saved portfolios */}
          {importMode === 'saved' && (
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              {portfolios.length === 0 && <span style={{ fontSize: 14, color: C.muted }}>No saved portfolios found.</span>}
              {portfolios.map((p) => (
                <button
                  key={p.id}
                  onClick={() => handleLoadPortfolio(p.id)}
                  disabled={busy}
                  style={{ ...btnBase, background: 'rgba(148,163,184,0.1)', color: C.text }}
                >
                  {p.name || `Portfolio #${p.id}`}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Holdings table */}
        <div style={{ ...cardStyle, padding: 0, overflow: 'hidden', marginBottom: 28 }}>
          <div style={{ padding: '20px 24px 12px', fontSize: 16, fontWeight: 600 }}>
            Holdings{' '}
            <span style={{ fontSize: 13, color: C.muted, fontWeight: 400 }}>({holdings.length})</span>
          </div>

          {loading ? (
            <div style={{ padding: 48, textAlign: 'center', color: C.muted }}>Loading holdings...</div>
          ) : holdings.length === 0 ? (
            <div style={{ padding: 48, textAlign: 'center', color: C.muted }}>
              No holdings yet. Import from Kite, upload a CSV, or add manually.
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: font.sans, fontSize: 14 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                    {['Symbol', 'Qty', 'Avg', 'LTP', 'Value', 'P&L', 'Haircut%', 'Collateral', 'Status', ''].map((h) => (
                      <th
                        key={h}
                        style={{
                          padding: '12px 16px',
                          textAlign: h === 'Symbol' || h === 'Status' ? 'left' : 'right',
                          fontSize: 11,
                          fontWeight: 600,
                          color: C.muted,
                          textTransform: 'uppercase',
                          letterSpacing: 0.5,
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {holdings.map((h) => {
                    const pnl = h.pnl ?? ((h.ltp - h.average_price) * h.quantity);
                    const value = h.value ?? (h.ltp * h.quantity);
                    const statusKey = h.status || 'COLLATERAL';
                    return (
                      <tr
                        key={h.symbol}
                        style={{ borderBottom: `1px solid ${C.border}`, transition: 'background 0.15s' }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(148,163,184,0.04)')}
                        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                      >
                        <td style={{ padding: '14px 16px', fontWeight: 600 }}>{h.symbol}</td>
                        <td style={{ padding: '14px 16px', textAlign: 'right', fontFamily: font.mono }}>{fmt(h.quantity)}</td>
                        <td style={{ padding: '14px 16px', textAlign: 'right', fontFamily: font.mono }}>{fmtCur(h.average_price)}</td>
                        <td style={{ padding: '14px 16px', textAlign: 'right', fontFamily: font.mono }}>{fmtCur(h.ltp)}</td>
                        <td style={{ padding: '14px 16px', textAlign: 'right', fontFamily: font.mono }}>{fmtCur(value)}</td>
                        <td style={{ padding: '14px 16px', textAlign: 'right', fontFamily: font.mono, color: pnlColor(pnl) }}>
                          {pnl >= 0 ? '+' : ''}{fmtCur(pnl)}
                        </td>
                        <td style={{ padding: '14px 16px', textAlign: 'right', fontFamily: font.mono }}>{h.haircut != null ? `${h.haircut}%` : '—'}</td>
                        <td style={{ padding: '14px 16px', textAlign: 'right', fontFamily: font.mono }}>{fmtCur(h.collateral_value)}</td>
                        <td style={{ padding: '14px 16px' }}>
                          <span
                            style={{
                              display: 'inline-block',
                              padding: '4px 10px',
                              borderRadius: 999,
                              fontSize: 11,
                              fontWeight: 700,
                              letterSpacing: 0.3,
                              background: `${STATUS_COLORS[statusKey]}20`,
                              color: STATUS_COLORS[statusKey],
                            }}
                          >
                            {statusKey.replace('_', ' ')}
                          </span>
                        </td>
                        <td style={{ padding: '14px 16px', textAlign: 'right' }}>
                          <button
                            onClick={() => handleDelete(h.symbol)}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: C.muted, padding: 4, borderRadius: 6, transition: 'color 0.15s' }}
                            onMouseEnter={(e) => (e.currentTarget.style.color = C.red)}
                            onMouseLeave={(e) => (e.currentTarget.style.color = C.muted)}
                            title="Delete holding"
                          >
                            <Trash2 size={16} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Save portfolio */}
        <div style={{ ...cardStyle, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <Save size={18} style={{ color: C.muted }} />
          <span style={{ fontSize: 14, fontWeight: 600 }}>Save Portfolio Snapshot</span>
          <input
            type="text"
            value={snapshotName}
            onChange={(e) => setSnapshotName(e.target.value)}
            placeholder="Snapshot name"
            style={{ ...inputStyle, maxWidth: 280, fontFamily: font.sans }}
          />
          <button onClick={handleSave} disabled={busy || !snapshotName.trim()} style={{ ...btnBase, background: C.emerald, color: '#0a0f1a' }}>
            <Save size={16} /> Save
          </button>
        </div>
      </div>
    </div>
  );
}
