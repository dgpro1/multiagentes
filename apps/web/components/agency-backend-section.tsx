"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Cloud, Database, HardDrive, LoaderCircle, PlugZap, RefreshCw, Unplug } from "lucide-react";
import { Alert } from "@/components/ui";
import { ConfirmModal } from "@/components/confirm-modal";
import { StorageConnectForm } from "@/components/storage-connect-form";
import { useToast } from "@/components/toast";
import { api, messageFrom } from "@/lib/api";
import { useAgencyModules } from "@/lib/agency-modules";
import { useApiError, useLanguage } from "@/lib/i18n";
import type { AgencyBackend, AgencyProject, AgencyStorage, StorageConnectPayload } from "@/types";

const RESULTS = ["authorized", "denied", "error", "expired"] as const;

/** The agency's own Supabase project and R2 bucket, in Settings. Shown only when
 * the platform switched the `agency_backend` module on for this agency: a module
 * that ships off is hidden, never offered and refused. */
export function AgencyBackendSection() {
  const modules = useAgencyModules(true);
  if (!modules?.includes("agency_backend")) return null;
  return <Connections />;
}

function Connections() {
  const { t, lang } = useLanguage();
  const apiError = useApiError();
  const toast = useToast();
  const [backend, setBackend] = useState<AgencyBackend | null>(null);
  const [storage, setStorage] = useState<AgencyStorage | null>(null);
  const [projects, setProjects] = useState<AgencyProject[] | null>(null);
  const [busy, setBusy] = useState<"" | "connect" | "projects" | "project" | "check" | "bucketCheck">("");
  const [asking, setAsking] = useState<"" | "project" | "bucket">("");
  const [replacing, setReplacing] = useState(false);
  const announced = useRef(false);

  const load = useCallback(async () => {
    try {
      const [next, bucket] = await Promise.all([api<AgencyBackend>("/agency/backend"), api<AgencyStorage>("/agency/storage")]);
      setBackend(next);
      setStorage(bucket);
    } catch (err) { toast.error(messageFrom(err)); }
  }, [toast]);
  useEffect(() => { void load(); }, [load]);

  // Supabase sends the administrator back here with the outcome in the address.
  useEffect(() => {
    const result = new URLSearchParams(window.location.search).get("backend");
    if (!result || announced.current || !(RESULTS as readonly string[]).includes(result)) return;
    announced.current = true;
    (result === "authorized" ? toast.success : toast.error)(t(`agencyBackend.settings.result.${result as (typeof RESULTS)[number]}`));
    window.history.replaceState(null, "", window.location.pathname);
  }, [t, toast]);

  async function connect() {
    setBusy("connect");
    try {
      const { authorization_url } = await api<{ authorization_url: string }>("/agency/backend/connect", { method: "POST" });
      window.location.assign(authorization_url);
    } catch (err) { toast.error(messageFrom(err)); setBusy(""); }
  }

  async function loadProjects() {
    setBusy("projects");
    try { setProjects(await api<AgencyProject[]>("/agency/backend/projects")); }
    catch (err) { toast.error(messageFrom(err)); }
    finally { setBusy(""); }
  }

  async function choose(ref: string) {
    setBusy("project");
    try {
      setBackend(await api<AgencyBackend>("/agency/backend/project", { method: "POST", body: JSON.stringify({ ref }) }));
      setProjects(null);
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setBusy(""); }
  }

  async function check() {
    setBusy("check");
    try {
      const next = await api<AgencyBackend>("/agency/backend/check", { method: "POST" });
      setBackend(next);
      if (next.status === "connected") toast.success(t("agencyBackend.settings.checked"));
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setBusy(""); }
  }

  async function disconnectProject() {
    try {
      setBackend(await api<AgencyBackend>("/agency/backend", { method: "DELETE" }));
      toast.success(t("agencyBackend.settings.disconnected"));
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setAsking(""); }
  }

  async function connectBucket(payload: StorageConnectPayload) {
    setStorage(await api<AgencyStorage>("/agency/storage", { method: "PUT", body: JSON.stringify(payload) }));
    setReplacing(false);
    toast.success(t("agencyBackend.settings.bucketConnected"));
  }

  async function checkBucket() {
    setBusy("bucketCheck");
    try {
      const next = await api<AgencyStorage>("/agency/storage/check", { method: "POST" });
      setStorage(next);
      if (next.status === "connected") toast.success(t("agencyBackend.settings.checked"));
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setBusy(""); }
  }

  async function disconnectBucket() {
    try {
      setStorage(await api<AgencyStorage>("/agency/storage", { method: "DELETE" }));
      toast.success(t("agencyBackend.settings.disconnected"));
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setAsking(""); }
  }

  if (!backend || !storage) return <section className="section-block"><LoaderCircle className="spin" size={20} /></section>;
  const projectBadge = backend.status === "connected" ? "human" : backend.status === "error" ? "danger" : "resolved";
  const bucketBadge = storage.status === "connected" ? "human" : storage.status === "error" ? "danger" : "resolved";
  const inUse = backend.clients_in_agency > 0;
  const bucketInUse = storage.clients_hosted > 0;

  return <section className="section-block agency-backend">
    <div className="section-heading"><div><h2><Cloud size={18} /> {t("agencyBackend.settings.title")}</h2><p>{t("agencyBackend.settings.copy")}</p></div></div>

    <div className="storage-panel">
      <div className="section-head">
        <div>
          <h3><Database size={16} /> {t("agencyBackend.settings.supabaseTitle")} <span className={`mini-badge ${projectBadge}`}>{t(`agencyBackend.settings.status.${backend.status}`)}</span></h3>
          <p>{t("agencyBackend.settings.supabaseCopy")}</p>
        </div>
        {backend.status === "connected" && <div className="header-actions">
          <button type="button" className="button secondary small" onClick={check} disabled={busy === "check"}>{busy === "check" ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} {t("agencyBackend.settings.check")}</button>
          <button type="button" className="button danger small" onClick={() => setAsking("project")} disabled={inUse} title={inUse ? t("agencyBackend.settings.inUse") : undefined}><Unplug size={14} /> {t("agencyBackend.settings.disconnect")}</button>
        </div>}
      </div>
      {!backend.oauth_ready && <Alert type="info"><AlertTriangle size={14} /> {t("agencyBackend.settings.notConfigured")}</Alert>}
      {backend.last_error && <Alert><AlertTriangle size={14} /> {apiError(backend.last_error)}</Alert>}

      {backend.status === "connected" && <div className="storage-summary">
        <div><small>{t("agencyBackend.settings.project")}</small><strong><CheckCircle2 size={14} /> {backend.project_name} ({backend.project_ref})</strong></div>
        <div><small>{t("agencyBackend.settings.region")}</small><strong>{backend.region || "—"}</strong></div>
        <div><small>{t("agencyBackend.settings.clientsInside")}</small><strong>{backend.clients_in_agency}</strong></div>
        <div><small>{t("agencyBackend.settings.checkedAt")}</small><strong>{backend.last_checked_at ? new Date(backend.last_checked_at).toLocaleString(lang) : "—"}</strong></div>
      </div>}
      {backend.status === "connected" && backend.region && backend.recommended_region && backend.region !== backend.recommended_region
        && <Alert><AlertTriangle size={14} /> {t("agencyBackend.settings.regionWarning", { region: backend.region, recommended: backend.recommended_region })}</Alert>}

      {(backend.status === "none" || backend.status === "pending" || backend.status === "error") && backend.oauth_ready && <div className="header-actions">
        <button type="button" className="button primary small" onClick={connect} disabled={busy === "connect"}>
          {busy === "connect" ? <><LoaderCircle className="spin" size={14} /> {t("agencyBackend.settings.redirecting")}</> : <><PlugZap size={14} /> {t("agencyBackend.settings.connect")}</>}
        </button>
      </div>}

      {(backend.status === "authorized" || (backend.status === "connected" && projects)) && <div className="storage-share">
        <strong>{t("agencyBackend.settings.chooseProject")}</strong>
        <span className="field-help">{t("agencyBackend.settings.chooseHint")}</span>
        {!projects
          ? <button type="button" className="button secondary small" onClick={loadProjects} disabled={busy === "projects"}>{busy === "projects" ? <LoaderCircle className="spin" size={14} /> : <Database size={14} />} {t("agencyBackend.settings.loadProjects")}</button>
          : <ul className="agency-project-list">{projects.map((project) => <li key={project.ref}>
              <span><strong>{project.name}</strong> <small>{project.ref} · {project.region}</small></span>
              <button type="button" className="button primary small" onClick={() => choose(project.ref)} disabled={busy === "project"}>{t("agencyBackend.settings.useProject")}</button>
            </li>)}</ul>}
      </div>}
    </div>

    <div className="storage-panel">
      <div className="section-head">
        <div>
          <h3><HardDrive size={16} /> {t("agencyBackend.settings.storageTitle")} <span className={`mini-badge ${bucketBadge}`}>{t(`agencyBackend.settings.status.${storage.status}`)}</span></h3>
          <p>{t("agencyBackend.settings.storageCopy")}</p>
        </div>
        {storage.status === "connected" && <div className="header-actions">
          <button type="button" className="button secondary small" onClick={checkBucket} disabled={busy === "bucketCheck"}>{busy === "bucketCheck" ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} {t("agencyBackend.settings.check")}</button>
          <button type="button" className="button secondary small" onClick={() => setReplacing((v) => !v)}>{t("resources.storage.reconnect")}</button>
          <button type="button" className="button danger small" onClick={() => setAsking("bucket")} disabled={bucketInUse} title={bucketInUse ? t("agencyBackend.settings.inUse") : undefined}><Unplug size={14} /> {t("agencyBackend.settings.disconnect")}</button>
        </div>}
      </div>
      {storage.last_error && <Alert><AlertTriangle size={14} /> {apiError(storage.last_error)}</Alert>}
      {storage.status === "connected" && <div className="storage-summary">
        <div><small>{t("agencyBackend.settings.bucket")}</small><strong><CheckCircle2 size={14} /> {storage.bucket}</strong></div>
        <div><small>{t("agencyBackend.settings.keyHint")}</small><strong>{storage.access_key_hint}</strong></div>
        <div><small>{t("agencyBackend.settings.clientsHosted")}</small><strong>{storage.clients_hosted}</strong></div>
      </div>}
      {(storage.status !== "connected" || replacing) && <StorageConnectForm onConnect={connectBucket} exampleBucket="agency-client-files" />}
    </div>

    {asking === "project" && <ConfirmModal title={t("agencyBackend.settings.disconnectTitle")} message={t("agencyBackend.settings.disconnectCopy")} confirmLabel={t("agencyBackend.settings.disconnect")} cancelLabel={t("common.cancel")} confirmIcon={<Unplug size={15} />} danger onConfirm={disconnectProject} onClose={() => setAsking("")} />}
    {asking === "bucket" && <ConfirmModal title={t("agencyBackend.settings.disconnectBucketTitle")} message={t("agencyBackend.settings.disconnectBucketCopy")} confirmLabel={t("agencyBackend.settings.disconnect")} cancelLabel={t("common.cancel")} confirmIcon={<Unplug size={15} />} danger onConfirm={disconnectBucket} onClose={() => setAsking("")} />}
  </section>;
}
