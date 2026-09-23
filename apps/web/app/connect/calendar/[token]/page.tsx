"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { AlertTriangle, CalendarCheck2, CalendarRange, CheckCircle2, LoaderCircle, XCircle } from "lucide-react";
import { api, ApiError, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { CalendarConnectInfo } from "@/types";

type Result = "connected" | "denied" | "scope" | "error" | "expired" | null;

/** The public link a team member opens to connect their own Google Calendar.
 * No OpenLivery account: everything here rides on the link's token. */
export default function CalendarConnectPage() {
  const t = useT();
  const { token } = useParams<{ token: string }>();
  const search = useSearchParams();
  const result = search.get("result") as Result;
  const [info, setInfo] = useState<CalendarConnectInfo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");

  useEffect(() => {
    api<CalendarConnectInfo>(`/calendar/connect/${token}`)
      .then(setInfo)
      .catch((err) => { if (err instanceof ApiError && err.status === 404) setNotFound(true); });
  }, [token]);

  async function connect() {
    setStarting(true);
    setStartError("");
    try {
      const { authorization_url } = await api<{ authorization_url: string }>(`/calendar/connect/${token}/start`, { method: "POST" });
      window.location.href = authorization_url;
    } catch (err) { setStartError(messageFrom(err)); setStarting(false); }
  }

  if (notFound) return <ConnectShell>
    <XCircle size={40} className="connect-icon danger" />
    <h1>{t("calendar.connect.invalidTitle")}</h1>
    <p>{t("calendar.connect.invalidCopy")}</p>
  </ConnectShell>;

  if (!info) return <ConnectShell><LoaderCircle size={32} className="connect-icon spin" /></ConnectShell>;

  if (info.expired) return <ConnectShell color={info.color} agency={info.agency_name}>
    <XCircle size={40} className="connect-icon danger" />
    <h1>{t("calendar.connect.expiredTitle")}</h1>
    <p>{t("calendar.connect.expiredCopy", { client: info.client_name })}</p>
  </ConnectShell>;

  const roleSuffix = info.member_role ? t("calendar.connect.roleSuffix", { role: info.member_role }) : "";
  const alreadyConnected = info.status === "connected" && result !== "denied" && result !== "scope" && result !== "error";

  return <ConnectShell color={info.color} agency={info.agency_name}>
    <div className="connect-avatar" style={{ background: info.color }}>{info.member_name.slice(0, 1).toUpperCase()}</div>
    <h1>{t("calendar.connect.heading")}</h1>
    <p>{t("calendar.connect.intro", { client: info.client_name, name: info.member_name, role: roleSuffix })}</p>

    {result === "connected" && <div className="connect-banner connect-banner-ok"><CheckCircle2 size={16} /> {t("calendar.connect.resultConnectedCopy", { email: info.google_email || "" })}</div>}
    {result === "denied" && <div className="connect-banner connect-banner-warn"><AlertTriangle size={16} /> {t("calendar.connect.resultDeniedCopy")}</div>}
    {result === "scope" && <div className="connect-banner connect-banner-warn"><AlertTriangle size={16} /> {t("calendar.connect.resultScopeCopy")}</div>}
    {result === "error" && <div className="connect-banner connect-banner-warn"><AlertTriangle size={16} /> {t("calendar.connect.resultErrorCopy")}</div>}
    {startError && <div className="connect-banner connect-banner-warn"><AlertTriangle size={16} /> {startError}</div>}

    {!info.oauth_ready ? <div className="connect-banner connect-banner-warn"><AlertTriangle size={16} /> {t("calendar.connect.notConfigured")}</div>
      : alreadyConnected ? <>
        <div className="connect-banner connect-banner-ok"><CalendarCheck2 size={16} /> {t("calendar.connect.alreadyConnectedCopy", { email: info.google_email || "" })}</div>
        <button type="button" className="button secondary" onClick={connect} disabled={starting}>{starting ? <LoaderCircle size={16} className="spin" /> : <CalendarRange size={16} />} {t("calendar.connect.reconnect")}</button>
      </> : <button type="button" className="button primary connect-google-button" onClick={connect} disabled={starting}>
        {starting ? <><LoaderCircle size={16} className="spin" /> {t("calendar.connect.connecting")}</> : <><GoogleMark /> {t("calendar.connect.connectButton")}</>}
      </button>}
  </ConnectShell>;
}

function GoogleMark() {
  return <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
    <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.6 2.4 30.1 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.1 17.7 9.5 24 9.5z" />
    <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.6c-.5 3-2.2 5.5-4.7 7.2l7.3 5.7c4.3-4 6.8-9.8 6.8-17.4z" />
    <path fill="#FBBC05" d="M10.5 28.3A14.5 14.5 0 019.8 24c0-1.5.3-3 .7-4.3l-7.9-6.1A24 24 0 000 24c0 3.9.9 7.5 2.6 10.7l7.9-6.4z" />
    <path fill="#34A853" d="M24 48c6.1 0 11.3-2 15-5.5l-7.3-5.7c-2 1.4-4.7 2.2-7.7 2.2-6.3 0-11.6-3.6-13.5-8.7l-7.9 6.4C6.5 42.6 14.6 48 24 48z" />
  </svg>;
}

function ConnectShell({ children, color, agency }: { children: React.ReactNode; color?: string; agency?: string }) {
  const t = useT();
  return <div className="connect-page" style={color ? ({ "--connect-accent": color } as React.CSSProperties) : undefined}>
    <div className="connect-card">{children}</div>
    <small className="connect-footer">{agency ? t("calendar.connect.poweredByWithAgency", { agency }) : t("calendar.connect.poweredBy")}</small>
  </div>;
}
