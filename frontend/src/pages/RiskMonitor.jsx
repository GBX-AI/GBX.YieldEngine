import React, { useState, useEffect, useCallback } from 'react';
import { getRiskStatus, getRiskAlerts, getPositions, getAdjustments, getActiveGtt, getNotifications } from '../api';

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
  },
  title: {
    fontSize: 22,
    fontWeight: 700,
    color: c.text,
  },
  refreshBtn: {
    padding: '8px 18px',
    borderRadius: 10,
    border: 'none',
    background: c.blue,
    color: '#0a0f1a',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: sans,
  },
  grid4: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
    gap: 16,
    marginBottom: 24,
  },
  card: {
    background: c.card,
    border: `1px solid ${c.border}`,
    borderRadius: 16,
    padding: 24,
    backdropFilter: 'blur(12px)',
  },
  cardLabel: {
    fontSize: 12,
    fontWeight: 500,
    color: c.muted,
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
  },
  cardValue: {
    fontSize: 28,
    fontWeight: 700,
    fontFamily: mono,
    lineHeight: 1.2,
  },
  cardSub: {
    fontSize: 12,
    color: c.muted,
    marginTop: 6,
    fontFamily: mono,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: 600,
    color: c.text,
    marginBottom: 16,
  },
  row: {
    display: 'flex',
    gap: 16,
    marginBottom: 24,
    flexWrap: 'wrap',
  },
  flexGrow: {
    flex: 1,
    minWidth: 320,
  },
  badge: {
    display: 'inline-block',
    padding: '3px 10px',
    borderRadius: 20,
    fontSize: 11,
    fontWeight: 600,
    fontFamily: mono,
  },
  tableRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 0',
    borderBottom: `1px solid ${c.border}`,
  },
  progressTrack: {
    width: '100%',
    height: 8,
    borderRadius: 4,
    background: 'rgba(148,163,184,0.15)',
    marginTop: 10,
  },
  adjustBtn: {
    padding: '6px 14px',
    borderRadius: 8,
    border: `1px solid ${c.amber}`,
    background: 'transparent',
    color: c.amber,
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: sans,
  },
  timelineDot: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    flexShrink: 0,
    marginTop: 4,
  },
  timelineItem: {
    display: 'flex',
    gap: 12,
    padding: '10px 0',
    borderBottom: `1px solid ${c.border}`,
  },
};

function deltaColor(val) {
  if (val > 0.3) return c.emerald;
  if (val < -0.3) return c.red;
  return c.amber;
}

function marginColor(pct) {
  if (pct < 60) return c.emerald;
  if (pct < 80) return c.amber;
  return c.red;
}

function severityColor(sev) {
  if (sev === 'critical' || sev === 'high') return c.red;
  if (sev === 'medium' || sev === 'warning') return c.amber;
  return c.blue;
}

export default function RiskMonitor() {
  const [risk, setRisk] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [positions, setPositions] = useState([]);
  const [gttOrders, setGttOrders] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [expandedPos, setExpandedPos] = useState(null);
  const [adjustments, setAdjustments] = useState({});
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [r, a, p, g, n] = await Promise.all([
        getRiskStatus().catch(() => null),
        getRiskAlerts().catch(() => []),
        getPositions().catch(() => []),
        getActiveGtt().catch(() => []),
        getNotifications(1).catch(() => ({ results: [] })),
      ]);
      setRisk(r);
      setAlerts(Array.isArray(a) ? a : []);
      setPositions(Array.isArray(p) ? p : []);
      setGttOrders(Array.isArray(g) ? g : []);
      setNotifications(Array.isArray(n?.results) ? n.results : Array.isArray(n) ? n : []);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const toggleAdjustments = async (posId) => {
    if (expandedPos === posId) {
      setExpandedPos(null);
      return;
    }
    setExpandedPos(posId);
    if (!adjustments[posId]) {
      try {
        const adj = await getAdjustments(posId);
        setAdjustments(prev => ({ ...prev, [posId]: adj }));
      } catch { /* ignore */ }
    }
  };

  const netDelta = risk?.net_delta ?? 0;
  const marginUtil = risk?.margin_utilization ?? 0;
  const dailyPnl = risk?.daily_pnl ?? 0;
  const openCount = risk?.open_positions ?? 0;
  const atRiskCount = risk?.at_risk_count ?? 0;
  const circuitBreaker = risk?.circuit_breaker_active ?? false;

  // Positions at risk: high delta or premium > 1.5x entry
  const atRiskPositions = positions.filter(p =>
    Math.abs(p.delta ?? 0) > (risk?.delta_threshold ?? 0.5) ||
    (p.current_premium && p.entry_premium && p.current_premium > 1.5 * p.entry_premium)
  );

  const recentNotifs = notifications.slice(0, 15);

  if (loading) {
    return (
      <div style={{ ...s.page, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ color: c.muted, fontSize: 14 }}>Loading risk data...</span>
      </div>
    );
  }

  return (
    <div style={s.page}>
      {/* Header */}
      <div style={s.header}>
        <span style={s.title}>Risk Monitor</span>
        <button style={s.refreshBtn} onClick={fetchAll}>Refresh</button>
      </div>

      {/* Top Gauges */}
      <div style={s.grid4}>
        {/* Net Delta */}
        <div style={s.card}>
          <div style={s.cardLabel}>Portfolio Net Delta</div>
          <div style={{ ...s.cardValue, color: deltaColor(netDelta) }}>
            {netDelta >= 0 ? '+' : ''}{netDelta.toFixed(3)}
          </div>
          <div style={s.progressTrack}>
            <div style={{
              position: 'relative',
              height: '100%',
            }}>
              <div style={{
                position: 'absolute',
                left: `${((netDelta + 1) / 2) * 100}%`,
                top: -2,
                width: 12,
                height: 12,
                borderRadius: '50%',
                background: deltaColor(netDelta),
                transform: 'translateX(-50%)',
                boxShadow: `0 0 8px ${deltaColor(netDelta)}`,
              }} />
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
            <span style={{ fontSize: 10, color: c.muted, fontFamily: mono }}>-1.0</span>
            <span style={{ fontSize: 10, color: c.muted, fontFamily: mono }}>0</span>
            <span style={{ fontSize: 10, color: c.muted, fontFamily: mono }}>+1.0</span>
          </div>
        </div>

        {/* Margin Utilization */}
        <div style={s.card}>
          <div style={s.cardLabel}>Margin Utilization</div>
          <div style={{ ...s.cardValue, color: marginColor(marginUtil) }}>
            {marginUtil.toFixed(1)}%
          </div>
          <div style={s.progressTrack}>
            <div style={{
              height: '100%',
              borderRadius: 4,
              width: `${Math.min(marginUtil, 100)}%`,
              background: marginColor(marginUtil),
              transition: 'width 0.4s ease',
            }} />
          </div>
          <div style={s.cardSub}>
            {marginUtil < 60 ? 'Healthy' : marginUtil < 80 ? 'Caution' : 'Critical'}
          </div>
        </div>

        {/* Daily P&L */}
        <div style={s.card}>
          <div style={s.cardLabel}>Daily P&L</div>
          <div style={{ ...s.cardValue, color: dailyPnl >= 0 ? c.emerald : c.red }}>
            {dailyPnl >= 0 ? '+' : ''}{dailyPnl.toLocaleString('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })}
          </div>
          <div style={s.cardSub}>Today</div>
        </div>

        {/* Open Positions */}
        <div style={s.card}>
          <div style={s.cardLabel}>Open Positions</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ ...s.cardValue, color: c.text }}>{openCount}</span>
            {atRiskCount > 0 && (
              <span style={{
                ...s.badge,
                background: `${c.red}20`,
                color: c.red,
              }}>
                {atRiskCount} at risk
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Main content */}
      <div style={s.row}>
        {/* Positions at Risk */}
        <div style={s.flexGrow}>
          <div style={{ ...s.card, height: '100%' }}>
            <div style={s.sectionTitle}>Positions at Risk</div>
            {atRiskPositions.length === 0 ? (
              <div style={{ color: c.muted, fontSize: 13, textAlign: 'center', padding: 24 }}>
                No positions flagged
              </div>
            ) : (
              atRiskPositions.map(pos => (
                <div key={pos.id} style={{ borderBottom: `1px solid ${c.border}`, paddingBottom: 12, marginBottom: 12 }}>
                  <div style={s.tableRow}>
                    <div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: c.text }}>{pos.symbol || pos.tradingsymbol}</div>
                      <div style={{ fontSize: 12, color: c.muted, fontFamily: mono, marginTop: 2 }}>
                        Delta: {(pos.delta ?? 0).toFixed(3)} | Premium: {pos.current_premium ?? '—'}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      {Math.abs(pos.delta ?? 0) > (risk?.delta_threshold ?? 0.5) && (
                        <span style={{ ...s.badge, background: `${c.red}20`, color: c.red }}>High Delta</span>
                      )}
                      {pos.current_premium && pos.entry_premium && pos.current_premium > 1.5 * pos.entry_premium && (
                        <span style={{ ...s.badge, background: `${c.amber}20`, color: c.amber }}>Premium 1.5x</span>
                      )}
                      <button style={s.adjustBtn} onClick={() => toggleAdjustments(pos.id)}>
                        {expandedPos === pos.id ? 'Close' : 'Adjust'}
                      </button>
                    </div>
                  </div>
                  {expandedPos === pos.id && (
                    <div style={{ padding: '12px 0 0', marginLeft: 16 }}>
                      {adjustments[pos.id] ? (
                        Array.isArray(adjustments[pos.id]) && adjustments[pos.id].length > 0 ? (
                          adjustments[pos.id].map((adj, i) => (
                            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', fontSize: 13, color: c.text }}>
                              <span>{adj.type || adj.action}</span>
                              <span style={{ fontFamily: mono, color: c.muted }}>{adj.description || adj.detail || '—'}</span>
                            </div>
                          ))
                        ) : (
                          <div style={{ fontSize: 12, color: c.muted }}>No adjustments available</div>
                        )
                      ) : (
                        <div style={{ fontSize: 12, color: c.muted }}>Loading adjustments...</div>
                      )}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* Active GTT Orders */}
        <div style={{ ...s.flexGrow, maxWidth: 420 }}>
          <div style={{ ...s.card, height: '100%' }}>
            <div style={s.sectionTitle}>Active GTT Orders</div>
            {gttOrders.length === 0 ? (
              <div style={{ color: c.muted, fontSize: 13, textAlign: 'center', padding: 24 }}>
                No active GTT orders
              </div>
            ) : (
              gttOrders.map((gtt, i) => (
                <div key={gtt.id ?? i} style={s.tableRow}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: c.text }}>{gtt.tradingsymbol || gtt.symbol}</div>
                    <div style={{ fontSize: 11, color: c.muted, fontFamily: mono, marginTop: 2 }}>
                      Trigger: {gtt.trigger_price ?? '—'} | Qty: {gtt.quantity ?? '—'}
                    </div>
                  </div>
                  <span style={{
                    ...s.badge,
                    background: gtt.status === 'active'
                      ? `${c.emerald}20`
                      : gtt.status === 'triggered'
                        ? `${c.blue}20`
                        : `${c.muted}20`,
                    color: gtt.status === 'active'
                      ? c.emerald
                      : gtt.status === 'triggered'
                        ? c.blue
                        : c.muted,
                  }}>
                    {gtt.status || 'unknown'}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Bottom row */}
      <div style={s.row}>
        {/* Risk Event Log */}
        <div style={s.flexGrow}>
          <div style={{ ...s.card, height: '100%' }}>
            <div style={s.sectionTitle}>Risk Event Log</div>
            {recentNotifs.length === 0 ? (
              <div style={{ color: c.muted, fontSize: 13, textAlign: 'center', padding: 24 }}>
                No recent events
              </div>
            ) : (
              recentNotifs.map((n, i) => (
                <div key={n.id ?? i} style={s.timelineItem}>
                  <div style={{
                    ...s.timelineDot,
                    background: severityColor(n.severity || n.type),
                  }} />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 13, color: c.text, lineHeight: 1.5 }}>{n.message || n.title}</div>
                    <div style={{ fontSize: 11, color: c.muted, fontFamily: mono, marginTop: 2 }}>
                      {n.created_at ? new Date(n.created_at).toLocaleString() : '—'}
                    </div>
                  </div>
                  <span style={{
                    ...s.badge,
                    background: `${severityColor(n.severity || n.type)}20`,
                    color: severityColor(n.severity || n.type),
                    alignSelf: 'flex-start',
                  }}>
                    {n.severity || n.type || 'info'}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Circuit Breaker */}
        <div style={{ minWidth: 280, maxWidth: 340 }}>
          <div style={s.card}>
            <div style={s.sectionTitle}>Circuit Breaker Status</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 16 }}>
              <div style={{
                width: 48,
                height: 48,
                borderRadius: 12,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: circuitBreaker ? `${c.red}20` : `${c.emerald}20`,
                fontSize: 22,
              }}>
                {circuitBreaker ? '⛔' : '✅'}
              </div>
              <div>
                <div style={{ fontSize: 18, fontWeight: 700, color: circuitBreaker ? c.red : c.emerald, fontFamily: mono }}>
                  {circuitBreaker ? 'ACTIVE' : 'NORMAL'}
                </div>
                <div style={{ fontSize: 12, color: c.muted }}>
                  {circuitBreaker ? 'Trading halted — thresholds breached' : 'All systems operational'}
                </div>
              </div>
            </div>

            {/* Alerts summary */}
            {alerts.length > 0 && (
              <div>
                <div style={{ fontSize: 12, fontWeight: 500, color: c.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  Active Alerts ({alerts.length})
                </div>
                {alerts.slice(0, 5).map((a, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: `1px solid ${c.border}` }}>
                    <div style={{ ...s.timelineDot, background: severityColor(a.severity), width: 6, height: 6 }} />
                    <span style={{ fontSize: 12, color: c.text, flex: 1 }}>{a.message || a.rule}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
