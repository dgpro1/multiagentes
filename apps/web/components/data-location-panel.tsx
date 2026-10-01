"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Building2, CheckCircle2, Database, HardDrive, LoaderCircle, Server, UserRound } from "lucide-react";
import { Alert } from "@/components/ui";
import { ConfirmModal } from "@/components/confirm-modal";
import { useToast } from "@/components/toast";
import { api, messageFrom } from "@/lib/api";
import { useAgencyModules } from "@/lib/agency-modules";
import { useLanguage } from "@/lib/i18n";
import type { DataStore, StorageConnection } from "@/types";

type Place = "central" | "agency" | "supabase";
type Counts = { counts?: Record<string, number> };

const ICON: Record<Place, typeof Server> = { central: Server, agency: Building2, supabase: UserRound };

/** Who looks after a client's data and files: HunterAI, the agency's own
 * Supabase project and R2 bucket, or the client's own accounts. Choosing one
 * runs the verified move; the files follow the data to and from the agency's
 * bucket, and have their own control in case that second step needs a retry. */
export function DataLocationPanel({ clientId }: { clientId: string }) {
  const { t, lang } = useLanguage();
  const toast = useToast();
  const modules = useAgencyModules(true);
  const [store, setStore] = useState<DataStore | null>(null);
  const [files, setFiles] = useState<StorageConnection | null>(null);
  const [asking, setAsking] = useState<Place | "filesToAgency" | "filesToClient" | "">("");
  const [busy, setBusy] = useState<"" | "data" | "files">("");

  const load = useCallback(async () => {
    try {
      const [next, bucket] = await Promise.all([
        api<DataStore>(`/clients/${clientId}/datastore`),
        api<StorageConnection>(`/clients/${clientId}/storage`),
      ]);
      setStore(next);
      setFiles(bucket);
    } catch (err) { toast.error(messageFrom(err)); }
  }, [clientId, toast]);
  useEffect(() => { void load(); }, [load]);

  if (!store || !files) return <div className="page-loading"><LoaderCircle className="spin" size={22} /></div>;

  const moduleOn = modules?.includes("agency_backend") === true;
  const mode = store.data_mode;
  const switching = mode === "switching" || busy !== "";
  const ownReady = store.status === "connected" && store.schema_version === store.schema_head;
  const ownBucket = Boolean(files.access_key_hint);
  const places: { place: Place; why: string }[] = [
    { place: "central", why: "" },
    ...(moduleOn || mode === "agency" ? [{ place: "agency" as const, why: store.agency_backend_ready ? "" : t("agencyBackend.location.notReadyAgency") }] : []),
    { place: "supabase", why: ownReady ? "" : t("agencyBackend.location.notReadyOwn") },
  ];
  const copy: Record<Place, string> = {
    central: t("agencyBackend.location.toPlatformCopy"),
    agency: t("agencyBackend.location.toAgencyCopy"),
    supabase: t("agencyBackend.location.toOwnCopy"),
  };

  async function change(target: Place) {
    setBusy("data");
    try {
      const moved = await api<DataStore & Counts>(`/clients/${clientId}/datastore/switch`, { method: "POST", body: JSON.stringify({ target }) });
      const rows = Object.values(moved.counts ?? {}).reduce((sum, n) => sum + n, 0);
      toast.success(t("agencyBackend.location.moved", { count: rows.toLocaleString(lang) }));
      setStore(moved);
      // The files follow the data into the agency's bucket, and back to the
      // client's own when it has one; if it has none they stay where they are.
      const wantsAgency = target === "agency" && files!.hosted_by !== "agency" && files!.agency_storage_ready;
      const wantsClient = target !== "agency" && files!.hosted_by === "agency" && ownBucket;
      if (wantsAgency || wantsClient) {
        setBusy("files");
        try { await moveFiles(wantsAgency ? "agency" : "client"); }
        catch (err) { toast.error(t("agencyBackend.location.movedFilesFailed", { error: messageFrom(err) })); }
      }
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setBusy(""); setAsking(""); await load(); }
  }

  async function moveFiles(target: "agency" | "client") {
    await api<StorageConnection>(`/clients/${clientId}/storage/switch`, { method: "POST", body: JSON.stringify({ target }) });
    toast.success(t("agencyBackend.location.filesMoved"));
  }

  async function changeFiles(target: "agency" | "client") {
    setBusy("files");
    try { await moveFiles(target); }
    catch (err) { toast.error(messageFrom(err)); }
    finally { setBusy(""); setAsking(""); await load(); }
  }

  const label: Record<Place, string> = {
    central: t("agencyBackend.location.platform"),
    agency: t("agencyBackend.location.agency"),
    supabase: t("agencyBackend.location.own"),
  };
  const description: Record<Place, string> = {
    central: t("agencyBackend.location.platformCopy"),
    agency: t("agencyBackend.location.agencyCopy"),
    supabase: t("agencyBackend.location.ownCopy"),
  };
  const filesHere = files.hosted_by === "agency" ? t("agencyBackend.location.filesAgency") : files.status === "connected" ? t("agencyBackend.location.filesClient") : t("agencyBackend.location.filesNone");

  return <section className="storage-panel data-location">
    <div className="section-head">
      <div>
        <h2><Database size={18} /> {t("agencyBackend.location.title")}</h2>
        <p>{t("agencyBackend.location.copy")}</p>
      </div>
    </div>
    {mode === "switching" && <Alert type="info"><LoaderCircle className="spin" size={14} /> {t("agencyBackend.location.switching")}</Alert>}
    {busy === "data" && <Alert type="info"><LoaderCircle className="spin" size={14} /> {t("agencyBackend.location.moving")}</Alert>}
    {busy === "files" && <Alert type="info"><LoaderCircle className="spin" size={14} /> {t("agencyBackend.location.movingFiles")}</Alert>}

    <div className="data-location-options" role="radiogroup" aria-label={t("agencyBackend.location.title")}>
      {places.map(({ place, why }) => {
        const Icon = ICON[place];
        const current = mode === place;
        return <button
          key={place}
          type="button"
          role="radio"
          aria-checked={current}
          className={`data-location-option${current ? " current" : ""}`}
          disabled={current ? false : switching || Boolean(why)}
          onClick={() => { if (!current) setAsking(place); }}
        >
          <strong><span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><Icon size={16} /> {label[place]}</span>{current && <span className="mini-badge human"><CheckCircle2 size={12} /> {t("agencyBackend.location.current")}</span>}</strong>
          <span>{description[place]}</span>
          {!current && why && <span className="why"><AlertTriangle size={12} /> {why}</span>}
        </button>;
      })}
    </div>

    {store.agency_schema_retired_at && mode !== "agency" && <p className="field-help">{t("agencyBackend.location.retiredCopy", { date: new Date(store.agency_schema_retired_at).toLocaleDateString(lang) })}</p>}
    {store.agency_schema_last_error && <Alert><AlertTriangle size={14} /> {store.agency_schema_last_error}</Alert>}

    {(moduleOn || files.hosted_by === "agency") && <div className="data-location-files">
      <div><small>{t("agencyBackend.location.filesTitle")}</small><br /><strong><HardDrive size={14} /> {filesHere}</strong></div>
      {files.hosted_by === "agency"
        ? <button type="button" className="button secondary small" disabled={switching || !ownBucket} title={ownBucket ? undefined : t("agencyBackend.location.filesNeedOwnBucket")} onClick={() => setAsking("filesToClient")}>{t("agencyBackend.location.filesToClient")}</button>
        : <button type="button" className="button secondary small" disabled={switching || !files.agency_storage_ready} title={files.agency_storage_ready ? undefined : t("agencyBackend.location.filesNeedAgencyBucket")} onClick={() => setAsking("filesToAgency")}>{t("agencyBackend.location.filesToAgency")}</button>}
    </div>}

    {(asking === "central" || asking === "agency" || asking === "supabase") && <ConfirmModal
      title={t("agencyBackend.location.confirmTitle")}
      message={copy[asking]}
      confirmLabel={`${t("agencyBackend.location.confirm")}: ${label[asking]}`}
      cancelLabel={t("common.cancel")}
      confirmIcon={<Database size={15} />}
      danger={false}
      onConfirm={() => change(asking)}
      onClose={() => setAsking("")}
    />}
    {asking === "filesToAgency" && <ConfirmModal title={t("agencyBackend.location.filesToAgencyTitle")} message={t("agencyBackend.location.filesToAgencyCopy")} confirmLabel={t("agencyBackend.location.filesToAgency")} cancelLabel={t("common.cancel")} danger={false} onConfirm={() => changeFiles("agency")} onClose={() => setAsking("")} />}
    {asking === "filesToClient" && <ConfirmModal title={t("agencyBackend.location.filesToClientTitle")} message={t("agencyBackend.location.filesToClientCopy")} confirmLabel={t("agencyBackend.location.filesToClient")} cancelLabel={t("common.cancel")} danger={false} onConfirm={() => changeFiles("client")} onClose={() => setAsking("")} />}
  </section>;
}
