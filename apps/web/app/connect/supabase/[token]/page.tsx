"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { AlertTriangle, CheckCircle2, Database, LoaderCircle, XCircle } from "lucide-react";
import { api, ApiError, messageFrom } from "@/lib/api";
import { useT, useApiError } from "@/lib/i18n";
import type { DataStoreConnectInfo, SupabaseProject } from "@/types";

type Result = "authorized" | "denied" | "error" | "expired" | null;

/** The public link a business owner opens to connect their own Supabase project.
 * No OpenLivery account: authorize with Supabase, pick the project, and
 * OpenLivery provisions its own role there. Everything rides on the token. */
export default function SupabaseConnectPage() {
  const t = useT();
  const apiError = useApiError();
  const { token } = useParams<{ token: string }>();
  const result = useSearchParams().get("result") as Result;
  const [info, setInfo] = useState<DataStoreConnectInfo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [projects, setProjects] = useState<SupabaseProject[] | null>(null);
  const [busy, setBusy] = useState<"" | "start" | string>("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const next = await api<DataStoreConnectInfo>(`/datastore/connect/${token}`);
      setInfo(next);
      if (next.status === "authorized" || (next.status === "error" && result === "authorized")) {
        setProjects(await api<SupabaseProject[]>(`/datastore/connect/${token}/projects`));
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setNotFound(true);
      else setError(messageFrom(err));
    }
  }, [token, result]);
  useEffect(() => { load(); }, [load]);

  async function authorize() {
    setBusy("start");
    setError("");
    try {
      const { authorization_url } = await api<{ authorization_url: string }>(`/datastore/connect/${token}/start`, { method: "POST" });
      window.location.href = authorization_url;
    } catch (err) { setError(messageFrom(err)); setBusy(""); }
  }

  async function choose(ref: string) {
    setBusy(ref);
    setError("");
    try {
      setInfo(await api<DataStoreConnectInfo>(`/datastore/connect/${token}/project`, { method: "POST", body: JSON.stringify({ ref }) }));
    } catch (err) { setError(messageFrom(err)); }
    finally { setBusy(""); }
  }

  if (notFound) return <Shell>
    <XCircle size={40} className="connect-icon danger" />
    <h1>{t("dataStore.connect.invalidTitle")}</h1>
    <p>{t("dataStore.connect.invalidCopy")}</p>
  </Shell>;
  if (!info) return <Shell><LoaderCircle size={32} className="connect-icon spin" /></Shell>;

  if (info.status === "connected") return <Shell>
    <CheckCircle2 size={40} className="connect-icon" />
    <h1>{t("dataStore.connect.doneTitle")}</h1>
    <p>{t("dataStore.connect.doneCopy", { project: info.project_name || info.project_ref })}</p>
  </Shell>;

  const banner = result === "denied" ? t("dataStore.connect.denied") : result === "error" ? t("dataStore.connect.error")
    : result === "expired" ? t("dataStore.connect.expired") : "";

  return <Shell wide>
    <Database size={36} className="connect-icon" />
    <h1>{t("dataStore.connect.title")}</h1>
    <p>{t("dataStore.connect.intro", { agency: info.agency_name || "HunterAI", client: info.client_name })}</p>
    {banner && <div className="connect-banner connect-banner-warn"><AlertTriangle size={16} /> {banner}</div>}
    {(error || info.last_error) && <div className="connect-banner connect-banner-warn"><AlertTriangle size={16} /> {error || apiError(info.last_error ?? "")}</div>}

    {info.status === "authorized" || projects ? <div style={{ width: "100%", textAlign: "left" }}>
      <strong>{t("dataStore.connect.pickTitle")}</strong>
      <p className="field-help">{t("dataStore.connect.pickHint", { region: info.recommended_region })}</p>
      {projects === null ? <LoaderCircle className="spin" size={20} />
        : projects.length === 0 ? <p className="field-help">{t("dataStore.connect.noProjects", { region: info.recommended_region })}</p>
        : <ul className="resource-picker-list">{projects.map((project) => <li key={project.ref}>
          <label>
            <span><strong>{project.name}</strong><small>{project.ref} · {project.region}</small></span>
            <button type="button" className="button primary small" style={{ marginLeft: "auto" }} disabled={busy !== ""} onClick={() => choose(project.ref)}>
              {busy === project.ref ? <><LoaderCircle className="spin" size={14} /> {t("dataStore.connect.provisioning")}</> : t("dataStore.connect.use")}
            </button>
          </label>
        </li>)}</ul>}
      <button type="button" className="button secondary small" style={{ marginTop: 10 }} onClick={authorize} disabled={busy !== ""}>{t("dataStore.connect.reconnect")}</button>
    </div> : <>
      <ol className="storage-steps" style={{ textAlign: "left" }}>
        <li>{t("dataStore.connect.step1")}</li>
        <li>{t("dataStore.connect.step2", { client: info.client_name })}</li>
        <li>{t("dataStore.connect.step3")}</li>
      </ol>
      {!info.oauth_ready ? <div className="connect-banner connect-banner-warn"><AlertTriangle size={16} /> {t("dataStore.notConfigured")}</div>
        : <button type="button" className="button primary" onClick={authorize} disabled={busy !== ""}>
          {busy === "start" ? <><LoaderCircle size={16} className="spin" /> {t("dataStore.connect.authorizing")}</> : <><Database size={16} /> {t("dataStore.connect.authorize")}</>}
        </button>}
    </>}
  </Shell>;
}

function Shell({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  const t = useT();
  return <div className="connect-page">
    <div className="connect-card" style={wide ? { width: "min(640px, 100%)" } : undefined}>{children}</div>
    <small className="connect-footer">{t("dataStore.connect.poweredBy")}</small>
  </div>;
}
