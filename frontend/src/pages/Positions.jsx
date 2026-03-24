import React, { useState, useEffect, useCallback } from 'react';
import {
  getPositions,
  closePosition,
  rollPosition,
  getAdjustments,
  executeAdjustment,
  getPermission,
  getRiskStatus,
} from '../api';

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

function statusMeta(status) {
  switch (status) {
    case 'SAFE':
      return { color: colors.emerald, bg: 'rgba(110,231,183,0.12)' };
    case 'AT_RISK':
      return { color: colors.amber, bg: 'rgba(252,211,77,0.12)' };
    case 'ITM':
      return { color: colors.red, bg: 'rgba(248,113,113,0.12)' };
    case 'EXPIRING_TODAY':
      return { color: colors.amber, bg: 'rgba(252,211,77,0.12)' };
    default:
      return { color: colors.muted, bg: 'rgba(148,163,184,0.08)' };
  }
}

function deriveStatus(position) {
  if (position.status) return position.status;
  const absDelta = Math.abs(position.delta || 0);
  if (position.daysToExpiry === 0) return 'EXPIRING_TODAY';
  if (absDelta > 0.5) return 'ITM';
  if (absDelta >= 0.3) return 'AT_RISK';
  return 'SAFE';
}

function marginColor(pct) {
  if (pct < 60) return colors.emerald;
  if (pct < 80) return colors.amber;
  return colors.red;
}

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

function fmtPct(n) {
  if (n == null) return '—';
  return Number(n).toFixed(1) + '%';
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
    fontSize: 24,
    fontWeight: 700,
    marginBottom: 24,
    color: colors.text,
  },
  riskBar: {
    display: 'flex',
    gap: 16,
    marginBottom: 20,
    flexWrap: 'wrap',
  },
  riskChip: {
    flex: '1 1 200px',
    backgroundColor: colors.card,
    border: `1px solid ${colors.border}`,
    borderRadius: 10,
    padding: '14px 20px',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  riskLabel: {
    fontSize: 12,
    color: colors.muted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  riskValue: {
    fontSize: 20,
    fontFamily: fontMono,
    fontWeight: 600,
  },
  plCard: {
    backgroundColor: colors.card,
    border: `1px solid ${colors.border}`,
    borderRadius: 12,
    padding: '18px 24px',
    marginBottom: 20,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  plLabel: {
    fontSize: 14,
    color: colors.muted,
  },
  plValue: {
    fontSize: 28,
    fontFamily: fontMono,
    fontWeight: 700,
  },
  banner: {
    backgroundColor: 'rgba(248,113,113,0.1)',
    border: `1px solid rgba(248,113,113,0.25)`,
    borderRadius: 8,
    padding: '10px 16px',
    marginBottom: 20,
    fontSize: 13,
    color: colors.red,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
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
  th: {
    textAlign: 'left',
    padding: '12px 14px',
    fontSize: 11,
    fontWeight: 600,
    color: colors.muted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    borderBottom: `1px solid ${colors.border}`,
    whiteSpace: 'nowrap',
  },
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
    letterSpacing: 0.3,
  }),
  btn: (variant) => ({
    padding: '6px 14px',
    borderRadius: 6,
    fontSize: 12,
    fontWeight: 600,
    fontFamily: fontSans,
    cursor: 'pointer',
    border: 'none',
    ...(variant === 'danger'
      ? { backgroundColor: 'rgba(248,113,113,0.15)', color: colors.red }
      : variant === 'primary'
      ? { backgroundColor: 'rgba(56,189,248,0.15)', color: colors.blue }
      : { backgroundColor: 'rgba(148,163,184,0.1)', color: colors.muted }),
  }),
  btnDisabled: {
    opacity: 0.4,
    cursor: 'not-allowed',
  },
  adjustPanel: {
    backgroundColor: 'rgba(15,23,42,0.5)',
    borderTop: `1px solid ${colors.border}`,
    padding: '16px 14px',
  },
  adjustGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
    gap: 12,
  },
  adjustCard: (selected) => ({
    backgroundColor: selected ? 'rgba(56,189,248,0.08)' : 'rgba(15,23,42,0.6)',
    border: `1px solid ${selected ? colors.blue : colors.border}`,
    borderRadius: 10,
    padding: '14px 16px',
    cursor: 'pointer',
    transition: 'border-color 0.15s',
  }),
  adjustTitle: {
    fontSize: 14,
    fontWeight: 600,
    marginBottom: 8,
    color: colors.text,
  },
  adjustDetail: {
    fontSize: 12,
    color: colors.muted,
    lineHeight: 1.6,
  },
  spinner: {
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    minHeight: 300,
    color: colors.muted,
    fontSize: 14,
  },
  actionRow: {
    display: 'flex',
    gap: 6,
  },
};

// ── Components ──

function RiskSummaryBar({ risk }) {
  const mu = risk?.marginUtilization ?? 0;
  return (
    <div style={s.riskBar}>
      <div style={s.riskChip}>
        <span style={s.riskLabel}>Portfolio Delta</span>
        <span style={{ ...s.riskValue, color: plColor(risk?.totalDelta) }}>
          {fmt(risk?.totalDelta, 3)}
        </span>
      </div>
      <div style={s.riskChip}>
        <span style={s.riskLabel}>Margin Utilization</span>
        <span style={{ ...s.riskValue, color: marginColor(mu) }}>
          {fmtPct(mu)}
        </span>
      </div>
      <div style={s.riskChip}>
        <span style={s.riskLabel}>Daily Loss Limit</span>
        <span
          style={{
            ...s.riskValue,
            color: risk?.dailyLossBreached ? colors.red : colors.emerald,
          }}
        >
          {risk?.dailyLossBreached ? 'BREACHED' : 'OK'}
        </span>
      </div>
    </div>
  );
}

function StatusBadge({ status }) {
  return <span style={s.badge(statusMeta(status))}>{status.replace('_', ' ')}</span>;
}

function AdjustmentPanel({ positionId, onExecute }) {
  const [adjustments, setAdjustments] = useState(null);
  const [selected, setSelected] = useState(null);
  const [executing, setExecuting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAdjustments(positionId).then((data) => {
      if (!cancelled) setAdjustments(data);
    });
    return () => { cancelled = true; };
  }, [positionId]);

  const handleExecute = async () => {
    if (!selected) return;
    setExecuting(true);
    try {
      await executeAdjustment(positionId, selected.id);
      onExecute?.();
    } finally {
      setExecuting(false);
    }
  };

  if (!adjustments) {
    return (
      <div style={s.adjustPanel}>
        <span style={{ color: colors.muted, fontSize: 13 }}>Loading adjustments...</span>
      </div>
    );
  }

  return (
    <div style={s.adjustPanel}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, color: colors.text }}>
        Adjustment Options
      </div>
      <div style={s.adjustGrid}>
        {adjustments.map((adj) => (
          <div
            key={adj.id}
            style={s.adjustCard(selected?.id === adj.id)}
            onClick={() => setSelected(adj)}
          >
            <div style={s.adjustTitle}>{adj.name}</div>
            <div style={s.adjustDetail}>
              <div>Cost: <span style={{ fontFamily: fontMono, color: plColor(-adj.cost) }}>{fmt(-adj.cost)}</span></div>
              <div>Fees: <span style={{ fontFamily: fontMono }}>{fmt(-adj.fees)}</span></div>
              <div>Prob. profit: <span style={{ fontFamily: fontMono, color: colors.emerald }}>{fmtPct(adj.probability)}</span></div>
            </div>
          </div>
        ))}
      </div>
      {selected && (
        <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center' }}>
          <button
            style={{
              ...s.btn('primary'),
              ...(executing ? s.btnDisabled : {}),
            }}
            disabled={executing}
            onClick={handleExecute}
          >
            {executing ? 'Executing...' : `Execute: ${selected.name}`}
          </button>
          <button style={s.btn('ghost')} onClick={() => setSelected(null)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

function PositionRow({ pos, canClose, canRoll, onRefresh }) {
  const [expanded, setExpanded] = useState(false);
  const [closing, setClosing] = useState(false);
  const [rolling, setRolling] = useState(false);
  const status = deriveStatus(pos);
  const meta = statusMeta(status);
  const needsAdjust = status === 'AT_RISK' || status === 'ITM';

  const leftBorder =
    status === 'AT_RISK'
      ? `3px solid ${colors.amber}`
      : status === 'ITM'
      ? `3px solid ${colors.red}`
      : '3px solid transparent';

  const handleClose = async (e) => {
    e.stopPropagation();
    setClosing(true);
    try {
      await closePosition(pos.id);
      onRefresh?.();
    } finally {
      setClosing(false);
    }
  };

  const handleRoll = async (e) => {
    e.stopPropagation();
    setRolling(true);
    try {
      await rollPosition(pos.id);
      onRefresh?.();
    } finally {
      setRolling(false);
    }
  };

  return (
    <>
      <tr
        style={{ borderLeft: leftBorder, cursor: needsAdjust ? 'pointer' : 'default' }}
        onClick={() => needsAdjust && setExpanded(!expanded)}
      >
        <td style={s.td}>{pos.symbol}</td>
        <td style={s.td}>{pos.strategy}</td>
        <td style={{ ...s.td, color: colors.muted }}>{pos.legs}</td>
        <td style={s.tdMono}>{fmt(pos.entryPremium)}</td>
        <td style={s.tdMono}>{fmt(pos.currentValue)}</td>
        <td style={{ ...s.tdMono, color: plColor(pos.unrealizedPL) }}>
          {fmt(pos.unrealizedPL)}
        </td>
        <td style={{ ...s.tdMono, color: colors.muted }}>{pos.daysHeld}</td>
        <td style={s.td}>{pos.expiry}</td>
        <td style={{ ...s.tdMono, color: colors.muted }}>{pos.delta?.toFixed(3)}</td>
        <td style={s.td}>
          <StatusBadge status={status} />
        </td>
        <td style={s.td}>
          <span style={{ fontSize: 12, color: pos.gttActive ? colors.emerald : colors.muted }}>
            {pos.gttActive ? 'Active' : 'None'}
          </span>
        </td>
        <td style={s.td}>
          <div style={s.actionRow}>
            <button
              style={{
                ...s.btn('danger'),
                ...((!canClose || closing) ? s.btnDisabled : {}),
              }}
              disabled={!canClose || closing}
              onClick={handleClose}
            >
              {closing ? '...' : 'Close'}
            </button>
            <button
              style={{
                ...s.btn('primary'),
                ...((!canRoll || rolling) ? s.btnDisabled : {}),
              }}
              disabled={!canRoll || rolling}
              onClick={handleRoll}
            >
              {rolling ? '...' : 'Roll'}
            </button>
          </div>
        </td>
      </tr>
      {expanded && needsAdjust && (
        <tr>
          <td colSpan={12} style={{ padding: 0 }}>
            <AdjustmentPanel positionId={pos.id} onExecute={onRefresh} />
          </td>
        </tr>
      )}
    </>
  );
}

export default function Positions() {
  const [positions, setPositions] = useState([]);
  const [risk, setRisk] = useState(null);
  const [permissions, setPermissions] = useState({});
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [posData, riskData, permData] = await Promise.all([
        getPositions(),
        getRiskStatus(),
        getPermission(),
      ]);
      setPositions(posData ?? []);
      setRisk(riskData);
      setPermissions(permData ?? {});
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const totalPL = positions.reduce((sum, p) => sum + (p.unrealizedPL || 0), 0);
  const hasExpiring = positions.some((p) => deriveStatus(p) === 'EXPIRING_TODAY');
  const hasItm = positions.some((p) => deriveStatus(p) === 'ITM');

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
            Loading positions...
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={s.page}>
      <h1 style={s.header}>Open Positions</h1>

      <RiskSummaryBar risk={risk} />

      <div style={s.plCard}>
        <span style={s.plLabel}>Total Unrealized P&amp;L</span>
        <span style={{ ...s.plValue, color: plColor(totalPL) }}>{fmt(totalPL)}</span>
      </div>

      {(hasExpiring || hasItm) && (
        <div style={s.banner}>
          <span style={{ fontSize: 16 }}>&#9888;</span>
          Close ITM positions before 3:25 PM to avoid exercise STT
        </div>
      )}

      <div style={s.tableWrap}>
        <table style={s.table}>
          <thead>
            <tr>
              <th style={s.th}>Symbol</th>
              <th style={s.th}>Strategy</th>
              <th style={s.th}>Legs</th>
              <th style={s.th}>Entry Premium</th>
              <th style={s.th}>Current Value</th>
              <th style={s.th}>Unrealized P&amp;L</th>
              <th style={s.th}>Days Held</th>
              <th style={s.th}>Expiry</th>
              <th style={s.th}>Delta</th>
              <th style={s.th}>Status</th>
              <th style={s.th}>GTT</th>
              <th style={s.th}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={12} style={{ ...s.td, textAlign: 'center', color: colors.muted, padding: 48 }}>
                  No open positions
                </td>
              </tr>
            ) : (
              positions.map((pos) => (
                <PositionRow
                  key={pos.id}
                  pos={pos}
                  canClose={permissions.canClose}
                  canRoll={permissions.canRoll}
                  onRefresh={fetchData}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
