import React, { useState, useEffect } from 'react';
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { getAnalyticsSummary, getAnalyticsStrategy, getAnalyticsMonthly, getFeesSummary } from '../api';

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
  card: {
    background: c.card,
    border: `1px solid ${c.border}`,
    borderRadius: 16,
    padding: 24,
    backdropFilter: 'blur(12px)',
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
    minWidth: 300,
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
  },
  th: {
    fontSize: 11,
    fontWeight: 600,
    color: c.muted,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    padding: '10px 12px',
    textAlign: 'left',
    borderBottom: `1px solid ${c.border}`,
  },
  thRight: {
    fontSize: 11,
    fontWeight: 600,
    color: c.muted,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    padding: '10px 12px',
    textAlign: 'right',
    borderBottom: `1px solid ${c.border}`,
  },
  td: {
    fontSize: 13,
    color: c.text,
    padding: '10px 12px',
    borderBottom: `1px solid ${c.border}`,
    fontFamily: mono,
  },
  tdLeft: {
    fontSize: 13,
    color: c.text,
    padding: '10px 12px',
    borderBottom: `1px solid ${c.border}`,
    fontFamily: sans,
    fontWeight: 500,
  },
  tdRight: {
    fontSize: 13,
    padding: '10px 12px',
    borderBottom: `1px solid ${c.border}`,
    fontFamily: mono,
    textAlign: 'right',
  },
  toggleRow: {
    display: 'flex',
    gap: 8,
    marginBottom: 16,
  },
  toggleBtn: (active) => ({
    padding: '6px 16px',
    borderRadius: 8,
    border: `1px solid ${active ? c.blue : c.border}`,
    background: active ? 'rgba(56,189,248,0.12)' : 'transparent',
    color: active ? c.blue : c.muted,
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: sans,
    transition: 'all 0.15s ease',
  }),
  perfItem: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '8px 0',
    borderBottom: `1px solid ${c.border}`,
  },
  perfName: {
    fontSize: 13,
    color: c.text,
    fontWeight: 500,
  },
  perfValue: {
    fontSize: 13,
    fontFamily: mono,
    fontWeight: 600,
  },
  feeRow: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '10px 0',
    borderBottom: `1px solid ${c.border}`,
  },
  feeLabel: {
    fontSize: 13,
    color: c.muted,
  },
  feeValue: {
    fontSize: 13,
    fontWeight: 600,
    fontFamily: mono,
    color: c.text,
  },
  grid4: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
    gap: 16,
    marginBottom: 24,
  },
  statLabel: {
    fontSize: 12,
    fontWeight: 500,
    color: c.muted,
    marginBottom: 6,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
  },
  statValue: {
    fontSize: 24,
    fontWeight: 700,
    fontFamily: mono,
    lineHeight: 1.2,
  },
  loading: {
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    minHeight: '60vh',
    color: c.muted,
    fontSize: 14,
  },
};

const fmt = (v) => {
  if (v == null) return '—';
  const abs = Math.abs(v);
  const prefix = v < 0 ? '-' : '';
  if (abs >= 1e6) return `${prefix}₹${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${prefix}₹${(abs / 1e3).toFixed(1)}K`;
  return `${prefix}₹${abs.toFixed(2)}`;
};

const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);

const pnlColor = (v) => (v >= 0 ? c.emerald : c.red);

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
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
      {payload.map((p, i) => (
        <div key={i} style={{ fontSize: 13, fontWeight: 600, fontFamily: mono, color: p.color || c.text, marginBottom: 2 }}>
          {p.name}: {fmt(p.value)}
        </div>
      ))}
    </div>
  );
};

const PIE_COLORS = [c.emerald, c.red, c.muted];

export default function Analytics() {
  const [summary, setSummary] = useState(null);
  const [strategies, setStrategies] = useState([]);
  const [monthly, setMonthly] = useState([]);
  const [fees, setFees] = useState(null);
  const [monthlyMode, setMonthlyMode] = useState('net'); // 'gross' | 'net'
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      getAnalyticsSummary().catch(() => null),
      getAnalyticsStrategy().catch(() => []),
      getAnalyticsMonthly().catch(() => []),
      getFeesSummary('all').catch(() => null),
    ]).then(([sum, strat, mon, fee]) => {
      setSummary(sum);
      setStrategies(Array.isArray(strat) ? strat : strat?.strategies || []);
      setMonthly(Array.isArray(mon) ? mon : mon?.months || []);
      setFees(fee);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return <div style={s.page}><div style={s.loading}>Loading analytics...</div></div>;
  }

  // Derive data
  const winRate = summary?.win_rate ?? 0;
  const lossRate = summary?.loss_rate ?? (1 - winRate);
  const breakeven = 1 - winRate - (summary?.loss_rate ?? (1 - winRate));
  const pieData = [
    { name: 'Wins', value: Math.max(winRate, 0) },
    { name: 'Losses', value: Math.max(summary?.loss_rate ?? (1 - winRate), 0) },
    { name: 'Breakeven', value: Math.max(breakeven, 0) },
  ].filter(d => d.value > 0);

  // Cumulative P&L
  let cumGross = 0;
  let cumNet = 0;
  const cumulative = monthly.map((m) => {
    cumGross += m.gross_pnl ?? m.total_gross_pnl ?? 0;
    cumNet += m.net_pnl ?? m.total_net_pnl ?? 0;
    return { month: m.month, cumGross, cumNet };
  });

  // Top / Worst performers
  const sorted = [...strategies].sort((a, b) => (b.total_net_pnl ?? 0) - (a.total_net_pnl ?? 0));
  const top5 = sorted.slice(0, 5);
  const worst5 = [...sorted].reverse().slice(0, 5);

  // Fee stats
  const totalFees = fees?.total_fees ?? summary?.total_fees ?? 0;
  const monthlyFees = fees?.monthly_fees ?? (totalFees / Math.max(monthly.length, 1));
  const yearlyFees = fees?.yearly_fees ?? totalFees;
  const grossIncome = summary?.total_gross_pnl ?? 1;
  const feePct = grossIncome !== 0 ? totalFees / Math.abs(grossIncome) : 0;
  const avgFee = fees?.avg_fee_per_trade ?? (summary?.total_trades ? totalFees / summary.total_trades : 0);

  return (
    <div style={s.page}>
      <div style={s.header}>
        <div style={s.title}>Analytics</div>
      </div>

      {/* Summary Stats */}
      <div style={s.grid4}>
        <div style={s.card}>
          <div style={s.statLabel}>Total Net P&L</div>
          <div style={{ ...s.statValue, color: pnlColor(summary?.total_net_pnl ?? 0) }}>
            {fmt(summary?.total_net_pnl)}
          </div>
        </div>
        <div style={s.card}>
          <div style={s.statLabel}>Total Trades</div>
          <div style={{ ...s.statValue, color: c.blue }}>{summary?.total_trades ?? 0}</div>
        </div>
        <div style={s.card}>
          <div style={s.statLabel}>Win Rate</div>
          <div style={{ ...s.statValue, color: c.emerald }}>{pct(winRate)}</div>
        </div>
        <div style={s.card}>
          <div style={s.statLabel}>Total Fees</div>
          <div style={{ ...s.statValue, color: c.amber }}>{fmt(totalFees)}</div>
        </div>
      </div>

      {/* Strategy Performance Table */}
      <div style={{ ...s.card, marginBottom: 24 }}>
        <div style={s.sectionTitle}>Strategy Performance</div>
        <div style={{ overflowX: 'auto' }}>
          <table style={s.table}>
            <thead>
              <tr>
                <th style={s.th}>Strategy</th>
                <th style={s.thRight}>Trades</th>
                <th style={s.thRight}>Gross P&L</th>
                <th style={s.thRight}>Fees</th>
                <th style={s.thRight}>Net P&L</th>
                <th style={s.thRight}>Avg P&L/Trade</th>
                <th style={s.thRight}>Win Rate</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((st, i) => (
                <tr key={i} style={{ transition: 'background 0.1s' }}>
                  <td style={s.tdLeft}>{st.strategy_type ?? st.name ?? '—'}</td>
                  <td style={s.tdRight}>{st.trade_count ?? st.trades ?? 0}</td>
                  <td style={{ ...s.tdRight, color: pnlColor(st.total_gross_pnl ?? 0) }}>
                    {fmt(st.total_gross_pnl ?? st.gross_pnl)}
                  </td>
                  <td style={{ ...s.tdRight, color: c.amber }}>{fmt(st.total_fees ?? st.fees)}</td>
                  <td style={{ ...s.tdRight, color: pnlColor(st.total_net_pnl ?? 0) }}>
                    {fmt(st.total_net_pnl ?? st.net_pnl)}
                  </td>
                  <td style={{ ...s.tdRight, color: pnlColor(st.avg_pnl_per_trade ?? 0) }}>
                    {fmt(st.avg_pnl_per_trade ?? st.avg_pnl)}
                  </td>
                  <td style={{ ...s.tdRight, color: c.emerald }}>{pct(st.win_rate)}</td>
                </tr>
              ))}
              {strategies.length === 0 && (
                <tr>
                  <td colSpan={7} style={{ ...s.td, textAlign: 'center', color: c.muted, padding: 32 }}>
                    No strategy data available
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Charts Row */}
      <div style={s.row}>
        {/* Monthly P&L Bar Chart */}
        <div style={{ ...s.card, ...s.flexGrow }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <div style={s.sectionTitle}>Monthly P&L</div>
            <div style={s.toggleRow}>
              <button style={s.toggleBtn(monthlyMode === 'gross')} onClick={() => setMonthlyMode('gross')}>
                Gross
              </button>
              <button style={s.toggleBtn(monthlyMode === 'net')} onClick={() => setMonthlyMode('net')}>
                Net
              </button>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={monthly}>
              <CartesianGrid strokeDasharray="3 3" stroke={c.border} />
              <XAxis dataKey="month" tick={{ fontSize: 11, fill: c.muted }} stroke={c.border} />
              <YAxis tick={{ fontSize: 11, fill: c.muted, fontFamily: mono }} stroke={c.border} />
              <Tooltip content={<CustomTooltip />} />
              <Bar
                dataKey={monthlyMode === 'gross' ? (d => d.gross_pnl ?? d.total_gross_pnl ?? 0) : (d => d.net_pnl ?? d.total_net_pnl ?? 0)}
                name={monthlyMode === 'gross' ? 'Gross P&L' : 'Net P&L'}
                radius={[4, 4, 0, 0]}
              >
                {monthly.map((m, i) => {
                  const val = monthlyMode === 'gross'
                    ? (m.gross_pnl ?? m.total_gross_pnl ?? 0)
                    : (m.net_pnl ?? m.total_net_pnl ?? 0);
                  return <Cell key={i} fill={val >= 0 ? c.emerald : c.red} fillOpacity={0.8} />;
                })}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Win Rate Pie Chart */}
        <div style={{ ...s.card, minWidth: 280, maxWidth: 360 }}>
          <div style={s.sectionTitle}>Win Rate Breakdown</div>
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Pie
                data={pieData}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={95}
                paddingAngle={3}
                dataKey="value"
                stroke="none"
              >
                {pieData.map((_, i) => (
                  <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} fillOpacity={0.85} />
                ))}
              </Pie>
              <Tooltip
                formatter={(v) => pct(v)}
                contentStyle={{
                  background: 'rgba(15,23,42,0.95)',
                  border: `1px solid ${c.border}`,
                  borderRadius: 8,
                  fontFamily: mono,
                  fontSize: 13,
                }}
                itemStyle={{ color: c.text }}
              />
              <Legend
                wrapperStyle={{ fontSize: 12, color: c.muted, fontFamily: sans }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Cumulative P&L Line Chart */}
      <div style={{ ...s.card, marginBottom: 24 }}>
        <div style={s.sectionTitle}>Cumulative P&L</div>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={cumulative}>
            <CartesianGrid strokeDasharray="3 3" stroke={c.border} />
            <XAxis dataKey="month" tick={{ fontSize: 11, fill: c.muted }} stroke={c.border} />
            <YAxis tick={{ fontSize: 11, fill: c.muted, fontFamily: mono }} stroke={c.border} />
            <Tooltip content={<CustomTooltip />} />
            <Legend wrapperStyle={{ fontSize: 12, color: c.muted }} />
            <Line
              type="monotone"
              dataKey="cumGross"
              name="Gross P&L"
              stroke={c.blue}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: c.blue }}
            />
            <Line
              type="monotone"
              dataKey="cumNet"
              name="Net P&L"
              stroke={c.emerald}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: c.emerald }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Bottom Row: Fee Analysis + Top/Worst Performers */}
      <div style={s.row}>
        {/* Fee Analysis */}
        <div style={{ ...s.card, ...s.flexGrow }}>
          <div style={s.sectionTitle}>Fee Analysis</div>
          <div style={s.feeRow}>
            <span style={s.feeLabel}>Total Fees</span>
            <span style={{ ...s.feeValue, color: c.amber }}>{fmt(totalFees)}</span>
          </div>
          <div style={s.feeRow}>
            <span style={s.feeLabel}>Monthly Average</span>
            <span style={s.feeValue}>{fmt(monthlyFees)}</span>
          </div>
          <div style={s.feeRow}>
            <span style={s.feeLabel}>Yearly Total</span>
            <span style={s.feeValue}>{fmt(yearlyFees)}</span>
          </div>
          <div style={s.feeRow}>
            <span style={s.feeLabel}>Fees as % of Gross</span>
            <span style={{ ...s.feeValue, color: feePct > 0.2 ? c.red : c.amber }}>{pct(feePct)}</span>
          </div>
          <div style={{ ...s.feeRow, borderBottom: 'none' }}>
            <span style={s.feeLabel}>Avg Fee / Trade</span>
            <span style={s.feeValue}>{fmt(avgFee)}</span>
          </div>
        </div>

        {/* Top 5 Performers */}
        <div style={{ ...s.card, ...s.flexGrow }}>
          <div style={s.sectionTitle}>Top 5 Performers</div>
          {top5.map((st, i) => (
            <div key={i} style={s.perfItem}>
              <span style={s.perfName}>
                <span style={{ color: c.muted, fontFamily: mono, marginRight: 8, fontSize: 11 }}>#{i + 1}</span>
                {st.strategy_type ?? st.name ?? '—'}
              </span>
              <span style={{ ...s.perfValue, color: c.emerald }}>{fmt(st.total_net_pnl ?? st.net_pnl)}</span>
            </div>
          ))}
          {top5.length === 0 && (
            <div style={{ color: c.muted, fontSize: 13, textAlign: 'center', padding: 24 }}>No data</div>
          )}
        </div>

        {/* Worst 5 Performers */}
        <div style={{ ...s.card, ...s.flexGrow }}>
          <div style={s.sectionTitle}>Worst 5 Performers</div>
          {worst5.map((st, i) => (
            <div key={i} style={s.perfItem}>
              <span style={s.perfName}>
                <span style={{ color: c.muted, fontFamily: mono, marginRight: 8, fontSize: 11 }}>#{i + 1}</span>
                {st.strategy_type ?? st.name ?? '—'}
              </span>
              <span style={{ ...s.perfValue, color: c.red }}>{fmt(st.total_net_pnl ?? st.net_pnl)}</span>
            </div>
          ))}
          {worst5.length === 0 && (
            <div style={{ color: c.muted, fontSize: 13, textAlign: 'center', padding: 24 }}>No data</div>
          )}
        </div>
      </div>
    </div>
  );
}
