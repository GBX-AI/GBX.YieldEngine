import React, { useState, useEffect, useRef, useCallback } from 'react';
import { execute } from '../api';

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
  overlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.7)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
    backdropFilter: 'blur(4px)',
  },
  modal: {
    background: c.bg,
    border: `1px solid ${c.border}`,
    borderRadius: 20,
    padding: 0,
    width: '100%',
    maxWidth: 560,
    margin: 16,
    maxHeight: '90vh',
    overflowY: 'auto',
    boxShadow: '0 24px 64px rgba(0,0,0,0.5)',
  },
  modalHeader: {
    padding: '24px 28px 16px',
    borderBottom: `1px solid ${c.border}`,
  },
  title: {
    fontSize: 20,
    fontWeight: 700,
    color: c.text,
  },
  section: {
    padding: '20px 28px',
    borderBottom: `1px solid ${c.border}`,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: c.muted,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    marginBottom: 14,
  },
  legRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '8px 0',
    borderBottom: `1px solid ${c.border}`,
  },
  badge: {
    display: 'inline-block',
    padding: '3px 10px',
    borderRadius: 20,
    fontSize: 11,
    fontWeight: 600,
    fontFamily: mono,
  },
  summaryRow: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '8px 0',
  },
  summaryLabel: {
    fontSize: 13,
    color: c.muted,
  },
  summaryValue: {
    fontSize: 14,
    fontWeight: 600,
    fontFamily: mono,
    color: c.text,
  },
  riskBox: {
    background: `${c.red}12`,
    border: `1px solid ${c.red}40`,
    borderRadius: 12,
    padding: 18,
  },
  riskTitle: {
    fontSize: 13,
    fontWeight: 700,
    color: c.red,
    marginBottom: 12,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  riskItem: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '6px 0',
    borderBottom: `1px solid ${c.red}15`,
  },
  riskLabel: {
    fontSize: 12,
    color: c.text,
  },
  riskValue: {
    fontSize: 13,
    fontWeight: 600,
    fontFamily: mono,
  },
  warningNote: {
    fontSize: 12,
    color: c.amber,
    marginTop: 10,
    lineHeight: 1.5,
    padding: '8px 12px',
    background: `${c.amber}10`,
    borderRadius: 8,
  },
  checkboxRow: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '10px 0',
    cursor: 'pointer',
  },
  checkbox: {
    width: 18,
    height: 18,
    borderRadius: 4,
    border: `2px solid ${c.border}`,
    background: 'transparent',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    marginTop: 1,
    cursor: 'pointer',
  },
  checkboxChecked: {
    width: 18,
    height: 18,
    borderRadius: 4,
    border: `2px solid ${c.emerald}`,
    background: `${c.emerald}20`,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    marginTop: 1,
    cursor: 'pointer',
    color: c.emerald,
    fontSize: 12,
    fontWeight: 700,
  },
  checkLabel: {
    fontSize: 13,
    color: c.text,
    lineHeight: 1.5,
  },
  footer: {
    padding: '20px 28px',
    display: 'flex',
    gap: 12,
  },
  cancelBtn: {
    flex: 1,
    padding: '14px 20px',
    borderRadius: 12,
    border: `1px solid ${c.border}`,
    background: 'transparent',
    color: c.muted,
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: sans,
  },
  confirmBtn: {
    flex: 1,
    padding: '14px 20px',
    borderRadius: 12,
    border: 'none',
    fontSize: 14,
    fontWeight: 600,
    fontFamily: sans,
    transition: 'opacity 0.15s ease',
  },
};

export default function OrderConfirmation({ open, onClose, order, onConfirmed }) {
  const [checks, setChecks] = useState([false, false, false]);
  const [countdown, setCountdown] = useState(5);
  const [submitting, setSubmitting] = useState(false);
  const timerRef = useRef(null);

  const resetState = useCallback(() => {
    setChecks([false, false, false]);
    setCountdown(5);
    setSubmitting(false);
    if (timerRef.current) clearInterval(timerRef.current);
  }, []);

  useEffect(() => {
    if (open) {
      resetState();
      timerRef.current = setInterval(() => {
        setCountdown(prev => {
          if (prev <= 1) {
            clearInterval(timerRef.current);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [open, resetState]);

  if (!open || !order) return null;

  const legs = order.legs || [];
  const totalPremium = order.total_premium ?? order.premium ?? 0;
  const margin = order.margin_required ?? order.margin ?? 0;
  const maxLoss = order.max_loss ?? order.risk?.max_loss ?? '—';
  const fees = order.fees_estimate ?? order.fees ?? '—';
  const whatIf = order.what_if_scenarios || order.scenarios || [];
  const alternatives = order.alternatives || [];
  const exerciseSttWarning = order.exercise_stt_warning ?? order.stt_warning;

  const allChecked = checks.every(Boolean);
  const canConfirm = allChecked && countdown === 0 && !submitting;

  const toggleCheck = (i) => {
    setChecks(prev => prev.map((v, idx) => idx === i ? !v : v));
  };

  const handleConfirm = async () => {
    if (!canConfirm) return;
    setSubmitting(true);
    try {
      const result = await execute(order);
      if (onConfirmed) onConfirmed(result);
    } catch {
      // keep modal open on error
    }
    setSubmitting(false);
  };

  const fmtCurrency = (val) => {
    if (val === '—' || val == null) return '—';
    return `₹${Number(val).toLocaleString('en-IN')}`;
  };

  const checkLabels = [
    'I understand the maximum loss possible on this trade',
    'I understand the margin that will be blocked in my account',
    'I have reviewed the risk disclosure above',
  ];

  return (
    <div style={s.overlay} onClick={onClose}>
      <div style={s.modal} onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div style={s.modalHeader}>
          <div style={s.title}>Confirm Order</div>
        </div>

        {/* Section 1: Order Details */}
        <div style={s.section}>
          <div style={s.sectionTitle}>Order Details</div>
          {legs.map((leg, i) => {
            const isBuy = leg.side === 'BUY' || leg.transaction_type === 'BUY';
            return (
              <div key={i} style={s.legRow}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{
                    ...s.badge,
                    background: isBuy ? `${c.emerald}20` : `${c.red}20`,
                    color: isBuy ? c.emerald : c.red,
                  }}>
                    {leg.side || leg.transaction_type || '—'}
                  </span>
                  <span style={{ fontSize: 13, color: c.text, fontWeight: 500 }}>
                    {leg.tradingsymbol || leg.symbol || leg.instrument}
                  </span>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: 13, fontFamily: mono, color: c.text }}>
                    Qty: {leg.quantity ?? '—'}
                  </div>
                  <div style={{ fontSize: 12, fontFamily: mono, color: c.muted }}>
                    @ ₹{leg.price ?? '—'}
                  </div>
                </div>
              </div>
            );
          })}

          <div style={{ marginTop: 14 }}>
            <div style={s.summaryRow}>
              <span style={s.summaryLabel}>Total Premium</span>
              <span style={{ ...s.summaryValue, color: c.emerald }}>{fmtCurrency(totalPremium)}</span>
            </div>
            <div style={s.summaryRow}>
              <span style={s.summaryLabel}>Margin Required</span>
              <span style={s.summaryValue}>{fmtCurrency(margin)}</span>
            </div>
          </div>
        </div>

        {/* Section 2: Risk Disclosure */}
        <div style={s.section}>
          <div style={s.sectionTitle}>Risk Disclosure</div>
          <div style={s.riskBox}>
            <div style={s.riskTitle}>
              <span style={{ fontSize: 16 }}>⚠</span>
              Understand Before You Proceed
            </div>

            <div style={s.riskItem}>
              <span style={s.riskLabel}>Maximum Loss</span>
              <span style={{ ...s.riskValue, color: c.red }}>
                {typeof maxLoss === 'number' ? fmtCurrency(maxLoss) : maxLoss}
              </span>
            </div>

            {whatIf.length > 0 && whatIf.map((sc, i) => (
              <div key={i} style={s.riskItem}>
                <span style={s.riskLabel}>{sc.label || sc.scenario}</span>
                <span style={{ ...s.riskValue, color: (sc.pnl ?? sc.value ?? 0) >= 0 ? c.emerald : c.red }}>
                  {fmtCurrency(sc.pnl ?? sc.value)}
                </span>
              </div>
            ))}

            <div style={s.riskItem}>
              <span style={s.riskLabel}>Estimated Fees & Charges</span>
              <span style={{ ...s.riskValue, color: c.amber }}>
                {typeof fees === 'number' ? fmtCurrency(fees) : fees}
              </span>
            </div>

            {exerciseSttWarning && (
              <div style={s.warningNote}>
                <strong>Exercise STT Warning:</strong> {typeof exerciseSttWarning === 'string'
                  ? exerciseSttWarning
                  : 'If ITM options are held to expiry, Securities Transaction Tax (STT) on exercise can be significantly higher than normal trading STT. Consider squaring off before expiry.'}
              </div>
            )}

            {alternatives.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: c.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  Alternative Strategies
                </div>
                {alternatives.map((alt, i) => (
                  <div key={i} style={{ ...s.riskItem, borderBottomColor: `${c.blue}15` }}>
                    <span style={{ ...s.riskLabel, color: c.blue }}>{alt.name || alt.strategy}</span>
                    <span style={{ ...s.riskValue, color: c.blue }}>
                      {alt.return ? `${alt.return}%` : alt.description || '—'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Section 3: Safety Checklist */}
        <div style={s.section}>
          <div style={s.sectionTitle}>Safety Checklist</div>
          {checkLabels.map((label, i) => (
            <div key={i} style={s.checkboxRow} onClick={() => toggleCheck(i)}>
              <div style={checks[i] ? s.checkboxChecked : s.checkbox}>
                {checks[i] && '✓'}
              </div>
              <span style={s.checkLabel}>{label}</span>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div style={s.footer}>
          <button style={s.cancelBtn} onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button
            style={{
              ...s.confirmBtn,
              background: canConfirm ? c.emerald : `${c.emerald}30`,
              color: canConfirm ? '#0a0f1a' : `${c.emerald}60`,
              cursor: canConfirm ? 'pointer' : 'not-allowed',
              opacity: submitting ? 0.6 : 1,
            }}
            onClick={handleConfirm}
            disabled={!canConfirm}
          >
            {submitting
              ? 'Placing Order...'
              : countdown > 0
                ? `Confirm (${countdown}s)`
                : allChecked
                  ? 'Confirm Order'
                  : 'Complete Checklist'}
          </button>
        </div>
      </div>
    </div>
  );
}
