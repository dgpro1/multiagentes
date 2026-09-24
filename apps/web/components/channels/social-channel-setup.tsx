"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Bot, CheckCircle2, CircleAlert, Facebook, Instagram, History, LoaderCircle, Plug, Power, ShieldCheck } from "lucide-react";
import { Alert, Modal } from "@/components/ui";
import { AccountList } from "@/components/account-list";
import { ApiError, messageFrom } from "@/lib/api";
import { ChannelsScopeProvider, useBackToChannels, useChannelClient, useChannelsApi, useChannelsScope, type ChannelHrefs } from "@/components/channels/scope";
import { accountName, accountTitle, rememberLine, requestedLine } from "@/lib/channels";
import { useLanguage } from "@/lib/i18n";
import type { Client, SocialChannel, SocialConfig, SocialHistoryJob, SocialProvider } from "@/types";

/** A client's accounts on one provider. The page opens on the list of them;
 * one is picked from there (or named by `?line=<id>`) and the panels below
 * then configure that one. `?new`, or an empty list, starts another through
 * the hosted authorization page, opened in a new tab. */
export function SocialChannelSetup({ provider, apiBase, hrefFor, client }: { provider: SocialProvider; apiBase?: string; hrefFor?: ChannelHrefs; client?: { id: string; name: string } | null }) {
  return <ChannelsScopeProvider apiBase={apiBase} hrefFor={hrefFor} client={client}><SocialScreen provider={provider} /></ChannelsScopeProvider>;
}

function SocialScreen({ provider }: { provider: SocialProvider }) {
  const { t } = useLanguage();
  const { hrefFor, portal } = useChannelsScope();
  const { api } = useChannelsApi();
  const { clientId: id, loadClient } = useChannelClient();
  const backLabel = useBackToChannels();
  const [client, setClient] = useState<Client | null>(null);
  const [config, setConfig] = useState<SocialConfig[SocialProvider] | null>(null);
  const [lines, setLines] = useState<SocialChannel[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [agentId, setAgentId] = useState("");
  const [label, setLabel] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pendingApproval, setPendingApproval] = useState(false);
  const [callbackNotice, setCallbackNotice] = useState<"ready" | "error" | null>(null);
  const [saved, setSaved] = useState(false);
  const [historyJob, setHistoryJob] = useState<SocialHistoryJob | null>(null);
  const [historyPollFailed, setHistoryPollFailed] = useState(false);
  const channel = adding ? null : lines.find((line) => line.id === selectedId) ?? null;
  // The selected account's routes; with none selected, the client's, which adds one.
  const path = `/social/${provider}/channels/${channel?.id ?? id}`;
  const Icon = provider === "instagram" ? Instagram : Facebook;

  const upsert = useCallback((current: SocialChannel) => {
    setLines((items) => items.some((item) => item.id === current.id) ? items.map((item) => (item.id === current.id ? current : item)) : [...items, current]);
  }, []);

  const applyChannel = useCallback((current: SocialChannel) => {
    upsert(current);
    setAdding(false);
    setSelectedId(current.id);
    rememberLine(current.id);
    setAgentId(current.agent_id);
    setLabel(current.label || "");
  }, [upsert]);

  /** Back to the list of accounts, with none picked. */
  const showList = useCallback(() => {
    setAdding(false); setSelectedId(null); rememberLine(null);
    setHistoryJob(null); setError(""); setSaved(false);
  }, []);

  const startAdding = useCallback((owner: Client | null) => {
    setAdding(true); setSelectedId(null); rememberLine(null);
    setAgentId(owner?.agents[0]?.id || ""); setLabel(""); setError(""); setSaved(false);
  }, []);

  const loadHistory = useCallback(async (channelId: string) => {
    try {
      setHistoryJob(await api<SocialHistoryJob>(`/social/${provider}/channels/${channelId}/import-history`));
      setHistoryPollFailed(false);
    } catch (err) {
      setHistoryJob(null);
      if (!(err instanceof ApiError && err.status === 404)) setHistoryPollFailed(true);
    }
  }, [api, provider]);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [owner, setup, items] = await Promise.all([
        loadClient(),
        api<SocialConfig>("/social/config"),
        api<SocialChannel[]>(`/social/${provider}/clients/${id}/channels`),
      ]);
      setClient(owner); setConfig(setup[provider]); setLines(items);
      const wanted = requestedLine();
      const line = items.find((item) => item.id === wanted.line) ?? null;
      if (wanted.adding) startAdding(owner);
      else if (line) { applyChannel(line); await loadHistory(line.id); }
      else showList();
      const callbackStatus = new URLSearchParams(window.location.search).get("messaging_status");
      if (callbackStatus === "ready" || callbackStatus === "error") {
        setCallbackNotice(callbackStatus);
        setPendingApproval(false);
        const clean = new URL(window.location.href);
        clean.searchParams.delete("messaging_status");
        window.history.replaceState(window.history.state, "", clean.pathname + clean.search + clean.hash);
      }
    } catch (err) { setError(messageFrom(err)); }
    finally { setLoading(false); }
  }, [id, api, loadClient, provider, applyChannel, startAdding, showList, loadHistory]);
  useEffect(() => { void load(); }, [load]);

  // The hosted page approves in another tab; reload when coming back.
  useEffect(() => {
    if (!pendingApproval) return;
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      setPendingApproval(false);
      void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [pendingApproval, load]);

  const importing = historyJob?.status === "pending" || historyJob?.status === "processing";
  const channelId = channel?.id ?? null;
  useEffect(() => {
    if (!importing || !channelId) return;
    let cancelled = false;
    let polling = false;
    const timer = window.setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const job = await api<SocialHistoryJob>(`/social/${provider}/channels/${channelId}/import-history`);
        if (!cancelled) { setHistoryJob(job); setHistoryPollFailed(false); }
      } catch {
        if (!cancelled) setHistoryPollFailed(true);
      } finally { polling = false; }
    }, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [api, provider, channelId, importing]);

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setCallbackNotice(null); setSaved(false);
    try { await action(); }
    catch (err) { setError(messageFrom(err)); }
    finally { setBusy(false); }
  }

  async function authorize() {
    if (!agentId) return;
    await run(async () => {
      const result = await api<{ authorization_url: string }>(`/social/${provider}/oauth/start`, {
        method: "POST", body: JSON.stringify({ client_id: id, agent_id: agentId, next_path: hrefFor.type(provider) }),
      });
      window.open(result.authorization_url, "_blank", "noopener");
      setPendingApproval(true);
    });
  }

  async function verifySaved() {
    if (!channel) return;
    await run(async () => {
      applyChannel(await api<SocialChannel>(`${path}/connect`, { method: "POST" }));
      setSaved(true);
    });
  }

  async function saveDetails() {
    if (!channel) return;
    await run(async () => {
      applyChannel(await api<SocialChannel>(path, { method: "PATCH", body: JSON.stringify({ agent_id: agentId, label: label.trim() }) }));
      setSaved(true);
    });
  }

  async function importHistory() {
    if (importing || !channel) return;
    await run(async () => {
      setHistoryJob(await api<SocialHistoryJob>(`${path}/import-history`, { method: "POST", body: "{}" }));
      setHistoryPollFailed(false);
    });
  }

  async function disconnect() {
    if (!channel) return;
    await run(async () => {
      await api(`${path}/disconnect`, { method: "POST" });
      // The account is gone; back to the list. With none left the empty
      // list stays: "add account" is the way in, never an automatic
      // landing on the form.
      showList();
    });
  }

  if (loading) return <div className="page-loading"><LoaderCircle className="spin" /> {t("social.loading")}</div>;
  if (!client || !config) return <div className="page"><Alert>{error || t("social.loadFailed")}</Alert><button className="button secondary" onClick={load}>{t("social.retry")}</button></div>;
  const connected = channel?.status === "connected" && channel.is_enabled;
  const statusLabel = connected ? "social.connected" : channel?.status === "error" ? "social.error" : "social.disconnected";
  const dirty = Boolean(channel && (channel.agent_id !== agentId || (channel.label || "") !== label.trim()));
  const nameOf = (line: SocialChannel) => accountName(line, t("social.accountFallback", { n: lines.indexOf(line) + 1 }));
  const listView = !adding && !channel;
  const agentNameOf = (line: SocialChannel) => client.agents.find((agent) => agent.id === line.agent_id)?.name || t("clients.detail.noAgent");
  const rows = lines.map((line) => {
    const live = line.is_enabled && line.status === "connected";
    return {
      id: line.id, title: line.display_name || (line.username ? `@${line.username.replace(/^@/, "")}` : "") || line.external_account_id || nameOf(line), inboxName: nameOf(line),
      agentName: `${t("clients.detail.colAgent")}: ${agentNameOf(line)}`,
      state: live ? "connected" as const : "disconnected" as const,
      stateLabel: t(live ? "social.connected" : line.status === "error" ? "social.error" : "social.disconnected"),
    };
  });
  const connectedCount = rows.filter((row) => row.state === "connected").length;

  return <div className="page wa-page social-page">
    {listView || !lines.length
      ? <Link href={hrefFor.overview()} className="back-link"><ArrowLeft size={17} /> {backLabel(client.name)}</Link>
      : <button type="button" className="back-link" onClick={showList}><ArrowLeft size={17} /> {t(`social.${provider}.title`)}</button>}
    <header className="wa-header"><div className={`wa-mark ${provider}`}><Icon size={26} /></div><div><span>{listView ? t("clients.whatsapp.channelOf", { name: client.name }) : `${t(`social.${provider}.title`)} · ${client.name}`}</span><h1>{channel ? accountTitle(channel, nameOf(channel)) : adding ? t("social.newAccount") : t(`social.${provider}.title`)}</h1><p>{t(`social.${provider}.description`)}</p></div>{channel && <div className={`wa-state ${connected ? "connected" : channel.status === "error" ? "error" : "disconnected"}`}>{connected ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />} {t(statusLabel)}</div>}</header>
    {error && <Alert>{error}</Alert>}
    {callbackNotice && <Alert>{t(callbackNotice === "ready" ? "social.approvalReady" : "social.approvalError")}</Alert>}
    {listView && <AccountList rows={rows} summary={lines.length === 1 ? t("clients.detail.channelAccountOne") : t("clients.detail.channelAccounts", { count: lines.length, connected: connectedCount })} addLabel={t("clients.detail.addAccount")} openLabel={t("clients.detail.configure")} onOpen={(lineId) => { const line = lines.find((item) => item.id === lineId); if (line) { applyChannel(line); setError(""); setSaved(false); void loadHistory(line.id); } }} onAdd={() => startAdding(client)} />}
    {saved && <p className="social-feedback" role="status"><CheckCircle2 size={16} /> {t("social.saved")}</p>}
    {!listView && <div className="wa-layout"><main>
      <section className="wa-panel"><div className="wa-panel-head"><span><Bot size={19} /></span><div><h2>{t("clients.whatsapp.assignedAgent")}</h2><p>{t("clients.whatsapp.assignedAgentCopy")}</p></div></div><div className="wa-agent-row"><label>{t("clients.whatsapp.agentToRespond")}<select value={agentId} onChange={(event) => setAgentId(event.target.value)} disabled={busy}><option value="">{t("clients.whatsapp.selectAgent")}</option>{client.agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}{agent.is_active ? "" : t("clients.whatsapp.inactiveSuffix")}</option>)}</select></label><label>{t("social.accountName")}<input value={label} maxLength={80} placeholder={t("social.accountNamePlaceholder")} onChange={(event) => setLabel(event.target.value)} disabled={busy} /></label>{channel && <button className="button secondary" disabled={!agentId || !dirty || busy} onClick={saveDetails}>{t("social.agentSave")}</button>}</div><p className="social-meta">{t("social.accountNameHint")}</p>{!client.agents.length && <Alert>{t("clients.whatsapp.needsAgent")}</Alert>}</section>
      <section className="wa-panel"><div className="wa-panel-head"><span><Plug size={19} /></span><div><h2>{t("social.accountTitle")}</h2><p>{t("social.accountCopy")}</p></div></div>
        <p>{t(`social.${provider}.requirement`)}</p>
        {channel && <div className="social-account"><Icon size={24} /><div><strong>{channel.display_name || channel.username || channel.external_account_id}</strong>{channel.username && <small>@{channel.username.replace(/^@/, "")}</small>}<small>{channel.external_account_id}</small>{connected && <small>{t("social.connectedCopy")}</small>}</div></div>}
        {channel?.last_error && <Alert>{channel.last_error}</Alert>}
        {pendingApproval && <p className="social-meta" role="status">{t("social.approvalPending")}</p>}
        {!config.oauth_ready && <p className="social-setup-notice">{t(portal ? "social.providerNotReadyPortal" : "social.providerNotReady")}</p>}
        <div className="wa-actions"><button className="button primary" onClick={authorize} disabled={!config.oauth_ready || !agentId || busy}>{busy ? <LoaderCircle className="spin" size={17} /> : <Plug size={17} />} {t(channel ? "social.reconnect" : "social.connect")}</button>{channel && <button className="button secondary" disabled={busy} onClick={verifySaved}>{t(connected ? "social.verify" : "social.connectSaved")}</button>}{channel && <button className="button danger" onClick={() => setDisconnectOpen(true)} disabled={busy}><Power size={17} /> {t("social.disconnect")}</button>}</div>
        <Modal open={disconnectOpen} title={t("social.disconnectTitle", { name: channel?.display_name || channel?.username || channel?.external_account_id || "" })} onClose={() => setDisconnectOpen(false)}>
          <div className="modal-form">
            <p className="modal-copy">{t("social.disconnectCopy")}</p>
            <div className="modal-actions"><button type="button" className="button" onClick={() => setDisconnectOpen(false)}>{t("common.cancel")}</button><button type="button" className="button danger" disabled={busy} onClick={disconnect}>{busy ? <LoaderCircle className="spin" size={16} /> : <><Power size={15} /> {t("social.disconnect")}</>}</button></div>
          </div>
        </Modal>
      </section>
      {channel && (connected || historyJob) && <section className="wa-panel"><div className="wa-panel-head"><span><History size={19} /></span><div><h2>{t("social.historyTitle")}</h2><p>{t("social.importHistoryCopy")}</p></div></div>{provider === "instagram" && <p className="social-meta">{t("social.instagramHistoryLimit")}</p>}{historyJob && <div className="social-history-status" role="status"><strong>{t(historyJob.status === "pending" ? "social.historyPending" : historyJob.status === "processing" ? "social.historyProcessing" : historyJob.status === "completed" ? "social.historyCompleted" : "social.historyFailed")}</strong><small>{t("social.historyCounts", { conversations: historyJob.conversations_count, messages: historyJob.messages_count })}</small></div>}{historyJob?.last_error && <Alert>{historyJob.last_error}</Alert>}{historyPollFailed && <p className="social-meta" role="status">{t("social.historyPollError")}</p>}<div className="wa-actions"><button className="button secondary" onClick={importHistory} disabled={!connected || importing || busy}>{importing ? <LoaderCircle className="spin" size={17} /> : <History size={17} />} {t("social.importHistory")}</button></div></section>}
    </main><aside className="wa-side"><ShieldCheck size={22} /><h3>{t("social.rulesTitle")}</h3><p>{t("social.rulesCopy")}</p><hr /><h3>{t("social.historyTitle")}</h3><p>{t("social.historyCopy")}</p></aside></div>}
  </div>;
}
