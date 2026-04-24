"""CSS constants — single source of truth. Injected once in main()."""

CUSTOM_CSS = """
<style>
:root {
  --brand:#1E6B4A; --brand-light:#E1F5EE; --brand-mid:#BAE0CA; --brand-text:#085041;
  --danger-bg:#FEF2F2; --danger-text:#7F1D1D; --danger-border:#FECACA;
  --warning-bg:#FFFBEB; --warning-text:#78350F; --warning-border:#FDE68A;
  --success-bg:#F0FDF4; --success-text:#14532D; --success-border:#BBF7D0;
  --border:rgba(0,0,0,.07); --muted:#6B7280; --surface:#F9FAFB;
}

.block-container { padding-top: 1.5rem !important; }

.finding-card {
  border: 1px solid var(--border); border-radius: 12px; overflow: hidden;
  margin-bottom: 12px; background: #fff;
  box-shadow: 0 1px 4px rgba(0,0,0,.05); transition: box-shadow .2s ease;
}
.finding-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,.09); }
.finding-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px; border-bottom: 1px solid var(--border); background: var(--surface);
}
.finding-body { padding: 14px 16px; }
.field-label {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .6px; color: var(--muted); margin-bottom: 3px;
}
.field-value { font-size: 13px; line-height: 1.65; margin-bottom: 12px; }

.law-chip {
  display: inline-block; background: var(--brand-light); color: var(--brand-text);
  font-size: 11px; font-weight: 600; padding: 3px 10px;
  border-radius: 20px; border: 1px solid var(--brand-mid);
}

.fix-box {
  background: var(--brand-light); color: var(--brand-text); font-size: 12px;
  padding: 10px 14px; border-radius: 8px; border-left: 3px solid var(--brand);
  line-height: 1.6; margin-top: 4px;
}

.badge-high { background: var(--danger-bg);  color: var(--danger-text);  border: 1px solid var(--danger-border);  }
.badge-med  { background: var(--warning-bg); color: var(--warning-text); border: 1px solid var(--warning-border); }
.badge-low  { background: var(--success-bg); color: var(--success-text); border: 1px solid var(--success-border); }
.badge-high, .badge-med, .badge-low {
  font-size: 10px; font-weight: 700; padding: 2px 9px;
  border-radius: 20px; letter-spacing: .3px;
}

.status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.dot-on    { background: #1E6B4A; box-shadow: 0 0 0 3px rgba(30,107,74,.15); }
.dot-off   { background: #E24B4A; box-shadow: 0 0 0 3px rgba(226,75,74,.15); }
.dot-amber { background: #D97706; box-shadow: 0 0 0 3px rgba(217,119,6,.15); }
.dot-muted { background: #D1D5DB; }

.metric-box {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 16px 18px; text-align: center;
}
.metric-label-sm {
  font-size: 11px; color: var(--muted); margin-bottom: 6px;
  font-weight: 500; letter-spacing: .3px; text-transform: uppercase;
}
.metric-val-lg { font-size: 24px; font-weight: 700; line-height: 1.2; }
</style>
"""
