import React, { useState, useEffect } from 'react';
import { getArbitrage, scan } from '../api';

const c = {
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

const mono = "'IBM Plex Mono', monospace";
const sans = "'DM Sans', sans-serif";

const TYPE_LABELS = {
  CASH_FUTURES: { label: 'Cash & Futures', color: c.blue },
  PUT_CALL_PARITY: { label: 'Put-Call Parity', color: c.purple },
  CALENDAR_SPREAD: { label: 'Calendar Spread', color: c.amber },
};

const s = {
  page: {
    minHeight: '100vh',
    background: c.bg,
    padding: 24,
    fontFamily: sans,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 24,
    flexWrap: 'wrap',
    gap: 12,
  },
  title: {
    fontSize: 22,
    fontWeight: 700,
    color: c.text,
  },
  controls: {
    display: 'flex',
    gap: 10,
    alignItems: 'center',
    flexWrap: 'wrap',
  },
  select: {
    padding: '8px 14px',
    borderRadius: 10,
    border: `1px solid ${c.border}`,
    background: c.card,
    color: c.text,
    fontSize: 13,
    fontFamily: sans,
    cursor: 'pointer',
    outline: 'none',
  },
  scanBtn: {
    padding: '8px 18px',
    borderRadius: 10,
    border: 'none',
    background: c.emerald,
    color: '#0a0f1a',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: sans,
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))',
    gap: 16,
  },
  card: {
    background: c.card,
    border: `1px solid ${c.border}`,
    borderRadius: 16,
    padding: 20,
    backdropFilter: 'blur(12px)',
    cursor: 'pointer',
    transition: 'border-color 0.15s ease',
  },
  cardTop: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 14,
  },
  badge: {
    display: 'inline-block',
    padding: '3px 10px',
    borderRadius: 20,
    fontSize: 11,
    fontWeight: 600,
    fontFamily: mono,
  },
  symbol: {
    fontSize: 16,
    fontWeight: 700,
    color: c.text,
    marginBottom: 4,
  },
  returnVal: {
    fontSize: 26,
    fontWeight: 700,
    fontFamily: mono,
    color: c.emerald,
    lineHeight: 1.2,
  },
  statsRow: {
    display: 'flex',
    gap: 20,
    marginTop: 12,
    flexWrap: 'wrap',
  },
  stat: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  statLabel: {
    fontSize: 11,
    color: c.muted,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  statValue: {
    fontSize: 14,
    fontWeight: 600,
    fontFamily: mono,
    color: c.text,
  },
  legsSection: {
    marginTop: 14,
    padding: '14px 0 0',
    borderTop: `1px solid ${c.border}`,
  },
  legRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '6px 0',
  },
  legLabel: {
    fontSize: 13,
    color: c.text,
  },
  legDetail: {
    fontSize: 12,
    fontFamily: mono,
    color: c.muted,
  },
  empty: {
    textAlign: 'center',
    padding: 60,
    color: c.muted,
    fontSize: 14,
  },
};

export default function Arbitrage() {
  const [opps, setOpps] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [sortBy, setSortBy] = useState('return');
  const [filterType, setFilterType] = useState('ALL');
  const [expandedId, setExpandedId] = useState(null);

  const fetchOpps = async () => {
    setLoading(true);
    try {
      const data = await getArbitrage();
      setOpps(Array.isArray(data) ? data : data?.opportunities || []);
    } catch {
      setOpps([]);
    }
    setLoading(false);
  };

  useEffect(() => { fetchOpps(); }, []);

  const handleScan = async () => {
    setScanning(true);
    try {
      await scan(0);
      await fetchOpps();
    } catch { /* ignore */ }
    setScanning(false);
  };

  const filtered = opps.filter(o => filterType === 'ALL' || o.type === filterType);

  const sorted = [...filtered].sort((a, b) => {
    if (sortBy === 'return') return (b.annualized_return ?? b.return ?? 0) - (a.annualized_return ?? a.return ?? 0);
    if (sortBy === 'type') return (a.type || '').localeCompare(b.type || '');
    return 0;
  });

  const typeOptions = ['ALL', ...Object.keys(TYPE_LABELS)];

  return (
    <div style={s.page}>
      {/* Header */}
      <div style={s.header}>
        <span style={s.title}>Arbitrage Opportunities</span>
        <div style={s.controls}>
          <select style={s.select} value={filterType} onChange={e => setFilterType(e.target.value)}>
            {typeOptions.map(t => (
              <option key={t} value={t}>{t === 'ALL' ? 'All Types' : TYPE_LABELS[t]?.label || t}</option>
            ))}
          </select>
          <select style={s.select} value={sortBy} onChange={e => setSortBy(e.target.value)}>
            <option value="return">Sort: Return</option>
            <option value="type">Sort: Type</option>
          </select>
          <button style={s.scanBtn} onClick={handleScan} disabled={scanning}>
            {scanning ? 'Scanning...' : 'Scan Now'}
          </button>
        </div>
      </div>

      {loading ? (
        <div style={s.empty}>Loading arbitrage data...</div>
      ) : sorted.length === 0 ? (
        <div style={s.empty}>No arbitrage opportunities found. Try scanning.</div>
      ) : (
        <div style={s.grid}>
          {sorted.map((opp, i) => {
            const typeInfo = TYPE_LABELS[opp.type] || { label: opp.type, color: c.muted };
            const ret = opp.annualized_return ?? opp.return ?? 0;
            const isExpanded = expandedId === (opp.id ?? i);
            const legs = opp.legs || [];

            return (
              <div
                key={opp.id ?? i}
                style={{
                  ...s.card,
                  borderColor: isExpanded ? typeInfo.color : c.border,
                }}
                onClick={() => setExpandedId(isExpanded ? null : (opp.id ?? i))}
              >
                <div style={s.cardTop}>
                  <div>
                    <span style={{
                      ...s.badge,
                      background: `${typeInfo.color}20`,
                      color: typeInfo.color,
                    }}>
                      {typeInfo.label}
                    </span>
                    {opp.risk_free && (
                      <span style={{
                        ...s.badge,
                        background: `${c.emerald}20`,
                        color: c.emerald,
                        marginLeft: 8,
                      }}>
                        Risk-Free
                      </span>
                    )}
                  </div>
                  <div style={s.returnVal}>{ret.toFixed(1)}%</div>
                </div>

                <div style={s.symbol}>{opp.symbol || opp.underlying}</div>

                <div style={s.statsRow}>
                  <div style={s.stat}>
                    <span style={s.statLabel}>Margin</span>
                    <span style={s.statValue}>
                      {opp.margin ? `₹${Number(opp.margin).toLocaleString('en-IN')}` : '—'}
                    </span>
                  </div>
                  <div style={s.stat}>
                    <span style={s.statLabel}>Holding</span>
                    <span style={s.statValue}>{opp.holding_days ?? opp.days ?? '—'} days</span>
                  </div>
                  <div style={s.stat}>
                    <span style={s.statLabel}>Legs</span>
                    <span style={s.statValue}>{legs.length}</span>
                  </div>
                  <div style={s.stat}>
                    <span style={s.statLabel}>Return (Ann.)</span>
                    <span style={{ ...s.statValue, color: c.emerald }}>{ret.toFixed(2)}%</span>
                  </div>
                </div>

                {isExpanded && legs.length > 0 && (
                  <div style={s.legsSection}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: c.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      Leg Details
                    </div>
                    {legs.map((leg, li) => (
                      <div key={li} style={s.legRow}>
                        <div>
                          <span style={{
                            ...s.badge,
                            background: leg.side === 'BUY' || leg.transaction_type === 'BUY'
                              ? `${c.emerald}20`
                              : `${c.red}20`,
                            color: leg.side === 'BUY' || leg.transaction_type === 'BUY'
                              ? c.emerald
                              : c.red,
                            marginRight: 8,
                          }}>
                            {leg.side || leg.transaction_type || '—'}
                          </span>
                          <span style={s.legLabel}>{leg.tradingsymbol || leg.symbol || leg.instrument}</span>
                        </div>
                        <span style={s.legDetail}>
                          Qty: {leg.quantity ?? '—'} @ ₹{leg.price ?? '—'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
