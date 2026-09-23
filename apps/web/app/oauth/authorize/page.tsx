"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CheckCircle2, LoaderCircle, ShieldCheck, XCircle } from "lucide-react";
import { api, ApiError, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { OAuthClientInfo } from "@/types";

/** The consent screen: a signed-in agency user approves (or denies) a third
 * party's requested scopes, then the browser follows the redirect back. */
export default function OAuthAuthorizePage() {
  const t = useT();
  const router = useRouter();
  const search = useSearchParams();
  const clientId = search.get("client_id") ?? "";
  const redirectUri = search.get("redirect_uri") ?? "";
  const scope = search.get("scope") ?? "";
  const state = search.get("state") ?? "";
  const [info, setInfo] = useState<OAuthClientInfo | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!clientId) { setFailed(true); return; }
    api<OAuthClientInfo>(`/oauth/client?client_id=${encodeURIComponent(clientId)}`)
      .then(setInfo)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setFailed(true);
      });
  }, [clientId, router]);

  async function answer(approved: boolean) {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ redirect_to: string }>("/oauth/authorize", {
        method: "POST",
        body: JSON.stringify({ client_id: clientId, redirect_uri: redirectUri, scope, state, approved }),
      });
      window.location.assign(result.redirect_to);
    } catch (err) {
      setError(messageFrom(err));
      setBusy(false);
    }
  }

  if (failed || !clientId || !redirectUri) return <div className="connect-page"><div className="connect-card">
    <XCircle size={40} className="connect-icon danger" />
    <h1>{t("oauth.invalidTitle")}</h1>
    <p>{t("oauth.invalidCopy")}</p>
  </div></div>;

  if (!info) return <div className="connect-page"><div className="connect-card">
    <LoaderCircle size={32} className="connect-icon spin" />
    <p>{t("oauth.loading")}</p>
  </div></div>;

  const wanted = scope.split(" ").filter(Boolean);
  const shown = wanted.length > 0 ? wanted : info.scopes;
  return <div className="connect-page"><div className="connect-card">
    <ShieldCheck size={40} className="connect-icon" />
    <h1>{t("oauth.heading")}</h1>
    <p>{t("oauth.intro", { name: info.name })}</p>
    <h2 className="connect-scopes-title">{t("oauth.scopesTitle")}</h2>
    <ul className="connect-scopes">
      {shown.map((key) => <li key={key}><CheckCircle2 size={15} />
        <span><strong><code>{key}</code></strong>
          {info.scope_descriptions[key] && <small>{info.scope_descriptions[key]}</small>}</span></li>)}
    </ul>
    {error && <div className="connect-banner connect-banner-warn">{error}</div>}
    <div className="connect-actions">
      <button type="button" className="button" disabled={busy} onClick={() => answer(false)}>{t("oauth.deny")}</button>
      <button type="button" className="button primary" disabled={busy} onClick={() => answer(true)}>
        {busy ? <LoaderCircle size={16} className="spin" /> : null} {busy ? t("oauth.working") : t("oauth.approve")}
      </button>
    </div>
  </div></div>;
}
