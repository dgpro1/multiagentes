"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Database, ExternalLink, Link2, LoaderCircle, RefreshCw, Unplug } from "lucide-react";
import { Alert } from "@/components/ui";
import { ConfirmModal } from "@/components/confirm-modal";
import { useToast } from "@/components/toast";
import { api, messageFrom } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";
import type { DataStore } from "@/types";

function formatBytes(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(1)} MB`;
}

/** The client's own Supabase project, from the agency's side: its state, the
 * connection link the business owner opens, a re-check and a disconnect. The
 * consent itself happens on the public link (app/connect/supabase/[token]). */
export function DataStorePanel({ clientId }: { clientId: string }) {
  const { t, lang } = useLanguage();
  const toast = useToast();
  const base = `/clients/${clientId}/datastore`;
  const [store, setStore] = useState<DataStore | null>(null);
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState<"" | "link" | "check" | "schema">("");
  const [asking, setAsking] = useState<"" | "toOwn" | "back">("");
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(() => api<DataStore>(base).then(setStore).catch((err) => toast.error(messageFrom(err))), [base, toast]);
  useEffect(() => { load(); }, [load]);

  async function createLink() {
    setBusy("link");
    try {
      const { connect_url } = await api<{ connect_url: string }>(`${base}/link`, { method: "POST" });
      setLink(connect_url);
      await load();
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setBusy(""); }
  }

  async function check() {
    setBusy("check");
    try {
      const next = await api<DataStore>(`${base}/check`, { method: "POST" });
      setStore(next);
      if (next.status === "connected") toast.success(t("dataStore.checked"));
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setBusy(""); }
  }

  async function prepare() {
    setBusy("schema");
    try {
      setStore(await api<DataStore>(`${base}/schema`, { method: "POST" }));
      toast.success(t("dataStore.prepared"));
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setBusy(""); }
  }

  async function move(target: "supabase" | "central") {
    const result = await api<DataStore & { counts: Record<string, number> }>(`${base}/switch`, { method: "POST", body: JSON.stringify({ target }) });
    setStore(result);
    setAsking("");
    const rows = Object.values(result.counts ?? {}).reduce((sum, n) => sum + n, 0);
    toast.success(t("dataStore.moved", { count: rows.toLocaleString(lang) }));
  }

  async function disconnect() {
    setStore(await api<DataStore>(base, { method: "DELETE" }));
    setConfirming(false);
    toast.success(t("dataStore.disconnected"));
  }

  if (!store) return <div className="page-loading"><LoaderCircle className="spin" size={24} /></div>;
  const connected = store.status === "connected";
  const badge = connected ? "human" : store.status === "error" ? "danger" : "resolved";

  return <section className="storage-panel">
    <div className="section-head">
      <div>
        <h2><Database size={18} /> {t("dataStore.title")} <span className={`mini-badge ${badge}`}>{t(`dataStore.status.${store.status}`)}</span></h2>
        <p>{t("dataStore.subtitle")}</p>
      </div>
      {connected && <div className="header-actions">
        <button type="button" className="button secondary small" onClick={check} disabled={busy === "check"}>{busy === "check" ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} {t("dataStore.check")}</button>
        <button type="button" className="button danger small" onClick={() => setConfirming(true)}><Unplug size={14} /> {t("dataStore.disconnect")}</button>
      </div>}
    </div>

    {!store.oauth_ready && <Alert type="info"><AlertTriangle size={14} /> {t("dataStore.notConfigured")}</Alert>}
    {store.last_error && <Alert><AlertTriangle size={14} /> {store.last_error}</Alert>}

    {connected && <div className="storage-summary">
      <div><small>{t("dataStore.project")}</small><strong><CheckCircle2 size={14} /> {store.project_name} ({store.project_ref})</strong></div>
      <div><small>{t("dataStore.region")}</small><strong>{store.region || "—"}</strong></div>
      <div><small>{t("dataStore.size")}</small><strong>{store.db_size_bytes != null ? formatBytes(store.db_size_bytes) : "—"}</strong></div>
      <div><small>{t("dataStore.checkedAt")}</small><strong>{store.last_checked_at ? new Date(store.last_checked_at).toLocaleString(lang) : "—"}</strong></div>
      <div><small>{t("dataStore.schema")}</small><strong>{!store.schema_version ? t("dataStore.schemaMissing")
        : store.schema_version === store.schema_head ? t("dataStore.schemaCurrent", { version: store.schema_version })
        : t("dataStore.schemaBehind", { version: store.schema_version, head: store.schema_head })}</strong></div>
      <div><small>{t("dataStore.mode")}</small><strong>{store.data_mode === "supabase" ? t("dataStore.modeSupabase") : store.data_mode === "switching" ? t("dataStore.modeSwitching") : t("dataStore.modeCentral")}</strong></div>
    </div>}
    {connected && store.schema_version === store.schema_head && store.data_mode !== "switching" && <div className="header-actions">
      {store.data_mode === "central"
        ? <button type="button" className="button primary small" onClick={() => setAsking("toOwn")}><Database size={14} /> {t("dataStore.moveToOwn")}</button>
        : <button type="button" className="button secondary small" onClick={() => setAsking("back")}>{t("dataStore.moveBack")}</button>}
    </div>}
    {asking === "toOwn" && <ConfirmModal title={t("dataStore.moveToOwnTitle")} message={t("dataStore.moveToOwnCopy")} confirmLabel={t("dataStore.moveToOwn")} cancelLabel={t("common.cancel")} confirmIcon={<Database size={15} />} danger={false} onConfirm={() => move("supabase")} onClose={() => setAsking("")} />}
    {asking === "back" && <ConfirmModal title={t("dataStore.moveBackTitle")} message={t("dataStore.moveBackCopy")} confirmLabel={t("dataStore.moveBack")} cancelLabel={t("common.cancel")} danger={false} onConfirm={() => move("central")} onClose={() => setAsking("")} />}
    {connected && store.schema_version !== store.schema_head && <div className="header-actions">
      <button type="button" className="button primary small" onClick={prepare} disabled={busy === "schema"}>
        {busy === "schema" ? <LoaderCircle className="spin" size={14} /> : <Database size={14} />} {t("dataStore.prepare")}
      </button>
    </div>}

    {connected && store.region && store.recommended_region && store.region !== store.recommended_region
      && <Alert><AlertTriangle size={14} /> {t("dataStore.regionWarning", { region: store.region, recommended: store.recommended_region })}</Alert>}
    {!connected && <Alert type="info">{t("dataStore.planWarning")}</Alert>}

    <div className="storage-share">
      <strong>{t("dataStore.link")}</strong>
      <span className="field-help">{t("dataStore.linkHint")}</span>
      <button type="button" className="button secondary small" onClick={createLink} disabled={busy === "link"}>
        {busy === "link" ? <LoaderCircle className="spin" size={14} /> : <Link2 size={14} />} {store.link_active || link ? t("dataStore.renewLink") : t("dataStore.newLink")}
      </button>
      {link && <>
        <code className="storage-link">{link}</code>
        <div className="header-actions">
          <button type="button" className="button secondary small" onClick={() => navigator.clipboard?.writeText(link).then(() => toast.success(t("dataStore.copied")), () => {})}><Copy size={14} /> {t("dataStore.copy")}</button>
          <a className="button secondary small" href={link} target="_blank" rel="noreferrer"><ExternalLink size={14} /> {t("dataStore.open")}</a>
        </div>
      </>}
    </div>

    {confirming && <ConfirmModal
      title={t("dataStore.disconnectTitle")}
      message={t("dataStore.disconnectCopy")}
      confirmLabel={t("dataStore.disconnect")}
      cancelLabel={t("common.cancel")}
      confirmIcon={<Unplug size={15} />}
      danger
      onConfirm={disconnect}
      onClose={() => setConfirming(false)}
    />}
  </section>;
}
