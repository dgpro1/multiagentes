"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowLeftRight, LoaderCircle, Search, TriangleAlert } from "lucide-react";
import { budgetText, MergePanel, shortDate } from "@/components/merge-leads/lead-panel";
import { useLeadScope } from "@/components/lead-card/scope";
import { useToast } from "@/components/toast";
import { Alert, Modal } from "@/components/ui";
import { api, messageFrom } from "@/lib/api";
import { ChannelDots } from "@/lib/channels";
import { useLanguage, useT } from "@/lib/i18n";
import { tagStyle } from "@/lib/tags";
import type { LeadCard, LeadMergeResult, MergeCandidate } from "@/types";

/** Merges the open lead with another of the same client: search for it, look at the
 * two sides (the older lead is the primary unless the roles are swapped), confirm.
 * `card` is the open lead as the lead card loaded it. Irreversible on the server. */
export function MergeDialog({ card, onClose, onMerged }: {
  card: LeadCard;
  onClose: () => void;
  /** The primary lead after the merge; the host opens it and reloads its list and thread. */
  onMerged: (primary: LeadCard) => void;
}) {
  const t = useT();
  const { lang } = useLanguage();
  const toast = useToast();
  const scope = useLeadScope();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MergeCandidate[] | null>(null);
  const [searchError, setSearchError] = useState("");
  const [picked, setPicked] = useState<MergeCandidate | null>(null);
  const [swapped, setSwapped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // The list follows the search box after a short pause; an empty box lists the most recent leads.
  useEffect(() => {
    if (picked) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ q: query.trim(), exclude: card.conversation_id });
      api<MergeCandidate[]>(`${scope.leadsBase}/leads/merge-candidates?${params}`)
        .then((rows) => { if (!cancelled) { setResults(rows); setSearchError(""); } })
        .catch((err) => { if (!cancelled) { setResults([]); setSearchError(messageFrom(err)); } });
    }, query.trim() ? 300 : 0);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query, picked, scope.leadsBase, card.conversation_id]);

  const openChannels = useMemo(() => {
    const linked = (card.linked_channels ?? []).map((item) => item.channel);
    return [...new Set(linked.length ? linked : [card.channel])];
  }, [card]);

  // The older lead is the primary by default; the open lead wins a tie or an unknown date.
  const openIsPrimary = useMemo(() => {
    if (!picked) return true;
    const older = !card.created_at || Date.parse(card.created_at) <= Date.parse(picked.created_at);
    return swapped ? !older : older;
  }, [card.created_at, picked, swapped]);

  async function confirm() {
    if (!picked || busy) return;
    const open = card.conversation_id;
    setBusy(true);
    setError("");
    try {
      const result = await api<LeadMergeResult>(`${scope.leadsBase}/leads/merge`, {
        method: "POST",
        body: JSON.stringify({ primary_conversation_id: openIsPrimary ? open : picked.conversation_id, secondary_conversation_id: openIsPrimary ? picked.conversation_id : open }),
      });
      toast.success(t("lead.merge.merged"));
      onMerged(result.primary);
    } catch (err) {
      setError(messageFrom(err));
      setBusy(false);
    }
  }

  const money = (value: number | null) => budgetText(value, card.currency, lang);
  const open = { number: card.number, name: card.contact.name, budget: money(card.deal_value), created: shortDate(card.created_at, lang), channels: openChannels };
  const other = picked && { number: picked.number, name: picked.contact_name, budget: money(picked.deal_value), created: shortDate(picked.created_at, lang), channels: picked.channels?.length ? picked.channels : [picked.channel] };
  const primary = openIsPrimary ? open : other;
  const secondary = openIsPrimary ? other : open;

  return <Modal open title={t("lead.merge.title")} description={picked ? undefined : t("lead.merge.pickCopy", { number: card.number })} onClose={onClose} wide>
    {!picked ? <div className="modal-form merge-search">
      <label className="merge-search-box"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("lead.merge.searchPlaceholder")} aria-label={t("lead.merge.searchPlaceholder")} autoFocus autoComplete="off" /></label>
      {searchError && <Alert>{t("lead.merge.searchFailed")} {searchError}</Alert>}
      <ul className="merge-results" aria-label={t("lead.merge.title")}>
        {results === null && <li className="merge-empty"><LoaderCircle className="spin" size={15} /> {t("lead.merge.searching")}</li>}
        {results?.length === 0 && !searchError && <li className="merge-empty">{t("lead.merge.noResults")}</li>}
        {results?.map((row) => <li key={row.conversation_id}><button type="button" className="merge-candidate" onClick={() => { setPicked(row); setSwapped(false); setError(""); }}>
          <span className="merge-candidate-main">
            <span className="merge-candidate-line"><span className="lead-number">#{row.number}</span><strong>{row.contact_name || t("lead.merge.noName")}</strong></span>
            <small>{[row.phone, row.email].filter(Boolean).join(" · ") || "—"}</small>
          </span>
          <span className="merge-candidate-side">
            <ChannelDots channels={row.channels?.length ? row.channels : [row.channel]} t={t} />
            <span className="merge-candidate-stage" style={row.stage ? tagStyle(row.stage.color) : undefined}><i className="tag-dot" />{row.stage ? row.stage.name : t("lead.merge.noStage")}</span>
            <small>{money(row.deal_value) ?? t("lead.merge.noBudget")} · {shortDate(row.created_at, lang)}</small>
          </span>
        </button></li>)}
      </ul>
      <div className="modal-actions"><button type="button" className="button" onClick={onClose}>{t("common.cancel")}</button></div>
    </div> : <div className="modal-form merge-preview">
      <div className="merge-compare">
        {primary && <MergePanel role="primary" {...primary} />}
        {secondary && <MergePanel role="secondary" {...secondary} />}
      </div>
      <button type="button" className="button merge-swap" onClick={() => setSwapped((v) => !v)}><ArrowLeftRight size={15} /> {t("lead.merge.swap")}</button>
      <div className="merge-effects">
        <strong>{t("lead.merge.effectsTitle")}</strong>
        <ul>
          <li>{t("lead.merge.effectChannels")}</li>
          <li>{t("lead.merge.effectHistory")}</li>
          <li>{t("lead.merge.effectPipeline")}</li>
          <li>{t("lead.merge.effectFields")}</li>
        </ul>
      </div>
      <div className="merge-warning" role="alert"><TriangleAlert size={16} /><span>{t("lead.merge.irreversible")}</span></div>
      {error && <Alert>{error}</Alert>}
      <div className="modal-actions">
        <button type="button" className="button" onClick={() => { setPicked(null); setError(""); }} disabled={busy}><ArrowLeft size={15} /> {t("lead.merge.back")}</button>
        <button type="button" className="button danger" onClick={confirm} disabled={busy}>{busy ? <><LoaderCircle className="spin" size={15} /> {t("lead.merge.merging")}</> : t("lead.merge.confirm")}</button>
      </div>
    </div>}
  </Modal>;
}
