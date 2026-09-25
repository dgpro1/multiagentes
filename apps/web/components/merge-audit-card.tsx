"use client";

import { useState } from "react";
import { ChevronDown, GitMerge } from "lucide-react";
import { budgetText, MergePanel, shortDate } from "@/components/merge-leads/lead-panel";
import { formatTime } from "@/lib/datetime";
import { useLanguage, useT } from "@/lib/i18n";
import type { Message } from "@/types";

/** True for the thread entry a merge leaves behind (rendered by MergeAuditCard instead of a bubble). */
export function isMergeActivity(message: Message): boolean {
  return message.kind === "activity" && message.activity?.event === "entity_merged";
}

/** The audit card of a merge, shown in the primary lead's thread: who merged which
 * leads, the two sides as they were, and what the merge did. `currency` is the
 * client's, for entries whose sides do not carry their own. */
export function MergeAuditCard({ message, currency }: { message: Message; currency?: string | null }) {
  const t = useT();
  const { lang } = useLanguage();
  const [more, setMore] = useState(false);
  const details = message.activity;
  const primary = details?.primary;
  const secondary = details?.secondary;
  const primaryNumber = primary?.number ?? details?.primary_number ?? 0;
  const secondaryNumber = secondary?.number ?? details?.secondary_number ?? 0;
  const when = `${shortDate(message.created_at, lang) ?? ""} ${formatTime(message.created_at, lang)}`.trim();
  return <div className="merge-audit" role="group" aria-label={t("lead.merge.audit.text", { primary: primaryNumber, secondary: secondaryNumber })}>
    <div className="merge-audit-head">
      <span className="merge-audit-icon"><GitMerge size={15} /></span>
      <span className="merge-audit-who">{message.sender_name && <strong>{message.sender_name}</strong>}<time>{when}</time></span>
    </div>
    <p className="merge-audit-intro">{t("lead.merge.audit.intro")}</p>
    <div className="merge-compare">
      <MergePanel role="primary" number={primaryNumber} name={primary?.name} budget={budgetText(primary?.price, primary?.currency ?? currency, lang)} created={shortDate(primary?.created_at, lang)} channels={primary?.channels ?? []} />
      <MergePanel role="secondary" number={secondaryNumber} name={secondary?.name} budget={budgetText(secondary?.price, secondary?.currency ?? currency, lang)} created={shortDate(secondary?.created_at, lang)} channels={secondary?.channels ?? []} />
    </div>
    <button type="button" className="merge-audit-more" aria-expanded={more} onClick={() => setMore((v) => !v)}>
      {more ? t("lead.merge.audit.less") : t("lead.merge.audit.more")}<ChevronDown size={14} className={more ? "open" : ""} />
    </button>
    {more && <ul className="merge-audit-list">
      <li>{t("lead.merge.audit.linked", { primary: primaryNumber, secondary: secondaryNumber })}</li>
      <li>{t("lead.merge.audit.unified")}</li>
    </ul>}
  </div>;
}
