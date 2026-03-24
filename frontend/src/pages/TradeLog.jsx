import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { getTrades, getTradeDetail } from '../api';

const colors = {
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

const fontMono = "'IBM Plex Mono', monospace";
const fontSans = "'DM Sans', sans-serif";

function plColor(val) {
  if (val > 0) return colors.emerald;
  if (val < 0) return colors.red;
  return colors.text;
}

function fmt(n, decimals = 2) {
  if (n == null) return '—';
  const sign = n >= 0 ? '+' : '';
  return sign + Number(n).toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function exitReasonMeta(reason) {
  switch (reason) {
    case 'Expiry':
      return { color: colors.muted, bg: 'rgba(148,163,184,0.1)' };
    case 'Stop-Loss':
      return { color: colors.red, bg: 'rgba(248,113,113,0.12)' };
    case 'Manual':
      return { color: colors.blue, bg: 'rgba(56,189,248,0.12)' };
    case 'Rolled':
      return { color: colors.purple, bg: 'rgba(167,139,250,0.12)' };
    default:
      return { color: colors.muted, bg: 'rgba(148,163,184,0.08)' };
  }
}

// ── Styles ──

const s = {
  page: {
    minHeight: '100vh',
    backgroundColor: colors.bg,
    color: colors.text,
    fontFamily: fontSans,
    padding: '24px 32px',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 24,
  },
  title: {
    fontSize: 24,
    fontWeight: 700,
  },
  filtersRow: {
    display: 'flex',
    gap: 12,
    marginBottom: 20,
    flexWrap: 'wrap',
    alignItems: 'flex-end',
  },
  filterGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  filterLabel: {
    fontSize: 11,
    color: colors.muted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  input: {
    backgroundColor: 'rgba(15,23,42,0.8)',
    border: `1px solid ${colors.border}`,
    borderRadius: 6,
    padding: '8px 12px',
    color: colors.text,
    fontSize: 13,
    fontFamily: fontSans,
    outline: 'none',
    minWidth: 130,
  },
  select: {
    backgroundColor: 'rgba(15,23,42,0.8)',
    border: `1px solid ${colors.border}`,
    borderRadius: 6,
    padding: '8px 12px',
    color: colors.text,
    fontSize: 13,
    fontFamily: fontSans,
    outline: 'none',
    cursor: 'pointer',
    minWidth: 120,
  },
  exportBtn: {
    padding: '8px 18px',
    borderRadius: 8,
    fontSize: 13,
    fontWeight: 600,
    fontFamily: fontSans,
    cursor: 'pointer',
    border: 'none',
    backgroundColor: 'rgba(56,189,248,0.12)',
    color: colors.blue,
    whiteSpace: 'nowrap',
  },
  tableWrap: {
    overflowX: 'auto',
    backgroundColor: colors.card,
    border: `1px solid ${colors.border}`,
    borderRadius: 12,
    marginBottom: 24,
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 13,
  },
  th: (sortable) => ({
    textAlign: 'left',
    padding: '12px 14px',
    fontSize: 11,
    fontWeight: 600,
    color: colors.muted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    borderBottom: `1px solid ${colors.border}`,
    whiteSpace: 'nowrap',
    cursor: sortable ? 'pointer' : 'default',
    userSelect: 'none',
  }),
  td: {
    padding: '12px 14px',
    borderBottom: `1px solid ${colors.border}`,
    whiteSpace: 'nowrap',
    fontFamily: fontSans,
  },
  tdMono: {
    padding: '12px 14px',
    borderBottom: `1px solid ${colors.border}`,
    whiteSpace: 'nowrap',
    fontFamily: fontMono,
    fontSize: 13,
  },
  badge: (meta) => ({
    display: 'inline-block',
    padding: '3px 10px',
    borderRadius: 999,
    fontSize: 11,
    fontWeight: 600,
    color: meta.color,
    backgroundColor: meta.bg,
  }),
  expandRow: {
    backgroundColor: 'rgba(15,23,42,0.4)',
    borderBottom: `1px solid ${colors.border}`,
  },
  expandCell: {
    padding: '16px 20px',
  },
  expandSection: {
    marginBottom: 14,
  },
  expandTitle: {
    fontSize: 12,
    fontWeight: 600,
    color: colors.muted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 8,
  },
  legTable: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 12,
  },
  legTh: {
    textAlign: 'left',
    padding: '6px 10px',
    fontSize: 10,
    fontWeight: 600,
    color: colors.muted,
    textTransform: 'uppercase',
    borderBottom: `1px solid ${colors.border}`,
  },
  legTd: {
    padding: '6px 10px',
    borderBottom: `1px solid ${colors.border}`,
    fontFamily: fontMono,
    fontSize: 12,
    color: colors.muted,
  },
  totalRow: {
    backgroundColor: 'rgba(56,189,248,0.04)',
    fontWeight: 700,
  },
  empty: {
    textAlign: 'center',
    color: colors.muted,
    padding: 64,
  },
  emptyIcon: {
    fontSize: 40,
    marginBottom: 12,
    opacity: 0.3,
  },
  spinner: {
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    minHeight: 300,
    color: colors.muted,
    fontSize: 14,
  },
  sortArrow: {
    display: 'inline-block',
    marginLeft: 4,
    fontSize: 10,
  },
};

const STRATEGIES = ['All', 'Iron Condor', 'Strangle', 'Straddle', 'Bull Put', 'Bear Call', 'Naked Put', 'Naked Call'];
const EXIT_REASONS = ['All', 'Expiry', 'Stop-Loss', 'Manual', 'Rolled'];
const PL_FILTERS = ['All', 'Profit', 'Loss'];

const SORTABLE_COLS = ['date', 'symbol', 'grossPL', 'netPL', 'fees', 'duration'];

function SortArrow({ col, sortCol, sortDir }) {
  if (col !== sortCol) return <span style={{ ...s.sortArrow, opacity: 0.25 }}>&#8597;</span>;
  return <span style={s.sortArrow}>{sortDir === 'asc' ? '&#9650;' : '&#9660;'}</span>;
}

function ExpandedRow({ trade }) {
  const [detail, setDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoadingDetail(true);
    getTradeDetail(trade.id).then((data) => {
      if (!cancelled) {
        setDetail(data);
        setLoadingDetail(false);
      }
    });
    return () => { cancelled = true; };
  }, [trade.id]);

  if (loadingDetail) {
    return (
      <tr style={s.expandRow}>
        <td colSpan={11} style={s.expandCell}>
          <span style={{ color: colors.muted, fontSize: 12 }}>Loading details...</span>
        </td>
      </tr>
    );
  }

  const legs = detail?.legs ?? [];
  const fees = detail?.feeBreakdown ?? {};
  const adjustments = detail?.adjustments ?? [];

  return (
    <tr style={s.expandRow}>
      <td colSpan={11} style={s.expandCell}>
        {/* Leg details */}
        {legs.length > 0 && (
          <div style={s.expandSection}>
            <div style={s.expandTitle}>Leg Details</div>
            <table style={s.legTable}>
              <thead>
                <tr>
                  <th style={s.legTh}>Type</th>
                  <th style={s.legTh}>Strike</th>
                  <th style={s.legTh}>Expiry</th>
                  <th style={s.legTh}>Qty</th>
                  <th style={s.legTh}>Entry</th>
                  <th style={s.legTh}>Exit</th>
                  <th style={s.legTh}>P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {legs.map((leg, i) => (
                  <tr key={i}>
                    <td style={{ ...s.legTd, fontFamily: fontSans }}>{leg.type}</td>
                    <td style={s.legTd}>{leg.strike}</td>
                    <td style={{ ...s.legTd, fontFamily: fontSans }}>{leg.expiry}</td>
                    <td style={s.legTd}>{leg.qty}</td>
                    <td style={s.legTd}>{fmt(leg.entry)}</td>
                    <td style={s.legTd}>{fmt(leg.exit)}</td>
                    <td style={{ ...s.legTd, color: plColor(leg.pl) }}>{fmt(leg.pl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Fee breakdown */}
        <div style={s.expandSection}>
          <div style={s.expandTitle}>Fee Breakdown</div>
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 12 }}>
            {[
              ['Brokerage', fees.brokerage],
              ['STT', fees.stt],
              ['Exchange', fees.exchange],
              ['GST', fees.gst],
              ['Stamp', fees.stamp],
            ].map(([label, val]) => (
              <div key={label}>
                <span style={{ color: colors.muted }}>{label}: </span>
                <span style={{ fontFamily: fontMono, color: colors.text }}>{fmt(val)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Adjustment history */}
        {adjustments.length > 0 && (
          <div style={s.expandSection}>
            <div style={s.expandTitle}>Adjustment History</div>
            {adjustments.map((adj, i) => (
              <div key={i} style={{ fontSize: 12, color: colors.muted, marginBottom: 4 }}>
                <span style={{ color: colors.text }}>{adj.date}</span>
                {' — '}
                {adj.action}
                {adj.cost != null && (
                  <span style={{ fontFamily: fontMono, color: plColor(-adj.cost), marginLeft: 8 }}>
                    {fmt(-adj.cost)}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </td>
    </tr>
  );
}

export default function TradeLog() {
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);

  // Filters
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [strategyFilter, setStrategyFilter] = useState('All');
  const [symbolFilter, setSymbolFilter] = useState('');
  const [plFilter, setPlFilter] = useState('All');
  const [exitFilter, setExitFilter] = useState('All');

  // Sort
  const [sortCol, setSortCol] = useState('date');
  const [sortDir, setSortDir] = useState('desc');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getTrades().then((data) => {
      if (!cancelled) {
        setTrades(data ?? []);
        setLoading(false);
      }
    });
    return () => { cancelled = true; };
  }, []);

  const handleSort = useCallback((col) => {
    if (!SORTABLE_COLS.includes(col)) return;
    setSortCol((prev) => {
      if (prev === col) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
        return col;
      }
      setSortDir('desc');
      return col;
    });
  }, []);

  const filtered = useMemo(() => {
    let result = [...trades];

    if (dateFrom) result = result.filter((t) => t.date >= dateFrom);
    if (dateTo) result = result.filter((t) => t.date <= dateTo);
    if (strategyFilter !== 'All') result = result.filter((t) => t.strategy === strategyFilter);
    if (symbolFilter) {
      const q = symbolFilter.toUpperCase();
      result = result.filter((t) => t.symbol?.toUpperCase().includes(q));
    }
    if (plFilter === 'Profit') result = result.filter((t) => (t.netPL ?? 0) > 0);
    if (plFilter === 'Loss') result = result.filter((t) => (t.netPL ?? 0) < 0);
    if (exitFilter !== 'All') result = result.filter((t) => t.exitReason === exitFilter);

    result.sort((a, b) => {
      let aVal = a[sortCol];
      let bVal = b[sortCol];
      if (typeof aVal === 'string') aVal = aVal.toLowerCase();
      if (typeof bVal === 'string') bVal = bVal.toLowerCase();
      if (aVal < bVal) return sortDir === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });

    return result;
  }, [trades, dateFrom, dateTo, strategyFilter, symbolFilter, plFilter, exitFilter, sortCol, sortDir]);

  const totals = useMemo(() => {
    return filtered.reduce(
      (acc, t) => ({
        grossPL: acc.grossPL + (t.grossPL ?? 0),
        fees: acc.fees + (t.fees ?? 0),
        netPL: acc.netPL + (t.netPL ?? 0),
      }),
      { grossPL: 0, fees: 0, netPL: 0 }
    );
  }, [filtered]);

  const exportCSV = () => {
    const headers = ['Date', 'Symbol', 'Strategy', 'Direction', 'Entry Premium', 'Exit Premium', 'Gross P&L', 'Fees', 'Net P&L', 'Exit Reason', 'Duration'];
    const rows = filtered.map((t) => [
      t.date, t.symbol, t.strategy, t.direction, t.entryPremium, t.exitPremium,
      t.grossPL, t.fees, t.netPL, t.exitReason, t.duration,
    ]);
    const csv = [headers, ...rows].map((r) => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `trade-log-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) {
    return (
      <div style={s.page}>
        <div style={s.spinner}>
          <div>
            <div style={{
              width: 32,
              height: 32,
              border: `3px solid ${colors.border}`,
              borderTopColor: colors.blue,
              borderRadius: '50%',
              animation: 'spin 0.8s linear infinite',
              margin: '0 auto 12px',
            }} />
            <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
            Loading trade history...
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={s.page}>
      <div style={s.header}>
        <h1 style={s.title}>Trade Log</h1>
        <button style={s.exportBtn} onClick={exportCSV}>
          Export CSV
        </button>
      </div>

      {/* Filters */}
      <div style={s.filtersRow}>
        <div style={s.filterGroup}>
          <label style={s.filterLabel}>From</label>
          <input
            type="date"
            style={s.input}
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
          />
        </div>
        <div style={s.filterGroup}>
          <label style={s.filterLabel}>To</label>
          <input
            type="date"
            style={s.input}
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </div>
        <div style={s.filterGroup}>
          <label style={s.filterLabel}>Strategy</label>
          <select style={s.select} value={strategyFilter} onChange={(e) => setStrategyFilter(e.target.value)}>
            {STRATEGIES.map((st) => (
              <option key={st} value={st}>{st}</option>
            ))}
          </select>
        </div>
        <div style={s.filterGroup}>
          <label style={s.filterLabel}>Symbol</label>
          <input
            type="text"
            style={s.input}
            placeholder="e.g. NIFTY"
            value={symbolFilter}
            onChange={(e) => setSymbolFilter(e.target.value)}
          />
        </div>
        <div style={s.filterGroup}>
          <label style={s.filterLabel}>P&amp;L</label>
          <select style={s.select} value={plFilter} onChange={(e) => setPlFilter(e.target.value)}>
            {PL_FILTERS.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>
        <div style={s.filterGroup}>
          <label style={s.filterLabel}>Exit Reason</label>
          <select style={s.select} value={exitFilter} onChange={(e) => setExitFilter(e.target.value)}>
            {EXIT_REASONS.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Table */}
      {filtered.length === 0 ? (
        <div style={{ ...s.tableWrap, ...s.empty }}>
          <div style={s.emptyIcon}>&#128203;</div>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>No trades found</div>
          <div style={{ fontSize: 13, color: colors.muted }}>Adjust your filters or check back later</div>
        </div>
      ) : (
        <div style={s.tableWrap}>
          <table style={s.table}>
            <thead>
              <tr>
                {[
                  { key: 'date', label: 'Date' },
                  { key: 'symbol', label: 'Symbol' },
                  { key: 'strategy', label: 'Strategy', sortable: false },
                  { key: 'direction', label: 'Direction', sortable: false },
                  { key: 'entryPremium', label: 'Entry Premium', sortable: false },
                  { key: 'exitPremium', label: 'Exit Premium', sortable: false },
                  { key: 'grossPL', label: 'Gross P&L' },
                  { key: 'fees', label: 'Fees' },
                  { key: 'netPL', label: 'Net P&L' },
                  { key: 'exitReason', label: 'Exit Reason', sortable: false },
                  { key: 'duration', label: 'Duration' },
                ].map(({ key, label, sortable }) => {
                  const isSortable = sortable !== false && SORTABLE_COLS.includes(key);
                  return (
                    <th
                      key={key}
                      style={s.th(isSortable)}
                      onClick={() => isSortable && handleSort(key)}
                    >
                      {label}
                      {isSortable && (
                        <span
                          style={s.sortArrow}
                          dangerouslySetInnerHTML={{
                            __html:
                              sortCol === key
                                ? sortDir === 'asc' ? '&#9650;' : '&#9660;'
                                : '&#8597;',
                          }}
                        />
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {filtered.map((trade) => {
                const erm = exitReasonMeta(trade.exitReason);
                const isExpanded = expandedId === trade.id;
                return (
                  <React.Fragment key={trade.id}>
                    <tr
                      style={{ cursor: 'pointer' }}
                      onClick={() => setExpandedId(isExpanded ? null : trade.id)}
                    >
                      <td style={s.td}>{trade.date}</td>
                      <td style={{ ...s.td, fontWeight: 600 }}>{trade.symbol}</td>
                      <td style={s.td}>{trade.strategy}</td>
                      <td style={s.td}>{trade.direction}</td>
                      <td style={s.tdMono}>{fmt(trade.entryPremium)}</td>
                      <td style={s.tdMono}>{fmt(trade.exitPremium)}</td>
                      <td style={{ ...s.tdMono, color: plColor(trade.grossPL) }}>{fmt(trade.grossPL)}</td>
                      <td style={{ ...s.tdMono, color: colors.red }}>{fmt(-Math.abs(trade.fees ?? 0))}</td>
                      <td style={{ ...s.tdMono, color: plColor(trade.netPL), fontWeight: 600 }}>{fmt(trade.netPL)}</td>
                      <td style={s.td}>
                        <span style={s.badge(erm)}>{trade.exitReason}</span>
                      </td>
                      <td style={{ ...s.tdMono, color: colors.muted }}>{trade.duration}d</td>
                    </tr>
                    {isExpanded && <ExpandedRow trade={trade} />}
                  </React.Fragment>
                );
              })}

              {/* Running total row */}
              <tr style={s.totalRow}>
                <td colSpan={6} style={{ ...s.td, textAlign: 'right', fontWeight: 700, color: colors.muted }}>
                  TOTAL ({filtered.length} trades)
                </td>
                <td style={{ ...s.tdMono, color: plColor(totals.grossPL), fontWeight: 700 }}>
                  {fmt(totals.grossPL)}
                </td>
                <td style={{ ...s.tdMono, color: colors.red, fontWeight: 700 }}>
                  {fmt(-Math.abs(totals.fees))}
                </td>
                <td style={{ ...s.tdMono, color: plColor(totals.netPL), fontWeight: 700 }}>
                  {fmt(totals.netPL)}
                </td>
                <td colSpan={2} style={s.td} />
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
