import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  scan, getRecommendations, getArbitrage,
  setRiskProfile, getRiskProfile, getPermission, getStatus,
  createManualTrade, refreshPrices, getSentiment,
} from '../api';
import {
  Search, ChevronDown, ChevronUp, Lock, Unlock,
  Shield, TrendingUp, AlertTriangle, Activity,
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
  orange: '#fb923c',
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
const STRATEGY_TYPES = ['ALL', 'SHORT_STRANGLE', 'ATM_SHORT_STRANGLE', 'IRON_CONDOR', 'RSI_OPTION_SELL', 'CALENDAR_SPREAD', 'COVERED_CALL', 'CASH_SECURED_PUT', 'PUT_CREDIT_SPREAD', 'ARBITRAGE'];
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
  SHORT_STRANGLE: '#f472b6',  // pink
  ATM_SHORT_STRANGLE: '#e879f9', // purple-pink
  IRON_CONDOR: '#34d399',     // teal
  RSI_OPTION_SELL: '#fb923c', // orange
  CALENDAR_SPREAD: '#22d3ee', // cyan
  COVERED_CALL: C.purple,
  CASH_SECURED_PUT: C.blue,
  PUT_CREDIT_SPREAD: C.amber,
  ARBITRAGE: C.emerald,
};

const VIX_COLORS = {
  LOW: C.emerald,
  NORMAL: C.amber,
  HIGH: C.orange,
  EXTREME: C.red,
};

const DELTA_BIAS_LABELS = {
  LONG_HEAVY: { label: 'Long Heavy', color: C.blue },
  SLIGHTLY_LONG: { label: 'Slightly Long', color: C.emerald },
  NEUTRAL: { label: 'Neutral', color: C.muted },
  SLIGHTLY_SHORT: { label: 'Slightly Short', color: C.amber },
  SHORT_HEAVY: { label: 'Short Heavy', color: C.red },
};

const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 }));
const fmtCur = (n) => (n == null ? '—' : '₹' + fmt(n));
const fmtPct = (n) => {
  if (n == null) return '—';
  const v = Number(n);
  // API returns prob_otm as 0-1 decimal, annualized_return as percentage
  return v <= 1 && v >= -1 ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(1)}%`;
};

/* ─── Badge component ─── */
const Badge = ({ children, color, style = {} }) => (
  <span style={{
    padding: '4px 10px', borderRadius: 999, fontSize: 11, fontWeight: 700,
    background: `${color}20`, color, letterSpacing: 0.3, whiteSpace: 'nowrap',
    ...style,
  }}>
    {children}
  </span>
);

/* ─── Metric cell ─── */
const Metric = ({ label, value, color = C.text, tooltip }) => (
  <div>
    <div style={{ fontSize: 11, color: C.muted, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4, cursor: tooltip ? 'help' : 'default' }} title={tooltip}>
      {label}
      {tooltip && <span style={{ marginLeft: 4, fontSize: 10, color: 'rgba(148,163,184,0.5)', cursor: 'help' }} title={tooltip}>&#9432;</span>}
    </div>
    <div style={{ fontFamily: font.mono, fontSize: 14, fontWeight: 600, color }}>{value}</div>
  </div>
);

/* ─── Recommendation Card ─── */
function RecCard({ rec, idx, expanded, onToggle }) {
  const id = rec.id || `${rec.symbol}-${rec.strike}-${idx}`;
  const safetyColor = SAFETY_COLORS[rec.safety || rec.safety_tag] || C.muted;
  const typeColor = TYPE_COLORS[rec.strategy || rec.strategy_type] || C.muted;
  const isCoveredCall = (rec.strategy || rec.strategy_type) === 'COVERED_CALL';
  const exitSug = rec.exit_suggestion;
  const charges = rec.charges_breakdown;

  const [executed, setExecuted] = useState(false);
  const [executing, setExecuting] = useState(false);

  const handleMarkExecuted = async (r) => {
    setExecuting(true);
    try {
      const leg = r.legs?.[0] || {};
      await createManualTrade({
        symbol: r.symbol,
        strategy_type: r.strategy || r.strategy_type,
        tradingsymbol: leg.tradingsymbol || `${r.symbol}${r.expiry_display}${leg.strike}${leg.option_type}`,
        action: leg.action || 'SELL',
        strike: leg.strike,
        option_type: leg.option_type,
        expiry_date: r.expiry_date || r.expiry,
        entry_premium: leg.premium || 0,
        quantity: leg.quantity || 0,
        lots: r.lots || 1,
        lot_size: r.lot_size,
        rec_data: {
          net_premium: r.net_premium,
          margin: r.margin_needed || r.margin,
          exit_suggestion: r.exit_suggestion,
          charges: r.total_charges,
        },
      });
      setExecuted(true);
    } catch (e) {
      setExecuting(false);
    }
  };

  const deltaImpactColor = rec.delta_impact != null
    ? (Math.abs(rec.delta_impact) > 2 ? C.red : Math.abs(rec.delta_impact) > 1 ? C.amber : C.emerald)
    : C.muted;

  return (
    <div style={{ ...cardStyle, padding: 0, overflow: 'hidden', opacity: rec.decision === 'REJECT' ? 0.5 : 1 }}>
      {/* ── Card Header (clickable) ── */}
      <div
        onClick={() => onToggle(id)}
        style={{
          padding: '20px 24px',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexWrap: 'wrap',
          transition: 'background 0.15s',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(148,163,184,0.04)')}
        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
      >
        {/* Rank */}
        <span style={{
          width: 32, height: 32, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: `${C.purple}20`, color: C.purple, fontFamily: font.mono, fontSize: 14, fontWeight: 700, flexShrink: 0,
        }}>
          {rec.rank || idx + 1}
        </span>

        {/* Symbol */}
        <span style={{ fontWeight: 700, fontSize: 16, minWidth: 80 }}>{rec.symbol}</span>

        {/* Strategy badge */}
        <Badge color={typeColor}>
          {(rec.strategy || rec.strategy_type || '').replace(/_/g, ' ')}
        </Badge>

        {/* Safety badge */}
        <Badge color={safetyColor}>
          {(rec.safety || rec.safety_tag || '').replace(/_/g, ' ')}
        </Badge>

        {/* Spacer */}
        <span style={{ flex: 1 }} />

        {/* Net Premium */}
        <span style={{ fontFamily: font.mono, fontWeight: 700, fontSize: 18, color: C.emerald }}>
          {/* Decision badge */}
          <span style={{
            padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 700, marginRight: 8,
            background: rec.decision === 'GO' ? 'rgba(110,231,183,0.15)' : rec.decision === 'REJECT' ? 'rgba(248,113,113,0.15)' : 'rgba(252,211,77,0.15)',
            color: rec.decision === 'GO' ? C.emerald : rec.decision === 'REJECT' ? C.red : C.amber,
          }}>
            {rec.decision || 'REVIEW'}
          </span>
          NET {fmtCur(rec.net_credit_adjusted || rec.net_premium || rec.premium)}
        </span>

        {expanded ? <ChevronUp size={18} style={{ color: C.muted }} /> : <ChevronDown size={18} style={{ color: C.muted }} />}
      </div>

      {/* ── Metrics Row 1 ── */}
      <div style={{
        padding: '0 24px 14px',
        display: 'grid',
        gridTemplateColumns: 'repeat(5, 1fr)',
        gap: 12,
      }}>
        <Metric
          label="Net Premium (after slippage)"
          value={rec.net_credit_adjusted ? `${fmtCur(rec.net_credit_adjusted)} (raw: ${fmtCur(rec.net_premium)})` : fmtCur(rec.net_premium)}
          color={C.emerald}
          tooltip="Net income after charges and 10% slippage buffer. Raw = before slippage."
        />
        <Metric label="Max Loss" value={fmtCur(rec.max_loss)} color={C.red} tooltip="Maximum possible loss if option expires in-the-money" />
        <Metric label="Prob OTM" value={fmtPct(rec.prob_otm)} color={C.emerald} tooltip="Probability the option expires out-of-the-money (worthless) — you keep the premium" />
        <Metric
          label={rec.risk_adjusted_return != null && rec.risk_factor < 1 ? "Risk-Adj. Return" : "True Ann. Return"}
          value={rec.risk_adjusted_return != null && rec.risk_factor < 1
            ? `${fmtPct(rec.risk_adjusted_return)} (raw: ${fmtPct(rec.annualized_return)})`
            : fmtPct(rec.annualized_return)}
          color={C.amber}
          tooltip={`Annualized return after charges${rec.risk_factor < 1 ? `. Adjusted by ${(rec.risk_factor * 100).toFixed(0)}% for ${rec.sentiment_signal || 'market'} sentiment` : ''}`}
        />
        <Metric label="Expiry" value={rec.expiry_display ? `${rec.expiry_display} (${rec.dte}d)` : rec.dte ? `${rec.dte}d` : '—'} color={C.muted} tooltip="Option expiry date from NSE (includes holiday adjustments)" />
      </div>

      {/* ── Metrics Row 2 ── */}
      <div style={{
        padding: '0 24px 16px',
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: 12,
      }}>
        <Metric label="Theta/Day" value={rec.theta_per_day_rupees != null ? fmtCur(rec.theta_per_day_rupees) : '—'} color={C.blue} tooltip="Daily time decay in rupees — amount you earn each day from theta" />
        <Metric
          label="Margin Required"
          value={rec.margin_needed || rec.margin ? `${fmtCur(rec.margin_needed || rec.margin)} (${rec.margin_pct_of_available != null ? `${rec.margin_pct_of_available}%` : '—'})` : '—'}
          color={rec.capital_warning ? C.red : C.text}
          tooltip="Estimated margin required to hold this position"
        />
        <div>
          <div style={{ fontSize: 11, color: C.muted, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4, cursor: 'help' }} title="How this trade affects your portfolio's directional exposure">Delta Impact <span style={{ fontSize: 10, color: 'rgba(148,163,184,0.5)' }}>&#9432;</span></div>
          <Badge color={deltaImpactColor} style={{ fontSize: 13 }}>
            {rec.delta_impact === 'REDUCES_DELTA' ? 'Reduces' : rec.delta_impact === 'ADDS_DELTA' ? 'Adds' : rec.delta_impact || '—'}
          </Badge>
        </div>
        <Metric label="R:R Ratio" value={rec.risk_reward_ratio != null ? `1:${Number(rec.risk_reward_ratio).toFixed(1)}` : '—'} color={C.text} tooltip="Risk to reward — ratio of max profit to max loss" />
      </div>

      {/* ── Trade Legs (always visible) ── */}
      {rec.legs && rec.legs.length > 0 && (
        <div style={{ padding: '0 24px 16px' }}>
          <div style={{ fontSize: 11, color: C.muted, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>Trade Legs</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {rec.legs.map((leg, li) => {
              const action = (leg.action || leg.side || '').toUpperCase();
              const isSell = action === 'SELL';
              // Show human-readable: "NIFTY 27 MAR 22300 PE" using expiry_display from rec
              const expDisplay = leg.expiry_display || rec.expiry_display || '';
              const instrument = `${rec.symbol} ${expDisplay} ${Math.round(leg.strike || 0)} ${leg.option_type || ''}`.trim();
              const expiryPart = '';  // Already included in instrument
              const lotsLabel = rec.lots ? ` (${rec.lots} lot${rec.lots > 1 ? 's' : ''})` : '';
              return (
                <div key={li} style={{
                  display: 'flex', gap: 12, alignItems: 'center',
                  padding: '10px 16px', borderRadius: 10,
                  background: isSell ? 'rgba(248,113,113,0.06)' : 'rgba(110,231,183,0.06)',
                  border: `1px solid ${isSell ? 'rgba(248,113,113,0.15)' : 'rgba(110,231,183,0.15)'}`,
                }}>
                  <span style={{
                    padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 700,
                    background: isSell ? `${C.red}20` : `${C.emerald}20`,
                    color: isSell ? C.red : C.emerald,
                    minWidth: 36, textAlign: 'center',
                  }}>
                    {action}
                  </span>
                  <span>
                    <span style={{ fontWeight: 600, fontSize: 14 }}>{instrument}{expiryPart}</span>
                    {leg.tradingsymbol && (
                      <span style={{ fontSize: 10, color: C.muted, marginLeft: 8, fontFamily: font.mono }}>
                        ({leg.tradingsymbol})
                      </span>
                    )}
                  </span>
                  <span style={{ fontFamily: font.mono, fontSize: 13, color: C.muted }}>
                    Qty: {leg.quantity}{lotsLabel}
                  </span>
                  <span style={{ fontFamily: font.mono, fontSize: 14, fontWeight: 600, color: isSell ? C.emerald : C.red, marginLeft: 'auto' }}>
                    {fmtCur(leg.premium)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Exit Guidance (always visible) ── */}
      {exitSug && (
        <div style={{ padding: '0 24px 16px' }}>
          <div style={{
            padding: '10px 16px', borderRadius: 10,
            background: 'rgba(56,189,248,0.06)',
            border: `1px solid rgba(56,189,248,0.15)`,
            fontSize: 13, color: C.blue, lineHeight: 1.6,
          }}>
            Target: Exit by <strong>{exitSug.target_exit_date || '—'}</strong> or buy back at <strong>{fmtCur(exitSug.target_exit_premium)}/share</strong>
            {exitSug.notes && <div style={{ marginTop: 4, fontSize: 12, opacity: 0.85 }}>{exitSug.notes}</div>}
          </div>
          {exitSug.gamma_warning && (
            <div style={{
              marginTop: 6, padding: '8px 16px', borderRadius: 10,
              background: 'rgba(248,113,113,0.1)',
              border: `1px solid rgba(248,113,113,0.25)`,
              fontSize: 13, fontWeight: 600, color: C.red,
            }}>
              ⚠ Expiry risk — exit today
            </div>
          )}
        </div>
      )}

      {/* ── Mark as Executed ── */}
      <div style={{ padding: '0 24px 16px' }}>
        <label style={{
          display: 'inline-flex', alignItems: 'center', gap: 10, cursor: executed ? 'default' : 'pointer',
          padding: '8px 16px', borderRadius: 10,
          background: executed ? 'rgba(110,231,183,0.12)' : 'rgba(148,163,184,0.06)',
          border: `1px solid ${executed ? 'rgba(110,231,183,0.3)' : C.border}`,
          fontSize: 13, fontWeight: 500, color: executed ? C.emerald : C.muted,
          opacity: executing ? 0.6 : 1,
        }}>
          <input
            type="checkbox"
            checked={executed}
            disabled={executed || executing}
            onChange={() => !executed && handleMarkExecuted(rec)}
            style={{ accentColor: C.emerald, width: 16, height: 16, cursor: 'pointer' }}
          />
          {executed ? 'Executed — Tracking this trade' : executing ? 'Saving...' : 'I executed this trade'}
        </label>
      </div>

      {/* ── Sentiment Note (per card) ── */}
      {rec.sentiment_note && (
        <div style={{
          padding: '4px 24px 8px', fontSize: 11, fontStyle: 'italic',
          color: rec.sentiment_signal === 'RED' ? C.red : rec.sentiment_signal === 'GREEN' ? C.emerald : C.amber,
        }}>
          {rec.sentiment_signal === 'RED' ? '⚠ ' : rec.sentiment_signal === 'GREEN' ? '✓ ' : '◆ '}
          {rec.sentiment_note}
        </div>
      )}

      {/* ── Confidence Reasons ── */}
      {rec.confidence_reasons && rec.confidence_reasons.length > 0 && (
        <div style={{ padding: '0 24px 8px', fontSize: 11, color: C.muted }}>
          {rec.confidence_reasons.map((r, i) => <span key={i} style={{ marginRight: 8 }}>• {r}</span>)}
        </div>
      )}

      {/* ── Price Source (always visible) ── */}
      <div style={{ padding: '0 24px 8px', display: 'flex', gap: 12, fontSize: 11, fontFamily: font.mono, color: C.muted }}>
        <span>
          Prices: <span style={{ color: rec.price_source === 'kite' ? C.emerald : C.amber }}>
            {rec.price_source === 'kite' ? 'Kite (Live)' : 'Simulated (Black-Scholes)'}
          </span>
        </span>
        {rec.fetched_at && (
          <span>
            Fetched: {new Date(rec.fetched_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
        {rec.bs_derived && rec.bs_derived.length > 0 && (
          <span style={{ color: C.amber }}>
            BS-derived: {rec.bs_derived.join(', ')}
          </span>
        )}
        {rec.execution_quality && (
          <span style={{ color: rec.execution_quality === 'GOOD' ? C.emerald : rec.execution_quality === 'FAIR' ? C.amber : C.red }}>
            Execution: {rec.execution_quality}
          </span>
        )}
      </div>

      {/* ── Holding Context (for covered calls, always visible) ── */}
      {isCoveredCall && rec.holding_qty != null && (
        <div style={{ padding: '0 24px 16px' }}>
          <div style={{
            padding: '10px 16px', borderRadius: 10,
            background: `${C.purple}08`,
            border: `1px solid ${C.purple}20`,
            fontSize: 13, color: C.purple, lineHeight: 1.6,
            fontFamily: font.mono,
          }}>
            You hold {fmt(rec.holding_qty)} shares @ {fmtCur(rec.avg_cost)}
            {rec.unrealized_pnl != null && (
              <> | Unrealized P&L: <span style={{ color: rec.unrealized_pnl >= 0 ? C.emerald : C.red }}>{fmtCur(rec.unrealized_pnl)}</span></>
            )}
            {rec.lots_possible != null && <> | {rec.lots_possible} lot{rec.lots_possible !== 1 ? 's' : ''} possible</>}
          </div>
        </div>
      )}

      {/* ── Expanded: Charges + Risk Detail ── */}
      {expanded && (
        <div style={{ borderTop: `1px solid ${C.border}`, padding: 24 }}>
          {/* Charges breakdown */}
          {(rec.gross_premium != null || charges) && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 11, color: C.muted, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>Charges Breakdown</div>
              <div style={{
                display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
                fontFamily: font.mono, fontSize: 13,
              }}>
                <span style={{ color: C.text }}>Gross {fmtCur(rec.gross_premium)}</span>
                {charges && (
                  <>
                    <span style={{ color: C.muted }}>→</span>
                    <span style={{ color: C.muted }}>Brokerage {fmtCur(charges.brokerage)}</span>
                    <span style={{ color: C.muted }}>→</span>
                    <span style={{ color: C.muted }}>STT {fmtCur(charges.stt)}</span>
                    <span style={{ color: C.muted }}>→</span>
                    <span style={{ color: C.muted }}>Exchange {fmtCur(charges.exchange_charges)}</span>
                    <span style={{ color: C.muted }}>→</span>
                    <span style={{ color: C.muted }}>GST {fmtCur(charges.gst)}</span>
                  </>
                )}
                <span style={{ color: C.muted }}>→</span>
                <span style={{ color: C.emerald, fontWeight: 700 }}>Net {fmtCur(rec.net_premium)}</span>
              </div>
            </div>
          )}

          {/* Risk detail */}
          <div style={{ display: 'flex', gap: 24, fontSize: 13, flexWrap: 'wrap', marginBottom: 20 }}>
            <span style={{ color: C.muted }}>
              Max profit: <span style={{ fontFamily: font.mono, color: C.emerald, fontWeight: 600 }}>{fmtCur(rec.max_profit)}</span>
            </span>
            <span style={{ color: C.muted }}>
              Max loss: <span style={{ fontFamily: font.mono, color: C.red, fontWeight: 600 }}>{fmtCur(rec.max_loss)}</span>
              {rec.max_loss_point != null && <> at {fmtCur(rec.max_loss_point)}</>}
            </span>
            <span style={{ color: C.muted }}>
              Risk:Reward <span style={{ fontFamily: font.mono, color: C.text, fontWeight: 600 }}>
                {rec.risk_reward_ratio != null ? `1:${Number(rec.risk_reward_ratio).toFixed(1)}` : '—'}
              </span>
            </span>
            {rec.loss_as_pct_of_margin != null && (
              <span style={{ color: C.muted }}>
                Loss as % of margin: <span style={{ fontFamily: font.mono, color: C.red, fontWeight: 600 }}>{fmtPct(rec.loss_as_pct_of_margin)}</span>
              </span>
            )}
          </div>

          {/* VIX context */}
          {rec.vix_at_scan != null && (
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 16 }}>
              VIX at scan: <span style={{ fontFamily: font.mono, color: C.text }}>{rec.vix_at_scan}</span>
              {rec.vix_signal && <> ({rec.vix_signal})</>}
            </div>
          )}

          {/* Strike Rationale */}
          {(rec.strike_rationale || rec.rationale) && (
            <div style={{ fontSize: 13, color: C.muted, lineHeight: 1.6, fontStyle: 'italic', marginBottom: 20 }}>
              {rec.strike_rationale || rec.rationale}
            </div>
          )}

          {/* Risk Ladder (alternatives) */}
          {rec.alternatives && Object.keys(rec.alternatives).length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 12, color: C.muted, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>Alternative Strikes</div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                {Object.entries(rec.alternatives).map(([profile, alt]) => {
                  const profileMeta = RISK_PROFILES.find((r) => r.key === profile);
                  const color = profileMeta?.color || C.muted;
                  return (
                    <div
                      key={profile}
                      style={{
                        background: 'rgba(148,163,184,0.05)',
                        border: `1px solid ${C.border}`,
                        borderRadius: 12,
                        padding: '12px 16px',
                        minWidth: 160,
                      }}
                    >
                      <div style={{ fontSize: 12, fontWeight: 700, color, marginBottom: 6, textTransform: 'capitalize' }}>
                        {profile}
                      </div>
                      <div style={{ fontFamily: font.mono, fontSize: 14, fontWeight: 600, marginBottom: 4 }}>
                        Strike {fmtCur(alt.strike)}
                      </div>
                      <div style={{ display: 'flex', gap: 16, fontSize: 12, color: C.muted }}>
                        <span>Prem: <span style={{ color: C.emerald, fontFamily: font.mono }}>{fmtCur(alt.premium)}</span></span>
                        <span>Prob: <span style={{ color: C.text, fontFamily: font.mono }}>{fmtPct(alt.prob_otm / 100)}</span></span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Execute button */}
          <button
            disabled={!false}
            style={{
              ...btnBase,
              padding: '12px 28px',
              fontSize: 15,
              background: 'rgba(148,163,184,0.15)',
              color: C.muted,
              cursor: 'not-allowed',
            }}
          >
            <Lock size={16} /> Read-Only Mode
          </button>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════ */
export default function Scanner() {
  /* ─── State ─── */
  const [allRecommendations, setAllRecommendations] = useState([]);
  const [coveredCalls, setCoveredCalls] = useState([]);
  const [arbitrage, setArbitrage] = useState([]);
  const [vix, setVix] = useState(null);
  const [portfolioRisk, setPortfolioRisk] = useState(null);
  const [riskProfile, setRiskProfileState] = useState('MODERATE');
  const [permission, setPermission] = useState('READONLY');
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState(null);
  const [expandedId, setExpandedId] = useState(null);

  // Filters
  const [safetyFilter, setSafetyFilter] = useState('ALL');
  const [strategyFilter, setStrategyFilter] = useState('ALL');

  // Summary
  const [totalWeeklyIncome, setTotalWeeklyIncome] = useState(null);
  const [totalMarginRequired, setTotalMarginRequired] = useState(null);
  const [dataSource, setDataSource] = useState(null);  // "kite" or "simulation"
  const [sentiment, setSentiment] = useState(null);
  const [marketStatus, setMarketStatus] = useState(null);

  /* ─── Init — only scan if no cached results ─── */
  useEffect(() => {
    getRiskProfile().then((d) => setRiskProfileState(d?.profile || d?.risk_profile || 'MODERATE')).catch(() => {});
    getPermission().then((d) => setPermission(d?.mode || d?.permission || 'READONLY')).catch(() => {});
    getSentiment().then(setSentiment).catch(() => {});

    // Only auto-scan if no results are loaded yet
    if (allRecommendations.length === 0 && !scanning) {
      getStatus().then((st) => {
        if ((st?.holdings_count ?? 0) > 0) {
          handleScan();
        }
      }).catch(() => {});
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* ─── Handlers ─── */
  const handleScan = useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      const scanData = await scan();
      const recs = scanData?.recommendations || [];
      const cc = scanData?.covered_calls || [];
      const arbs = scanData?.arbitrage || [];

      setAllRecommendations(recs);
      setCoveredCalls(cc);
      setArbitrage(arbs);
      setVix(scanData?.vix || null);
      setPortfolioRisk(scanData?.portfolio_risk || null);
      setTotalWeeklyIncome(scanData?.total_weekly_income ?? null);
      setTotalMarginRequired(scanData?.total_margin_required ?? null);
      setDataSource(scanData?.data_source || null);
      setMarketStatus(scanData?.market_status || null);
    } catch (e) {
      setError(e.message);
    } finally {
      setScanning(false);
    }
  }, []);

  /* ─── Auto-refresh prices every 5 seconds for displayed recs ─── */
  const refreshIntervalRef = useRef(null);

  useEffect(() => {
    // Only refresh when we have recommendations and data source is kite
    if (allRecommendations.length === 0 || dataSource !== 'kite') {
      if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current);
      return;
    }

    const doRefresh = async () => {
      try {
        // Collect all tradingsymbols from visible recs
        const symbols = new Set();
        [...allRecommendations, ...coveredCalls].forEach(r => {
          (r.legs || []).forEach(leg => {
            if (leg.tradingsymbol) symbols.add(leg.tradingsymbol);
          });
        });
        if (symbols.size === 0) return;

        const result = await refreshPrices([...symbols]);
        if (!result?.prices) return;

        // Update premiums in recommendations
        const updateRec = (rec) => {
          let updated = false;
          const newLegs = (rec.legs || []).map(leg => {
            const ts = leg.tradingsymbol;
            if (ts && result.prices[ts]) {
              updated = true;
              return { ...leg, premium: result.prices[ts].ltp };
            }
            return leg;
          });
          if (updated) {
            return { ...rec, legs: newLegs, fetched_at: result.fetched_at };
          }
          return rec;
        };

        setAllRecommendations(prev => prev.map(updateRec));
        setCoveredCalls(prev => prev.map(updateRec));
      } catch {
        // Silently fail — don't disrupt the UI
      }
    };

    refreshIntervalRef.current = setInterval(doRefresh, 5000);
    return () => { if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current); };
  }, [allRecommendations.length, coveredCalls.length, dataSource]);

  const handleRiskChange = async (profile) => {
    try {
      await setRiskProfile({ profile });
      setRiskProfileState(profile);
    } catch (e) { setError(e.message); }
  };

  // Client-side filtering (avoids stateless container issues)
  const recommendations = allRecommendations.filter((r) => {
    if (safetyFilter !== 'ALL' && (r.safety || r.safety_tag) !== safetyFilter) return false;
    if (strategyFilter !== 'ALL' && (r.strategy || r.strategy_type) !== strategyFilter) return false;
    return true;
  });

  const filteredCoveredCalls = coveredCalls.filter((r) => {
    if (safetyFilter !== 'ALL' && (r.safety || r.safety_tag) !== safetyFilter) return false;
    return true;
  });

  const toggleExpand = (id) => setExpandedId((prev) => (prev === id ? null : id));

  // VIX color
  const vixColor = vix ? (VIX_COLORS[vix.signal] || C.amber) : C.muted;

  // Delta bias
  const deltaBias = portfolioRisk?.delta_bias
    ? (DELTA_BIAS_LABELS[portfolioRisk.delta_bias] || { label: portfolioRisk.delta_bias, color: C.muted })
    : null;

  // Bottom summary
  const availableMargin = portfolioRisk?.available_margin;
  const marginPct = (availableMargin && totalMarginRequired)
    ? ((totalMarginRequired / availableMargin) * 100).toFixed(1)
    : null;

  /* ─── Render ─── */
  return (
    <div style={{ minHeight: '100vh', background: C.bg, color: C.text, fontFamily: font.sans, padding: '32px 24px' }}>
      <div style={{ maxWidth: 1280, margin: '0 auto' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 28 }}>
          <h1 style={{ fontSize: 28, fontWeight: 700, margin: 0 }}>Scanner</h1>
          {dataSource && (
            <span style={{
              fontSize: 12, fontWeight: 600, padding: '4px 12px', borderRadius: 999,
              fontFamily: font.mono,
              background: dataSource === 'kite' ? 'rgba(110,231,183,0.12)' : 'rgba(252,211,77,0.12)',
              color: dataSource === 'kite' ? C.emerald : C.amber,
              border: `1px solid ${dataSource === 'kite' ? 'rgba(110,231,183,0.3)' : 'rgba(252,211,77,0.3)'}`,
            }}>
              {dataSource === 'kite' ? 'LIVE — Kite API' : 'SIMULATION — Black-Scholes'}
            </span>
          )}
          {!dataSource && !scanning && (
            <span style={{ fontSize: 12, color: C.muted }}>Click "Scan Now" to analyze</span>
          )}
          {marketStatus && (
            <span style={{
              fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 999,
              fontFamily: font.mono,
              background: marketStatus.is_open ? 'rgba(110,231,183,0.12)' : 'rgba(252,211,77,0.12)',
              color: marketStatus.is_open ? C.emerald : C.amber,
            }}>
              {marketStatus.message} ({marketStatus.time})
            </span>
          )}
        </div>

        {/* Market Briefing — actionable for trading decisions */}
        {sentiment && (
          <div style={{
            ...cardStyle, marginBottom: 20, padding: 16,
            borderLeft: `4px solid ${sentiment.color}`,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: sentiment.color, boxShadow: `0 0 6px ${sentiment.color}60` }} />
              <span style={{ fontSize: 14, fontWeight: 700, color: sentiment.color }}>
                {sentiment.signal === 'GREEN' ? 'Market Favourable — good for selling premium' :
                 sentiment.signal === 'RED' ? 'Market Risky — reduce exposure or skip' :
                 'Mixed Signals — proceed with caution'}
              </span>
              <span style={{ fontSize: 11, fontFamily: font.mono, color: C.muted, marginLeft: 'auto' }}>
                Score: {sentiment.score}/100
              </span>
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
              {(sentiment.factors || []).map((f, i) => (
                <span key={i} style={{
                  padding: '3px 10px', borderRadius: 6, fontSize: 11, fontFamily: font.mono,
                  background: f.signal === 'GREEN' ? 'rgba(110,231,183,0.1)' : f.signal === 'RED' ? 'rgba(248,113,113,0.1)' : 'rgba(252,211,77,0.1)',
                  color: f.signal === 'GREEN' ? C.emerald : f.signal === 'RED' ? C.red : C.amber,
                }}>
                  {f.name}: {typeof f.value === 'string' ? f.value.slice(0, 40) : f.value}
                </span>
              ))}
            </div>
            {/* US Event Warnings — directly actionable */}
            {sentiment.us_events?.warnings?.map((w, i) => (
              <div key={i} style={{
                padding: '8px 12px', borderRadius: 8, marginBottom: 6,
                background: w.level === 'RED' ? 'rgba(248,113,113,0.1)' : 'rgba(252,211,77,0.08)',
                fontSize: 12, color: w.level === 'RED' ? C.red : C.amber,
              }}>
                {w.level === 'RED' ? '⚠' : '⚡'} {w.message} — <em>{w.recommendation}</em>
              </div>
            ))}
            <div style={{ fontSize: 11, color: C.muted, fontStyle: 'italic' }}>
              {sentiment.signal === 'GREEN' ? 'Scan results below are suitable for fresh positions.' :
               sentiment.signal === 'RED' ? 'Consider skipping new positions or using minimum lot sizes only.' :
               'Review each recommendation carefully before executing.'}
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div style={{ ...cardStyle, borderColor: C.red, marginBottom: 20, padding: 16, color: C.red, fontSize: 14 }}>
            {error}
            <span onClick={() => setError(null)} style={{ float: 'right', cursor: 'pointer', fontWeight: 700 }}>✕</span>
          </div>
        )}

        {/* ═══ HEADER BAR — 4 summary cards ═══ */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 28 }}>
          {/* Safe Weekly Income */}
          <div style={cardStyle}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Safe Weekly Income (Net)</div>
            <div style={{ fontSize: 22, fontWeight: 700, fontFamily: font.mono, color: C.emerald }}>
              {totalWeeklyIncome != null ? fmtCur(totalWeeklyIncome) : '—'}
            </div>
          </div>

          {/* Margin Required */}
          <div style={cardStyle}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Margin Required</div>
            <div style={{ fontSize: 22, fontWeight: 700, fontFamily: font.mono, color: C.amber }}>
              {totalMarginRequired != null ? fmtCur(totalMarginRequired) : '—'}
            </div>
          </div>

          {/* India VIX */}
          <div style={cardStyle}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>India VIX</div>
            <div style={{ fontSize: 22, fontWeight: 700, fontFamily: font.mono, color: vixColor }}>
              {vix ? vix.label : '—'}
            </div>
            {vix?.recommendation && (
              <div style={{ fontSize: 11, color: C.muted, marginTop: 6, lineHeight: 1.4 }}>{vix.recommendation}</div>
            )}
          </div>

          {/* Portfolio Delta */}
          <div style={cardStyle}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Portfolio Delta</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ fontSize: 22, fontWeight: 700, fontFamily: font.mono, color: C.text }}>
                {portfolioRisk?.portfolio_delta != null ? portfolioRisk.portfolio_delta.toFixed(1) : '—'}
              </span>
              {deltaBias && (
                <Badge color={deltaBias.color}>{deltaBias.label}</Badge>
              )}
            </div>
          </div>
        </div>

        {/* ═══ FILTERS + CONTROLS ═══ */}
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
            {allRecommendations.length > 0 && (
              <button
                onClick={handleScan}
                disabled={scanning}
                style={{
                  ...btnBase,
                  padding: '10px 20px',
                  fontSize: 13,
                  background: 'rgba(148,163,184,0.1)',
                  color: C.muted,
                }}
              >
                Refresh Prices
              </button>
            )}
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

        {/* ═══ RECOMMENDATION CARDS ═══ */}
        {!scanning && recommendations.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginBottom: 32 }}>
            {recommendations.map((rec, idx) => (
              <RecCard
                key={rec.id || `${rec.symbol}-${rec.strike}-${idx}`}
                rec={rec}
                idx={idx}
                expanded={expandedId === (rec.id || `${rec.symbol}-${rec.strike}-${idx}`)}
                onToggle={toggleExpand}
              />
            ))}
          </div>
        )}

        {/* ═══ COVERED CALLS SECTION ═══ */}
        {!scanning && filteredCoveredCalls.length > 0 && (
          <div style={{ marginBottom: 32 }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16,
              padding: '12px 0', borderBottom: `1px solid ${C.border}`,
            }}>
              <Activity size={20} style={{ color: C.purple }} />
              <h2 style={{ fontSize: 20, fontWeight: 700, margin: 0, color: C.purple }}>
                Covered Calls — From Your Holdings
              </h2>
              <Badge color={C.purple}>{filteredCoveredCalls.length}</Badge>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {filteredCoveredCalls.map((rec, idx) => (
                <RecCard
                  key={rec.id || `cc-${rec.symbol}-${rec.strike}-${idx}`}
                  rec={rec}
                  idx={idx}
                  expanded={expandedId === (rec.id || `cc-${rec.symbol}-${rec.strike}-${idx}`)}
                  onToggle={toggleExpand}
                />
              ))}
            </div>
          </div>
        )}

        {/* Empty state */}
        {!scanning && recommendations.length === 0 && filteredCoveredCalls.length === 0 && (
          <div style={{ ...cardStyle, textAlign: 'center', padding: 64 }}>
            <Search size={40} style={{ color: C.muted, marginBottom: 16 }} />
            <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>No recommendations yet</div>
            <div style={{ fontSize: 14, color: C.muted }}>
              Import your holdings, set your risk profile, then hit "Scan Now" to find opportunities.
            </div>
          </div>
        )}

        {/* ═══ BOTTOM SUMMARY BAR ═══ */}
        {!scanning && (recommendations.length > 0 || filteredCoveredCalls.length > 0) && (
          <div style={{
            ...cardStyle,
            marginTop: 8,
            padding: '16px 24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 12,
          }}>
            <div style={{ fontSize: 14, fontFamily: font.mono }}>
              <span style={{ color: C.muted }}>All trades combined: </span>
              <span style={{ color: C.emerald, fontWeight: 700 }}>Net income {fmtCur(totalWeeklyIncome)}</span>
              <span style={{ color: C.muted }}> | </span>
              <span style={{ color: C.amber, fontWeight: 700 }}>Margin needed {fmtCur(totalMarginRequired)}</span>
              {marginPct != null && (
                <span style={{ color: C.muted }}> ({marginPct}% of free capital)</span>
              )}
            </div>
            {portfolioRisk?.over_deployment_warning && (
              <div style={{
                padding: '6px 14px', borderRadius: 8,
                background: `${C.red}15`, color: C.red,
                fontSize: 13, fontWeight: 700,
              }}>
                ⚠ Over-deployment warning: margin usage exceeds safe threshold
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
