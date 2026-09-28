"use client";

// The − / number / + control for "how many lines". `null` means no limit, which
// is exactly how an absent quota (agency) or allocation (client) is stored, so
// the same control serves the platform's Plan tab and the agency's Channels tab.
// The consumption line stays with each caller: its wording differs.

import { useT } from "@/lib/i18n";

export function QuotaStepper({
  value,
  disabled = false,
  onChange,
}: {
  value: number | null;
  disabled?: boolean;
  onChange: (value: number | null) => void;
}) {
  const t = useT();
  const unlimited = value === null;
  const buttonStyle = { padding: "2px 9px", lineHeight: 1.2 } as const;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <button
        type="button"
        className="button"
        style={buttonStyle}
        aria-label={t("channels.quota.fewer")}
        disabled={disabled || unlimited}
        onClick={() => onChange(Math.max(0, (value ?? 0) - 1))}
      >
        −
      </button>
      <span
        style={{
          minWidth: 34,
          textAlign: "center",
          fontWeight: 700,
          color: unlimited ? "var(--muted)" : "var(--ink)",
        }}
      >
        {unlimited ? "∞" : value}
      </span>
      <button
        type="button"
        className="button"
        style={buttonStyle}
        aria-label={t("channels.quota.more")}
        disabled={disabled || unlimited}
        onClick={() => onChange((value ?? 0) + 1)}
      >
        +
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onChange(unlimited ? 0 : null)}
        style={{
          marginLeft: 2,
          background: "none",
          border: "none",
          padding: 0,
          fontSize: 12,
          cursor: disabled ? "default" : "pointer",
          color: disabled ? "var(--muted)" : "var(--accent, #0d9488)",
        }}
      >
        {unlimited ? t("channels.quota.setLimit") : t("channels.quota.unlimited")}
      </button>
    </div>
  );
}
