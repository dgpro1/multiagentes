"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, BadgeCheck, Bot, CheckCircle2, CircleAlert, ClipboardCopy, LoaderCircle, Plug, Power, RefreshCw, ShieldCheck, Smartphone, Trash2, Webhook } from "lucide-react";
import { Alert, Modal } from "@/components/ui";
import { AccountList } from "@/components/account-list";
import { ConfirmModal } from "@/components/confirm-modal";
import { api, messageFrom } from "@/lib/api";
import { accountName, accountTitle, messagingLimitLabel, qualityLabel, qualityTone, rememberLine, requestedLine } from "@/lib/channels";
import { useT, type I18nKey } from "@/lib/i18n";
import type { Client, WhatsAppCloudChannel } from "@/types";

const stateKeys: Record<WhatsAppCloudChannel["status"], { label: I18nKey; copy: I18nKey }> = {
  disconnected: { label: "clients.whatsappCloud.statusDisconnectedLabel", copy: "clients.whatsappCloud.statusDisconnectedCopy" },
  connected: { label: "clients.whatsappCloud.statusConnectedLabel", copy: "clients.whatsappCloud.statusConnectedCopy" },
  error: { label: "clients.whatsappCloud.statusErrorLabel", copy: "clients.whatsappCloud.statusErrorCopy" },
};

function CopyField({ label, value }: { label: string; value: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  async function copy() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }
  return <div className="wa-copy-field">
    <label>{label}<input readOnly value={value} onFocus={(event) => event.currentTarget.select()} /></label>
    <button type="button" className="button secondary" onClick={copy}><ClipboardCopy size={15} /> {copied ? t("clients.whatsappCloud.copied") : t("clients.whatsappCloud.copy")}</button>
  </div>;
}

/** A client's WhatsApp API numbers. One is selected at a time and the panels
 * below configure that one; "add another number" starts a new one. Numbers
 * are linked on a hosted authorization page opened in a new tab. */
export default function WhatsAppCloudChannelPage() {
  const t = useT();
  const { id } = useParams<{ id: string }>();
  const [client, setClient] = useState<Client | null>(null);
  const [lines, setLines] = useState<WhatsAppCloudChannel[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [agentId, setAgentId] = useState("");
  const [label, setLabel] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refreshNotice, setRefreshNotice] = useState("");
  const [pendingApproval, setPendingApproval] = useState(false);
  const [callbackNotice, setCallbackNotice] = useState<"ready" | "error" | null>(null);
  const [removing, setRemoving] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const channel = adding ? null : lines.find((line) => line.id === selectedId) ?? null;

  const upsert = useCallback((saved: WhatsAppCloudChannel) => {
    setLines((items) => items.some((item) => item.id === saved.id) ? items.map((item) => (item.id === saved.id ? saved : item)) : [...items, saved]);
  }, []);

  /** Open one number, or with `null` the list of them. */
  const show = useCallback((line: WhatsAppCloudChannel | null, owner: Client | null) => {
    setAdding(false);
    setSelectedId(line?.id ?? null);
    setAgentId(line?.agent_id || owner?.agents[0]?.id || "");
    setLabel(line?.label || "");
    setError("");
    rememberLine(line?.id ?? null);
  }, []);
  const startAdding = useCallback((owner: Client | null) => { show(null, owner); setAdding(true); }, [show]);

  useEffect(() => {
    Promise.all([api<Client>(`/clients/${id}`), api<WhatsAppCloudChannel[]>(`/whatsapp-cloud/clients/${id}/channels`)])
      .then(([owner, items]) => {
        setClient(owner); setLines(items);
        const wanted = requestedLine();
        const line = items.find((item) => item.id === wanted.line) ?? null;
        if (wanted.adding) startAdding(owner); else show(line, owner);
        const status = new URLSearchParams(window.location.search).get("messaging_status");
        if (status === "ready" || status === "error") {
          setCallbackNotice(status);
          setPendingApproval(false);
          const clean = new URL(window.location.href);
          clean.searchParams.delete("messaging_status");
          window.history.replaceState(window.history.state, "", clean.pathname + clean.search + clean.hash);
        }
      })
      .catch((err) => setError(messageFrom(err))).finally(() => setLoading(false));
  }, [id, show, startAdding]);

  // The hosted page approves in another tab; refresh when coming back.
  useEffect(() => {
    if (!pendingApproval) return;
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      setPendingApproval(false);
      api<WhatsAppCloudChannel[]>(`/whatsapp-cloud/clients/${id}/channels`)
        .then((items) => {
          setLines(items);
          const line = items.find((item) => item.id === selectedId) ?? null;
          if (line) upsert(line);
        })
        .catch((err) => setError(messageFrom(err)));
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [pendingApproval, id, selectedId, upsert]);

  async function save(): Promise<WhatsAppCloudChannel | null> {
    if (!agentId) return null;
    const payload = JSON.stringify({ agent_id: agentId, label: label.trim() });
    const saved = channel
      ? await api<WhatsAppCloudChannel>(`/whatsapp-cloud/channels/${channel.id}`, { method: "PUT", body: payload })
      : await api<WhatsAppCloudChannel>(`/whatsapp-cloud/clients/${id}/channels`, { method: "POST", body: payload });
    upsert(saved); setAdding(false); setSelectedId(saved.id); rememberLine(saved.id);
    setAgentId(saved.agent_id); setLabel(saved.label || "");
    return saved;
  }

  async function saveOnly() {
    setBusy(true); setError("");
    try { await save(); } catch (err) { setError(messageFrom(err)); } finally { setBusy(false); }
  }

  async function saveAndConnect() {
    setBusy(true); setError(""); setCallbackNotice(null);
    try {
      const saved = await save();
      if (!saved) return;
      const linked = await api<WhatsAppCloudChannel>(`/whatsapp-cloud/channels/${saved.id}/connect`, { method: "POST" });
      upsert(linked);
      if (linked.connect_url) {
        window.open(linked.connect_url, "_blank", "noopener");
        setPendingApproval(true);
      } else {
        setRefreshNotice(t("clients.whatsappCloud.refreshed"));
      }
    } catch (err) { setError(messageFrom(err)); } finally { setBusy(false); }
  }

  /** Re-read the number as the provider has it now: display name, formatting, quality. */
  async function refreshStatus() {
    if (!channel) return;
    setBusy(true); setError(""); setRefreshNotice("");
    try {
      upsert(await api<WhatsAppCloudChannel>(`/whatsapp-cloud/channels/${channel.id}/refresh`, { method: "POST" }));
      setRefreshNotice(t("clients.whatsappCloud.refreshed"));
    } catch (err) { setError(messageFrom(err)); } finally { setBusy(false); }
  }

  async function disconnect() {
    if (!channel) return;
    upsert(await api<WhatsAppCloudChannel>(`/whatsapp-cloud/channels/${channel.id}/disconnect`, { method: "POST" }));
  }

  async function remove() {
    if (!channel) return;
    setBusy(true); setError("");
    try {
      await api(`/whatsapp-cloud/channels/${channel.id}`, { method: "DELETE" });
      const rest = lines.filter((line) => line.id !== channel.id);
      setLines(rest); setRemoving(false);
      // With no numbers left the empty list stays: "add number" is the way in,
      // never an automatic landing on the form.
      show(null, client);
    } catch (err) { setError(messageFrom(err)); } finally { setBusy(false); }
  }

  if (loading || !client) return <div className="page-loading"><LoaderCircle className="spin" /> {t("clients.whatsappCloud.loading")}</div>;
  const state = stateKeys[channel?.status || "disconnected"];
  const canConnect = Boolean(agentId && !busy);
  const nameOf = (line: WhatsAppCloudChannel) => accountName(line, t("clients.whatsappCloud.numberFallback", { n: lines.indexOf(line) + 1 }));
  const listView = !adding && !channel;
  const agentNameOf = (line: WhatsAppCloudChannel) => client.agents.find((agent) => agent.id === line.agent_id)?.name || t("clients.detail.noAgent");
  const rows = lines.map((line) => ({
    id: line.id, title: line.phone_number || line.display_name || nameOf(line), inboxName: nameOf(line),
    agentName: `${t("clients.detail.colAgent")}: ${agentNameOf(line)}`,
    state: line.status === "connected" && line.is_enabled ? "connected" as const : "disconnected" as const,
    stateLabel: t(stateKeys[line.status].label),
  }));
  const connectedCount = rows.filter((row) => row.state === "connected").length;
  return <div className="page wa-page">
    {listView || !lines.length
      ? <Link href={`/clients/${client.id}?tab=channels`} className="back-link"><ArrowLeft size={17} /> {t("clients.whatsapp.back", { name: client.name })}</Link>
      : <button type="button" className="back-link" onClick={() => show(null, client)}><ArrowLeft size={17} /> {t("clients.whatsappCloud.title")}</button>}
    <header className="wa-header"><div className="wa-mark"><BadgeCheck size={26} /></div><div><span>{listView ? t("clients.whatsapp.channelOf", { name: client.name }) : `${t("clients.whatsappCloud.title")} · ${client.name}`}</span><h1>{channel ? accountTitle(channel, nameOf(channel)) : adding ? t("clients.whatsappCloud.newNumber") : t("clients.whatsappCloud.title")}</h1><p>{t("clients.whatsappCloud.headerCopy")}</p></div>{channel && <div className={`wa-state ${channel.status}`}>{channel.status === "connected" ? <CheckCircle2 size={17} /> : channel.status === "error" ? <CircleAlert size={17} /> : <RefreshCw size={17} />} {t(state.label)}</div>}</header>
    {error && <Alert>{error}</Alert>}
    {callbackNotice && <Alert>{t(callbackNotice === "ready" ? "clients.whatsappCloud.approvalReady" : "clients.whatsappCloud.approvalError")}</Alert>}
    {listView && <AccountList rows={rows} summary={lines.length === 1 ? t("clients.detail.channelNumberOne") : t("clients.detail.channelNumbers", { count: lines.length, connected: connectedCount })} addLabel={t("clients.detail.addNumber")} openLabel={t("clients.detail.configure")} onOpen={(lineId) => show(lines.find((line) => line.id === lineId) ?? null, client)} onAdd={() => startAdding(client)} />}
    {!listView && <div className="wa-layout"><main>
      <section className="wa-panel"><div className="wa-panel-head"><span><Bot size={19} /></span><div><h2>{t("clients.whatsapp.assignedAgent")}</h2><p>{t("clients.whatsapp.assignedAgentCopy")}</p></div></div><div className="wa-agent-row"><label>{t("clients.whatsapp.agentToRespond")}<select value={agentId} onChange={(event) => setAgentId(event.target.value)} disabled={busy}><option value="">{t("clients.whatsapp.selectAgent")}</option>{client.agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}{agent.is_active ? "" : t("clients.whatsapp.inactiveSuffix")}</option>)}</select></label><label>{t("clients.whatsapp.lineName")}<input value={label} maxLength={80} placeholder={t("clients.whatsapp.lineNamePlaceholder")} onChange={(event) => setLabel(event.target.value)} disabled={busy} /></label></div><p className="social-meta">{t("clients.whatsapp.lineNameHint")}</p>{!client.agents.length && <Alert>{t("clients.whatsapp.needsAgent")}</Alert>}</section>
      <section className="wa-panel"><div className="wa-panel-head"><span><Plug size={19} /></span><div><h2>{t("clients.whatsappCloud.connectionTitle")}</h2><p>{t(state.copy)}</p></div></div>
        {channel?.status === "connected" && <div className="wa-connected"><div className="wa-phone"><Smartphone size={24} /><span><small>{t("clients.whatsapp.connectedNumber")}</small><strong>{channel.phone_number || t("clients.whatsappCloud.numberFallback", { n: 1 })}</strong>{channel.display_name && <em>{channel.display_name}</em>}</span></div><div className="wa-connected-side"><div className="wa-ready"><CheckCircle2 size={18} /> {t("clients.whatsapp.readyForMessages")}</div><button type="button" className="button ghost small" onClick={refreshStatus} disabled={busy}>{busy ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} {busy ? t("clients.whatsappCloud.refreshing") : t("clients.whatsappCloud.refresh")}</button></div></div>}
        {channel?.status === "connected" && (channel.quality_rating || channel.messaging_limit) && <p className="wa-quality"><i className={`channel-state-dot ${qualityTone(channel.quality_rating)}`} aria-hidden="true" /><span>{t("clients.whatsappCloud.qualityLabel")}: <strong>{qualityLabel(channel.quality_rating, t)}</strong></span>{messagingLimitLabel(channel.messaging_limit, t) && <span>· {messagingLimitLabel(channel.messaging_limit, t)}</span>}</p>}
        {channel?.status !== "connected" && <p className="social-meta">{t("clients.whatsappCloud.connectionCopy")}</p>}
        {pendingApproval && <p className="social-meta" role="status">{t("clients.whatsappCloud.approvalPending")}</p>}
        {channel?.last_error && <Alert>{channel.last_error}</Alert>}
        {refreshNotice && <p className="social-meta" role="status">{refreshNotice}</p>}
        <div className="wa-actions">
          {channel && <div className="wa-actions-side">
            <button className="button quiet" onClick={() => setRemoving(true)} disabled={busy}><Trash2 size={16} /> {t("clients.whatsappCloud.removeNumber")}</button>
            {channel.status === "connected" && <button className="button quiet" onClick={() => setDisconnecting(true)} disabled={busy}><Power size={16} /> {t("clients.whatsappCloud.disconnect")}</button>}
          </div>}
          <button className="button secondary" onClick={saveOnly} disabled={!agentId || busy}>{t("clients.whatsappCloud.save")}</button>
          <button className="button primary" onClick={saveAndConnect} disabled={!canConnect}>{busy ? <LoaderCircle className="spin" size={17} /> : <Plug size={17} />} {t("clients.whatsappCloud.connectNumber")}</button>
        </div>
      </section>
      {channel && <section className="wa-panel"><div className="wa-panel-head"><span><Webhook size={19} /></span><div><h2>{t("clients.whatsappCloud.sharedWebhookTitle")}</h2><p>{t("clients.whatsappCloud.sharedWebhookCopy")}</p></div></div>
        <CopyField label={t("clients.whatsappCloud.webhookUrlLabel")} value={channel.webhook_url} />
      </section>}
    </main><aside className="wa-side"><ShieldCheck size={22} /><h3>{t("clients.whatsapp.separationTitle")}</h3><p>{t("clients.whatsapp.separationCopy")}<strong>{client.name}</strong>.</p><hr /><h3>{t("clients.whatsapp.humanControlTitle")}</h3><p>{t("clients.whatsapp.humanControlCopy")}</p></aside></div>}
    {disconnecting && channel && <ConfirmModal title={t("clients.whatsappCloud.disconnect")} message={t("clients.whatsappCloud.confirmDisconnect")} confirmLabel={t("clients.whatsappCloud.disconnect")} cancelLabel={t("common.cancel")} confirmIcon={<Power size={15} />} onConfirm={disconnect} onClose={() => setDisconnecting(false)} />}
    <Modal open={removing && Boolean(channel)} title={t("clients.whatsappCloud.removeNumberTitle", { name: channel ? nameOf(channel) : "" })} onClose={() => setRemoving(false)}>
      <div className="modal-form">
        <p className="modal-copy">{t("clients.whatsappCloud.removeNumberCopy")}</p>
        <div className="modal-actions"><button type="button" className="button" onClick={() => setRemoving(false)}>{t("common.cancel")}</button><button type="button" className="button danger" disabled={busy} onClick={remove}>{busy ? <LoaderCircle className="spin" size={16} /> : <><Trash2 size={15} /> {t("clients.whatsappCloud.removeNumber")}</>}</button></div>
      </div>
    </Modal>
  </div>;
}
