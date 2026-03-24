import React, { useState } from 'react';
import { setPermission } from '../api';

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
    padding: 32,
    width: '100%',
    maxWidth: 480,
    margin: 16,
    boxShadow: '0 24px 64px rgba(0,0,0,0.5)',
  },
  title: {
    fontSize: 20,
    fontWeight: 700,
    color: c.text,
    marginBottom: 20,
    textAlign: 'center',
  },
  warningBox: {
    background: `${c.red}12`,
    border: `1px solid ${c.red}40`,
    borderRadius: 12,
    padding: 20,
    marginBottom: 24,
  },
  warningTitle: {
    fontSize: 14,
    fontWeight: 700,
    color: c.red,
    marginBottom: 10,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  warningText: {
    fontSize: 13,
    color: c.text,
    lineHeight: 1.7,
  },
  warningItem: {
    fontSize: 13,
    color: c.text,
    lineHeight: 1.7,
    paddingLeft: 16,
    position: 'relative',
  },
  buttons: {
    display: 'flex',
    gap: 12,
    marginTop: 8,
  },
  stayBtn: {
    flex: 1,
    padding: '12px 20px',
    borderRadius: 12,
    border: `1px solid ${c.border}`,
    background: 'transparent',
    color: c.muted,
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: sans,
    transition: 'background 0.15s ease',
  },
  grantBtn: {
    flex: 1,
    padding: '12px 20px',
    borderRadius: 12,
    border: 'none',
    background: c.red,
    color: '#fff',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: sans,
    transition: 'opacity 0.15s ease',
  },
};

export default function PermissionGate({ open, onClose, onGranted }) {
  const [submitting, setSubmitting] = useState(false);

  if (!open) return null;

  const handleGrant = async () => {
    setSubmitting(true);
    try {
      await setPermission({ mode: 'EXECUTE' });
      if (onGranted) onGranted();
    } catch {
      // stay open on error
    }
    setSubmitting(false);
  };

  return (
    <div style={s.overlay} onClick={onClose}>
      <div style={s.modal} onClick={e => e.stopPropagation()}>
        <div style={s.title}>Switch to Execute Mode</div>

        {/* Warning Box */}
        <div style={s.warningBox}>
          <div style={s.warningTitle}>
            <span style={{ fontSize: 18 }}>⚠</span>
            Real Orders Warning
          </div>
          <div style={s.warningText}>
            Switching from <span style={{ fontFamily: mono, color: c.emerald, fontWeight: 600 }}>READONLY</span> to{' '}
            <span style={{ fontFamily: mono, color: c.red, fontWeight: 600 }}>EXECUTE</span> mode
            means the system will place <strong>real orders</strong> with your broker.
          </div>
          <div style={{ marginTop: 14 }}>
            <div style={s.warningItem}>
              <span style={{ position: 'absolute', left: 0, color: c.red }}>•</span>
              Live buy/sell orders will be sent to the exchange
            </div>
            <div style={s.warningItem}>
              <span style={{ position: 'absolute', left: 0, color: c.red }}>•</span>
              Real margin will be blocked in your trading account
            </div>
            <div style={s.warningItem}>
              <span style={{ position: 'absolute', left: 0, color: c.red }}>•</span>
              GTT (stop-loss) orders will be placed automatically
            </div>
            <div style={s.warningItem}>
              <span style={{ position: 'absolute', left: 0, color: c.red }}>•</span>
              You are responsible for all financial outcomes
            </div>
          </div>
        </div>

        <div style={{ fontSize: 13, color: c.muted, textAlign: 'center', marginBottom: 20, lineHeight: 1.6 }}>
          By granting execute permission, you acknowledge that you understand the risks
          involved and accept full responsibility for all trades placed by the system.
        </div>

        {/* Action Buttons */}
        <div style={s.buttons}>
          <button
            style={s.stayBtn}
            onClick={onClose}
            disabled={submitting}
          >
            Stay Read-Only
          </button>
          <button
            style={{
              ...s.grantBtn,
              opacity: submitting ? 0.6 : 1,
              cursor: submitting ? 'not-allowed' : 'pointer',
            }}
            onClick={handleGrant}
            disabled={submitting}
          >
            {submitting ? 'Granting...' : 'Grant Execute Permission'}
          </button>
        </div>
      </div>
    </div>
  );
}
