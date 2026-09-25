"use client";

import { ChannelDots } from "@/lib/channels";
import { formatMoney } from "@/lib/currencies";
import { useT } from "@/lib/i18n";

/** A budget in the client's currency; without one (an old audit entry) the bare amount, or null when there is none. */
export function budgetText(value: number | null | undefined, currency: string | null | undefined, locale: string): string | null {
  if (value === null || value === undefined) return null;
  return currency ? formatMoney(value, currency, locale) : value.toLocaleString(locale, { maximumFractionDigits: 2 });
}

/** A date without the time, in the app's language. */
export function shortDate(iso: string | null | undefined, locale: string): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
}

/** One side of a merge: which role it plays, the lead's number, budget, creation date and channels.
 * The secondary side is struck through and dimmed, since it is the one that goes away. */
export function MergePanel({ role, number, name, budget, created, channels }: {
  role: "primary" | "secondary";
  number: number;
  name?: string | null;
  budget: string | null;
  created: string | null;
  channels: string[];
}) {
  const t = useT();
  return <div className={`merge-panel ${role}`}>
    <span className="merge-panel-role">{role === "primary" ? t("lead.merge.primary") : t("lead.merge.secondary")}</span>
    <strong className="merge-panel-title">{t("lead.title", { number })}</strong>
    {name && <span className="merge-panel-name">{name}</span>}
    <span className="merge-panel-fact"><small>{t("lead.merge.budget")}</small><span>{budget ?? t("lead.merge.noBudget")}</span></span>
    {created && <small className="merge-panel-created">{t("lead.merge.created", { date: created })}</small>}
    {channels.length > 0 && <ChannelDots channels={channels} t={t} />}
  </div>;
}
