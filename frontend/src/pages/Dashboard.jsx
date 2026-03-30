import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { TrendingUp, TrendingDown, Activity, Search, Download, Eye } from 'lucide-react';
import { getAnalyticsSummary, getAnalyticsMonthly, getPositions, getDailySummary, getNotifications, scan, getHoldings, getSentiment } from '../api';

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
    padding: '24px',
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
    minWidth: 280,
  },
  actionBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '10px 20px',
    borderRadius: 10,
    border: 'none',
    cursor: 'pointer',
    fontSize: 13,
    fontWeight: 600,
    fontFamily: sans,
    transition: 'opacity 0.15s ease',
  },
  notifItem: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '10px 0',
    borderBottom: `1px solid ${c.border}`,
  },
  notifDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    marginTop: 5,
    flexShrink: 0,
  },
  notifText: {
    fontSize: 13,
    color: c.text,
    lineHeight: 1.5,
  },
  notifTime: {
    fontSize: 11,
    color: c.muted,
    fontFamily: mono,
    marginTop: 2,
  },
  dailyRow: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '8px 0',
    borderBottom: `1px solid ${c.border}`,
  },
  dailyLabel: {
    fontSize: 13,
    color: c.muted,
  },
  dailyValue: {
    fontSize: 13,
    fontWeight: 600,
    fontFamily: mono,
  },
};

const severityColor = {
  info: c.blue,
  success: c.emerald,
  warning: c.amber,
  error: c.red,
  critical: c.red,
};

const formatCurrency = (v) => {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const abs = Math.abs(v);
  const prefix = v < 0 ? '-' : '';
  if (abs >= 1e7) return `${prefix}₹${(abs / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${prefix}₹${(abs / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `${prefix}₹${(abs / 1e3).toFixed(1)}K`;
  return `${prefix}₹${abs.toFixed(2)}`;
};

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  const val = payload[0].value;
  return (
    <div
      style={{
        background: 'rgba(15,23,42,0.95)',
        border: `1px solid ${c.border}`,
        borderRadius: 8,
        padding: '8px 12px',
        backdropFilter: 'blur(12px)',
      }}
    >
      <div style={{ fontSize: 12, color: c.muted, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, fontFamily: mono, color: val >= 0 ? c.emerald : c.red }}>
        {formatCurrency(val)}
      </div>
    </div>
  );
};

export default function Dashboard() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [monthly, setMonthly] = useState([]);
  const [positions, setPositions] = useState([]);
  const [daily, setDaily] = useState(null);
  const [notifications, setNotifications] = useState([]);
  const [portfolio, setPortfolio] = useState(null);
  const [sentiment, setSentiment] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const [sumRes, monthRes, posRes, dailyRes, notifRes, holdRes, sentRes] = await Promise.allSettled([
          getAnalyticsSummary(),
          getAnalyticsMonthly(),
          getPositions(),
          getDailySummary(),
          getNotifications(),
          getHoldings(),
          getSentiment().catch(() => null),
        ]);
        if (!mounted) return;
        if (sumRes.status === 'fulfilled') setSummary(sumRes.value);
        if (monthRes.status === 'fulfilled') {
          const d = Array.isArray(monthRes.value) ? monthRes.value : monthRes.value?.data ?? [];
          setMonthly(d);
        }
        if (posRes.status === 'fulfilled') {
          const p = Array.isArray(posRes.value) ? posRes.value : posRes.value?.positions ?? [];
          setPositions(p);
        }
        if (dailyRes.status === 'fulfilled') setDaily(dailyRes.value);
        if (notifRes.status === 'fulfilled') {
          const n = Array.isArray(notifRes.value) ? notifRes.value : notifRes.value?.notifications ?? [];
          setNotifications(n.slice(0, 5));
        }
        if (holdRes.status === 'fulfilled') setPortfolio(holdRes.value?.summary ?? null);
        if (sentRes.status === 'fulfilled' && sentRes.value) setSentiment(sentRes.value);
      } catch { /* silent */ }
      if (mounted) setLoading(false);
    };
    load();
    return () => { mounted = false; };
  }, []);

  const handleScan = async () => {
    setScanning(true);
    try {
      await scan();
    } catch { /* silent */ }
    setScanning(false);
  };

  const totalIncome = summary?.totalIncome ?? 0;
  const totalLoss = summary?.totalLoss ?? 0;
  const netPnl = summary?.netPnl ?? totalIncome - Math.abs(totalLoss);
  const winRate = summary?.winRate ?? 0;
  const openCount = positions.filter((p) => p.status === 'open' || !p.closedAt).length || positions.length;
  const unrealizedPnl = positions.reduce((acc, p) => acc + (p.unrealizedPnl ?? p.pnl ?? 0), 0);

  const portfolioCards = portfolio ? [
    { label: 'Portfolio Value', value: formatCurrency(portfolio.portfolio_value), color: c.blue },
    { label: 'Unrealized P&L', value: formatCurrency(portfolio.unrealized_pnl), color: (portfolio.unrealized_pnl ?? 0) >= 0 ? c.emerald : c.red },
    { label: 'Usable Margin', value: formatCurrency(portfolio.usable_margin), color: c.purple },
    { label: 'Holdings', value: `${portfolio.holdings?.length ?? 0} stocks`, color: c.text },
  ] : [];

  const statCards = [
    { label: 'Total Income', value: formatCurrency(totalIncome), color: c.emerald, icon: TrendingUp },
    { label: 'Total Loss', value: formatCurrency(totalLoss), color: c.red, icon: TrendingDown },
    { label: 'Net P&L', value: formatCurrency(netPnl), color: netPnl >= 0 ? c.emerald : c.red, icon: Activity },
    { label: 'Win Rate', value: `${winRate.toFixed(1)}%`, color: c.purple, icon: TrendingUp },
  ];

  const dailyEntries = daily
    ? [
        { label: 'Trades Executed', value: daily.tradesExecuted ?? daily.trades ?? '—' },
        { label: 'Income', value: formatCurrency(daily.income ?? daily.profit ?? 0), color: c.emerald },
        { label: 'Loss', value: formatCurrency(daily.loss ?? 0), color: c.red },
        { label: 'Net', value: formatCurrency(daily.net ?? (daily.income ?? 0) - Math.abs(daily.loss ?? 0)), color: (daily.net ?? 0) >= 0 ? c.emerald : c.red },
        { label: 'Positions Opened', value: daily.positionsOpened ?? '—' },
        { label: 'Positions Closed', value: daily.positionsClosed ?? '—' },
      ]
    : [];

  if (loading) {
    return (
      <div style={{ ...s.page, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ color: c.muted, fontSize: 14 }}>Loading dashboard...</div>
      </div>
    );
  }

  return (
    <div style={s.page}>
      {/* Morning Briefing Card */}
      {sentiment && (
        <div style={{
          ...s.card, marginBottom: 24,
          borderLeft: `4px solid ${sentiment.color}`,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
            <div style={{
              width: 12, height: 12, borderRadius: '50%',
              background: sentiment.color,
              boxShadow: `0 0 8px ${sentiment.color}60`,
            }} />
            <div style={{ fontSize: 16, fontWeight: 700, color: sentiment.color }}>
              Market Sentiment: {sentiment.signal}
            </div>
            <div style={{ fontSize: 12, color: c.muted, fontFamily: mono, marginLeft: 'auto' }}>
              Score: {sentiment.score}/100
            </div>
          </div>

          <div style={{ fontSize: 13, color: c.text, marginBottom: 16, lineHeight: 1.6 }}>
            {sentiment.summary}
          </div>

          {/* Factor rows */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
            {(sentiment.factors || []).map((f, i) => (
              <div key={i} style={{
                padding: '10px 14px', borderRadius: 10,
                background: f.signal === 'GREEN' ? 'rgba(110,231,183,0.06)' :
                  f.signal === 'RED' ? 'rgba(248,113,113,0.06)' : 'rgba(252,211,77,0.06)',
                border: `1px solid ${f.signal === 'GREEN' ? 'rgba(110,231,183,0.15)' :
                  f.signal === 'RED' ? 'rgba(248,113,113,0.15)' : 'rgba(252,211,77,0.15)'}`,
              }}>
                <div style={{ fontSize: 11, color: c.muted, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>
                  {f.name}
                </div>
                <div style={{ fontSize: 14, fontWeight: 600, fontFamily: mono,
                  color: f.signal === 'GREEN' ? c.emerald : f.signal === 'RED' ? c.red : c.amber,
                }}>
                  {f.value}
                </div>
                {f.details && (
                  <div style={{ marginTop: 6, fontSize: 11, color: c.muted }}>
                    {Object.entries(f.details).map(([k, v]) => (
                      <div key={k}>{k}: <span style={{ fontFamily: mono }}>{v}</span></div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* US Event Warnings */}
          {sentiment.us_events?.has_warning && (
            <div style={{ marginTop: 16 }}>
              {(sentiment.us_events.warnings || []).map((w, i) => (
                <div key={i} style={{
                  padding: '12px 16px', borderRadius: 10, marginBottom: 8,
                  background: w.level === 'RED' ? 'rgba(248,113,113,0.1)' : 'rgba(252,211,77,0.1)',
                  border: `1px solid ${w.level === 'RED' ? 'rgba(248,113,113,0.25)' : 'rgba(252,211,77,0.25)'}`,
                }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: w.level === 'RED' ? c.red : c.amber, marginBottom: 4 }}>
                    {w.level === 'RED' ? '⚠' : '⚡'} {w.message}
                  </div>
                  <div style={{ fontSize: 12, color: c.muted }}>{w.recommendation}</div>
                </div>
              ))}

              {/* Recent US Economic Data */}
              {(sentiment.us_events.recent_surprises || []).filter(s => s.severity === 'HIGH').map((s, i) => (
                <div key={i} style={{
                  padding: '10px 16px', borderRadius: 10, marginBottom: 8,
                  background: s.surprise_direction === 'POSITIVE' ? 'rgba(110,231,183,0.06)' :
                    s.surprise_direction === 'NEGATIVE' ? 'rgba(248,113,113,0.06)' : 'rgba(148,163,184,0.06)',
                  border: `1px solid rgba(148,163,184,0.1)`,
                }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 2,
                    color: s.surprise_direction === 'POSITIVE' ? c.emerald : s.surprise_direction === 'NEGATIVE' ? c.red : c.muted,
                  }}>
                    US {s.indicator}: {s.actual} (prev: {s.previous})
                  </div>
                  <div style={{ fontSize: 12, color: c.muted }}>{s.interpretation}</div>
                </div>
              ))}
            </div>
          )}

          {/* Latest US Economic Readings */}
          {sentiment.us_events?.latest_readings && Object.keys(sentiment.us_events.latest_readings).length > 0 && (
            <div style={{ marginTop: 12, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              {Object.entries(sentiment.us_events.latest_readings).map(([key, r]) => (
                <div key={key} style={{ fontSize: 11, color: c.muted, fontFamily: mono }}>
                  {r.short_name}: <span style={{ color: c.text }}>{r.value}{r.unit === 'percent' ? '%' : ''}</span>
                  <span style={{ color: c.muted }}> ({r.date})</span>
                </div>
              ))}
            </div>
          )}

          {sentiment.fetched_at && (
            <div style={{ marginTop: 12, fontSize: 11, color: c.muted, fontFamily: mono }}>
              Updated: {new Date(sentiment.fetched_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })} IST
            </div>
          )}
        </div>
      )}

      {/* Portfolio Overview */}
      {portfolioCards.length > 0 && (
        <>
          <div style={{ ...s.sectionTitle, marginBottom: 12 }}>Portfolio Overview</div>
          <div style={s.grid4}>
            {portfolioCards.map(({ label, value, color }) => (
              <div key={label} style={s.card}>
                <div style={s.cardLabel}>{label}</div>
                <div style={{ ...s.cardValue, color }}>{value}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Trading Performance */}
      <div style={{ ...s.sectionTitle, marginBottom: 12 }}>Trading Performance</div>
      <div style={s.grid4}>
        {statCards.map(({ label, value, color, icon: Icon }) => (
          <div key={label} style={s.card}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={s.cardLabel}>{label}</div>
              <Icon size={18} color={color} style={{ opacity: 0.7 }} />
            </div>
            <div style={{ ...s.cardValue, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Chart + Open Positions */}
      <div style={s.row}>
        <div style={{ ...s.card, ...s.flexGrow, minWidth: 400 }}>
          <div style={s.sectionTitle}>Monthly Income</div>
          {monthly.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={monthly} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
                <CartesianGrid stroke="rgba(148,163,184,0.06)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="month"
                  tick={{ fill: c.muted, fontSize: 11, fontFamily: mono }}
                  axisLine={{ stroke: c.border }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: c.muted, fontSize: 11, fontFamily: mono }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => (v >= 1000 ? `${v / 1000}K` : v)}
                />
                <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(148,163,184,0.04)' }} />
                <Bar
                  dataKey="income"
                  radius={[4, 4, 0, 0]}
                  fill={c.emerald}
                  maxBarSize={36}
                  // Color per bar based on value
                  shape={(props) => {
                    const { x, y, width, height, value } = props;
                    const barColor = (value ?? 0) >= 0 ? c.emerald : c.red;
                    return (
                      <rect
                        x={x}
                        y={y}
                        width={width}
                        height={height}
                        rx={4}
                        ry={4}
                        fill={barColor}
                        fillOpacity={0.8}
                      />
                    );
                  }}
                />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: 260, display: 'flex', alignItems: 'center', justifyContent: 'center', color: c.muted, fontSize: 13 }}>
              No monthly data available
            </div>
          )}
        </div>

        <div style={{ ...s.card, minWidth: 220, maxWidth: 300 }}>
          <div style={s.cardLabel}>Open Positions</div>
          <div style={{ ...s.cardValue, color: c.text, marginBottom: 12 }}>{openCount}</div>
          <div style={s.cardLabel}>Unrealized P&L</div>
          <div style={{ ...s.cardValue, fontSize: 22, color: unrealizedPnl >= 0 ? c.emerald : c.red }}>
            {formatCurrency(unrealizedPnl)}
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div style={{ ...s.row, gap: 12, marginBottom: 24 }}>
        <button
          style={{
            ...s.actionBtn,
            background: scanning ? 'rgba(56,189,248,0.15)' : 'rgba(56,189,248,0.12)',
            color: c.blue,
            opacity: scanning ? 0.7 : 1,
          }}
          onClick={handleScan}
          disabled={scanning}
        >
          <Search size={15} />
          {scanning ? 'Scanning...' : 'Scan Now'}
        </button>
        <button
          style={{ ...s.actionBtn, background: 'rgba(110,231,183,0.12)', color: c.emerald }}
          onClick={() => navigate('/holdings')}
        >
          <Download size={15} />
          Import Portfolio
        </button>
        <button
          style={{ ...s.actionBtn, background: 'rgba(167,139,250,0.12)', color: c.purple }}
          onClick={() => navigate('/positions')}
        >
          <Eye size={15} />
          View Positions
        </button>
      </div>

      {/* Daily Summary + Notifications */}
      <div style={s.row}>
        {daily && (
          <div style={{ ...s.card, ...s.flexGrow }}>
            <div style={s.sectionTitle}>Today's Summary</div>
            {dailyEntries.map(({ label, value, color: entryColor }) => (
              <div key={label} style={s.dailyRow}>
                <span style={s.dailyLabel}>{label}</span>
                <span style={{ ...s.dailyValue, color: entryColor ?? c.text }}>{value}</span>
              </div>
            ))}
          </div>
        )}

        <div style={{ ...s.card, ...s.flexGrow }}>
          <div style={s.sectionTitle}>Recent Notifications</div>
          {notifications.length === 0 ? (
            <div style={{ color: c.muted, fontSize: 13, padding: '16px 0', textAlign: 'center' }}>
              No recent notifications
            </div>
          ) : (
            notifications.map((n, i) => (
              <div key={n.id ?? i} style={{ ...s.notifItem, borderBottom: i === notifications.length - 1 ? 'none' : s.notifItem.borderBottom }}>
                <div style={{ ...s.notifDot, background: severityColor[n.severity] ?? severityColor.info }} />
                <div>
                  <div style={s.notifText}>{n.message ?? n.text ?? 'Notification'}</div>
                  {n.timestamp && <div style={s.notifTime}>{n.timestamp}</div>}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
