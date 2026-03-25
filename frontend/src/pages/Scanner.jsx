import React, { useState, useEffect, useCallback } from 'react';
import {
  scan, getRecommendations, getArbitrage,
  setRiskProfile, getRiskProfile, getPermission, getStatus,
} from '../api';
import {
  Search, ChevronDown, ChevronUp, Lock, Unlock,
  Shield, TrendingUp, AlertTriangle,
} from 'lucide-react';

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
  transition: 'all 0.15s',
};

/* ─── Constants ─── */
const SAFETY_TAGS = ['ALL', 'VERY_SAFE', 'SAFE', 'MODERATE'];
const STRATEGY_TYPES = ['ALL', 'COVERED_CALL', 'CASH_SECURED_PUT', 'PUT_CREDIT_SPREAD', 'ARBITRAGE'];
const RISK_PROFILES = [
  { key: 'CONSERVATIVE', label: 'Conservative', icon: Shield, color: C.emerald },
  { key: 'MODERATE', label: 'Moderate', icon: TrendingUp, color: C.amber },
  { key: 'AGGRESSIVE', label: 'Aggressive', icon: AlertTriangle, color: C.red },
];

const SAFETY_COLORS = {
  VERY_SAFE: C.emerald,
  SAFE: C.blue,
  MODERATE: C.amber,
  RISKY: C.red,
};

const TYPE_COLORS = {
  COVERED_CALL: C.purple,
  CASH_SECURED_PUT: C.blue,
  PUT_CREDIT_SPREAD: C.amber,
  ARBITRAGE: C.emerald,
};

const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 }));
const fmtCur = (n) => (n == null ? '—' : '₹' + fmt(n));
const fmtPct = (n) => (n == null ? '—' : `${Number(n).toFixed(1)}%`);

export default function Scanner() {
  /* ─── State ─── */
  const [recommendations, setRecommendations] = useState([]);
  const [arbitrage, setArbitrage] = useState([]);
  const [riskProfile, setRiskProfileState] = useState('MODERATE');
  const [permission, setPermission] = useState('READONLY');
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState(null);
  const [expandedId, setExpandedId] = useState(null);

  // Filters
  const [safetyFilter, setSafetyFilter] = useState('ALL');
  const [strategyFilter, setStrategyFilter] = useState('ALL');

  // Summary
  const [summary, setSummary] = useState({ weeklyIncome: null, arbCount: 0 });

  /* ─── Init ─── */
  useEffect(() => {
    getRiskProfile().then((d) => setRiskProfileState(d?.profile || d?.risk_profile || 'MODERATE')).catch(() => {});
    getPermission().then((d) => setPermission(d?.mode || d?.permission || 'READONLY')).catch(() => {});
    getArbitrage().then((d) => {
      const arbs = d?.opportunities || d || [];
      setArbitrage(arbs);
      setSummary((s) => ({ ...s, arbCount: arbs.length }));
    }).catch(() => {});

    // Auto-scan if holdings exist and no recommendations loaded yet
    getStatus().then((st) => {
      if ((st?.holdings_count ?? 0) > 0) {
        handleScan();
      }
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* ─── Handlers ─── */
  const handleScan = useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      await scan();
      const filters = {};
      if (safetyFilter !== 'ALL') filters.safety = safetyFilter;
      if (strategyFilter !== 'ALL') filters.strategy = strategyFilter;
      const data = await getRecommendations(filters);
      const recs = data?.recommendations || data || [];
      setRecommendations(recs);
      const totalWeekly = recs.reduce((s, r) => s + (r.premium || 0), 0);
      setSummary((prev) => ({ ...prev, weeklyIncome: totalWeekly }));
    } catch (e) {
      setError(e.message);
    } finally {
      setScanning(false);
    }
  }, [safetyFilter, strategyFilter]);

  const handleRiskChange = async (profile) => {
    try {
      await setRiskProfile({ profile });
      setRiskProfileState(profile);
    } catch (e) { setError(e.message); }
  };

  const handleFilterChange = useCallback(async () => {
    if (recommendations.length === 0) return;
    try {
      const filters = {};
      if (safetyFilter !== 'ALL') filters.safety = safetyFilter;
      if (strategyFilter !== 'ALL') filters.strategy = strategyFilter;
      const data = await getRecommendations(filters);
      setRecommendations(data?.recommendations || data || []);
    } catch (e) { setError(e.message); }
  }, [safetyFilter, strategyFilter, recommendations.length]);

  useEffect(() => {
    if (recommendations.length > 0) handleFilterChange();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [safetyFilter, strategyFilter]);

  const toggleExpand = (id) => setExpandedId((prev) => (prev === id ? null : id));

  const isExecute = permission === 'EXECUTE';

  /* ─── Render ─── */
  return (
    <div style={{ minHeight: '100vh', background: C.bg, color: C.text, fontFamily: font.sans, padding: '32px 24px' }}>
      <div style={{ maxWidth: 1280, margin: '0 auto' }}>

        {/* Header */}
        <h1 style={{ fontSize: 28, fontWeight: 700, margin: '0 0 28px' }}>Scanner</h1>

        {/* Error */}
        {error && (
          <div style={{ ...cardStyle, borderColor: C.red, marginBottom: 20, padding: 16, color: C.red, fontSize: 14 }}>
            {error}
            <span onClick={() => setError(null)} style={{ float: 'right', cursor: 'pointer', fontWeight: 700 }}>✕</span>
          </div>
        )}

        {/* Summary bar */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16, marginBottom: 28 }}>
          <div style={cardStyle}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Safe Weekly Income Est.</div>
            <div style={{ fontSize: 22, fontWeight: 700, fontFamily: font.mono, color: C.emerald }}>
              {summary.weeklyIncome != null ? fmtCur(summary.weeklyIncome) : '—'}
            </div>
          </div>
          <div style={cardStyle}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Arbitrage Opportunities</div>
            <div style={{ fontSize: 22, fontWeight: 700, fontFamily: font.mono, color: C.blue }}>{summary.arbCount}</div>
          </div>
          <div style={cardStyle}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Risk Profile</div>
            <span
              style={{
                display: 'inline-block',
                padding: '6px 14px',
                borderRadius: 999,
                fontSize: 13,
                fontWeight: 700,
                background: `${RISK_PROFILES.find((r) => r.key === riskProfile)?.color || C.amber}20`,
                color: RISK_PROFILES.find((r) => r.key === riskProfile)?.color || C.amber,
              }}
            >
              {riskProfile}
            </span>
          </div>
        </div>

        {/* Filters + Controls */}
        <div style={{ ...cardStyle, marginBottom: 28 }}>
          {/* Safety tags */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Safety Filter</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {SAFETY_TAGS.map((tag) => (
                <button
                  key={tag}
                  onClick={() => setSafetyFilter(tag)}
                  style={{
                    ...btnBase,
                    padding: '8px 16px',
                    fontSize: 13,
                    borderRadius: 999,
                    background: safetyFilter === tag
                      ? (SAFETY_COLORS[tag] || C.text)
                      : `${SAFETY_COLORS[tag] || C.text}15`,
                    color: safetyFilter === tag ? '#0a0f1a' : (SAFETY_COLORS[tag] || C.text),
                  }}
                >
                  {tag.replace('_', ' ')}
                </button>
              ))}
            </div>
          </div>

          {/* Strategy type + Risk profile row */}
          <div style={{ display: 'flex', gap: 20, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div style={{ minWidth: 200 }}>
              <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Strategy Type</div>
              <select
                value={strategyFilter}
                onChange={(e) => setStrategyFilter(e.target.value)}
                style={{
                  background: 'rgba(15,23,42,0.9)',
                  border: `1px solid ${C.border}`,
                  borderRadius: 10,
                  padding: '10px 14px',
                  color: C.text,
                  fontFamily: font.sans,
                  fontSize: 14,
                  outline: 'none',
                  width: '100%',
                  cursor: 'pointer',
                }}
              >
                {STRATEGY_TYPES.map((s) => (
                  <option key={s} value={s} style={{ background: '#0a0f1a' }}>
                    {s === 'ALL' ? 'All Strategies' : s.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>
            </div>

            {/* Risk profile quick-switch */}
            <div>
              <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Risk Profile</div>
              <div style={{ display: 'flex', gap: 8 }}>
                {RISK_PROFILES.map(({ key, label, icon: Ic, color }) => (
                  <button
                    key={key}
                    onClick={() => handleRiskChange(key)}
                    style={{
                      ...btnBase,
                      padding: '8px 16px',
                      fontSize: 13,
                      background: riskProfile === key ? color : `${color}15`,
                      color: riskProfile === key ? '#0a0f1a' : color,
                    }}
                  >
                    <Ic size={14} /> {label}
                  </button>
                ))}
              </div>
            </div>

            {/* Scan button */}
            <button
              onClick={handleScan}
              disabled={scanning}
              style={{
                ...btnBase,
                padding: '12px 28px',
                fontSize: 15,
                background: scanning ? `${C.blue}60` : C.blue,
                color: '#0a0f1a',
                marginLeft: 'auto',
              }}
            >
              <Search size={16} /> {scanning ? 'Scanning...' : 'Scan Now'}
            </button>
          </div>
        </div>

        {/* Loading overlay */}
        {scanning && (
          <div style={{ ...cardStyle, textAlign: 'center', marginBottom: 28, padding: 48 }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>⟳</div>
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Scanning market...</div>
            <div style={{ fontSize: 13, color: C.muted }}>Analyzing option chains, computing probabilities, filtering by risk profile.</div>
          </div>
        )}

        {/* Recommendation cards */}
        {!scanning && recommendations.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {recommendations.map((rec, idx) => {
              const id = rec.id || `${rec.symbol}-${rec.strike}-${idx}`;
              const expanded = expandedId === id;
              const safetyColor = SAFETY_COLORS[rec.safety] || C.muted;
              const typeColor = TYPE_COLORS[rec.strategy] || C.muted;

              return (
                <div key={id} style={{ ...cardStyle, padding: 0, overflow: 'hidden' }}>
                  {/* Header row — always visible */}
                  <div
                    onClick={() => toggleExpand(id)}
                    style={{
                      padding: '20px 24px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 16,
                      flexWrap: 'wrap',
                      transition: 'background 0.15s',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(148,163,184,0.04)')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  >
                    {/* Rank */}
                    <span
                      style={{
                        width: 32, height: 32, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center',
                        background: `${C.purple}20`, color: C.purple, fontFamily: font.mono, fontSize: 14, fontWeight: 700, flexShrink: 0,
                      }}
                    >
                      {rec.rank || idx + 1}
                    </span>

                    {/* Symbol */}
                    <span style={{ fontWeight: 700, fontSize: 16, minWidth: 100 }}>{rec.symbol}</span>

                    {/* Strike */}
                    <span style={{ fontFamily: font.mono, fontSize: 14, color: C.muted }}>
                      {rec.strike ? `₹${fmt(rec.strike)}` : ''}
                    </span>

                    {/* Type tag */}
                    <span style={{
                      padding: '4px 10px', borderRadius: 999, fontSize: 11, fontWeight: 700,
                      background: `${typeColor}20`, color: typeColor, letterSpacing: 0.3,
                    }}>
                      {(rec.strategy || rec.type || '').replace(/_/g, ' ')}
                    </span>

                    {/* Safety tag */}
                    <span style={{
                      padding: '4px 10px', borderRadius: 999, fontSize: 11, fontWeight: 700,
                      background: `${safetyColor}20`, color: safetyColor,
                    }}>
                      {(rec.safety || '').replace('_', ' ')}
                    </span>

                    {/* Premium */}
                    <span style={{ fontFamily: font.mono, fontWeight: 700, fontSize: 16, color: C.emerald, marginLeft: 'auto' }}>
                      {fmtCur(rec.premium)}
                    </span>

                    {expanded ? <ChevronUp size={18} style={{ color: C.muted }} /> : <ChevronDown size={18} style={{ color: C.muted }} />}
                  </div>

                  {/* Metrics row */}
                  <div style={{
                    padding: '0 24px 16px',
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
                    gap: 12,
                  }}>
                    {[
                      { label: 'Prob OTM', value: fmtPct(rec.prob_otm), color: C.emerald },
                      { label: 'Delta', value: rec.delta != null ? rec.delta.toFixed(3) : '—', color: C.text },
                      { label: 'Margin', value: fmtCur(rec.margin), color: C.text },
                      { label: 'Ann. Return', value: fmtPct(rec.annualized_return), color: C.amber },
                      { label: 'Theta/day', value: rec.theta != null ? `₹${fmt(rec.theta)}` : '—', color: C.blue },
                    ].map((m) => (
                      <div key={m.label}>
                        <div style={{ fontSize: 11, color: C.muted, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>{m.label}</div>
                        <div style={{ fontFamily: font.mono, fontSize: 14, fontWeight: 600, color: m.color }}>{m.value}</div>
                      </div>
                    ))}
                  </div>

                  {/* Strike Rationale */}
                  {rec.rationale && (
                    <div style={{ padding: '0 24px 16px' }}>
                      <div style={{ fontSize: 13, color: C.muted, lineHeight: 1.6, fontStyle: 'italic' }}>
                        {rec.rationale}
                      </div>
                    </div>
                  )}

                  {/* Risk Ladder */}
                  {rec.risk_ladder && rec.risk_ladder.length > 0 && (
                    <div style={{ padding: '0 24px 16px' }}>
                      <div style={{ fontSize: 12, color: C.muted, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>Risk Ladder</div>
                      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                        {rec.risk_ladder.map((alt) => {
                          const isSelected = alt.profile === riskProfile;
                          const profileMeta = RISK_PROFILES.find((r) => r.key === alt.profile);
                          const color = profileMeta?.color || C.muted;
                          return (
                            <div
                              key={alt.profile}
                              style={{
                                background: isSelected ? `${color}15` : 'rgba(148,163,184,0.05)',
                                border: `1px solid ${isSelected ? color : C.border}`,
                                borderRadius: 12,
                                padding: '12px 16px',
                                minWidth: 160,
                                position: 'relative',
                              }}
                            >
                              {isSelected && (
                                <span style={{ position: 'absolute', top: -6, right: 8, fontSize: 14 }}>★</span>
                              )}
                              <div style={{ fontSize: 12, fontWeight: 700, color, marginBottom: 6 }}>
                                {alt.profile}
                              </div>
                              <div style={{ fontFamily: font.mono, fontSize: 14, fontWeight: 600, marginBottom: 4 }}>
                                Strike {fmtCur(alt.strike)}
                              </div>
                              <div style={{ display: 'flex', gap: 16, fontSize: 12, color: C.muted }}>
                                <span>Prem: <span style={{ color: C.emerald, fontFamily: font.mono }}>{fmtCur(alt.premium)}</span></span>
                                <span>Prob: <span style={{ color: C.text, fontFamily: font.mono }}>{fmtPct(alt.prob_otm)}</span></span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Expanded section */}
                  {expanded && (
                    <div style={{ borderTop: `1px solid ${C.border}`, padding: 24 }}>
                      {/* Order legs */}
                      {rec.legs && rec.legs.length > 0 && (
                        <div style={{ marginBottom: 20 }}>
                          <div style={{ fontSize: 12, color: C.muted, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>Order Legs</div>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {rec.legs.map((leg, li) => (
                              <div
                                key={li}
                                style={{
                                  display: 'flex', gap: 16, alignItems: 'center',
                                  padding: '10px 14px', borderRadius: 10,
                                  background: 'rgba(148,163,184,0.05)',
                                  fontSize: 14,
                                }}
                              >
                                <span style={{
                                  padding: '3px 8px', borderRadius: 6, fontSize: 11, fontWeight: 700,
                                  background: leg.side === 'BUY' ? `${C.emerald}20` : `${C.red}20`,
                                  color: leg.side === 'BUY' ? C.emerald : C.red,
                                }}>
                                  {leg.side}
                                </span>
                                <span style={{ fontWeight: 600 }}>{leg.instrument || leg.symbol}</span>
                                <span style={{ fontFamily: font.mono, color: C.muted }}>Qty: {leg.quantity}</span>
                                <span style={{ fontFamily: font.mono, color: C.muted, marginLeft: 'auto' }}>{fmtCur(leg.price)}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Fees estimate */}
                      {rec.fees && (
                        <div style={{ marginBottom: 20, fontSize: 13, color: C.muted }}>
                          Estimated fees &amp; charges: <span style={{ fontFamily: font.mono, color: C.text }}>{fmtCur(rec.fees)}</span>
                        </div>
                      )}

                      {/* Execute button */}
                      <button
                        disabled={!isExecute}
                        style={{
                          ...btnBase,
                          padding: '12px 28px',
                          fontSize: 15,
                          background: isExecute ? C.emerald : 'rgba(148,163,184,0.15)',
                          color: isExecute ? '#0a0f1a' : C.muted,
                          cursor: isExecute ? 'pointer' : 'not-allowed',
                        }}
                      >
                        {isExecute ? <Unlock size={16} /> : <Lock size={16} />}
                        {isExecute ? 'Execute Trade' : 'Read-Only Mode'}
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Empty state */}
        {!scanning && recommendations.length === 0 && (
          <div style={{ ...cardStyle, textAlign: 'center', padding: 64 }}>
            <Search size={40} style={{ color: C.muted, marginBottom: 16 }} />
            <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>No recommendations yet</div>
            <div style={{ fontSize: 14, color: C.muted }}>
              Import your holdings, set your risk profile, then hit "Scan Now" to find opportunities.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
