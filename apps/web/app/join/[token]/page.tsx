"use client";

import { FormEvent, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { LoaderCircle, ShieldCheck } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Alert } from "@/components/ui";
import { PasswordInput } from "@/components/password-input";

type InvitationInfo = {
  agency_name: string;
  agency_slug: string;
  email: string;
  name: string;
  status: string;
  expires_at: string;
};

export default function JoinPage() {
  const t = useT();
  const router = useRouter();
  const { token } = useParams<{ token: string }>();
  const [info, setInfo] = useState<InvitationInfo | null>(null);
  const [unknown, setUnknown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    api<InvitationInfo>(`/platform/invitations/${encodeURIComponent(token)}`)
      .then((current) => { if (active) setInfo(current); })
      .catch(() => { if (active) setUnknown(true); });
    return () => { active = false; };
  }, [token]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = Object.fromEntries(new FormData(event.currentTarget)) as Record<string, string>;
    try {
      await api(`/platform/invitations/${encodeURIComponent(token)}/accept`, {
        method: "POST",
        body: JSON.stringify({ name: data.name || null, password: data.password }),
      });
      router.push("/");
      router.refresh();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  const unusable = info !== null && info.status !== "pending";

  return (
    <main className="access-page platform-access">
      <header className="access-topbar">
        <div className="access-brand"><span className="hunterai-icon"><img src="/brand/hunterai-icon.png" alt="" /></span><strong>HunterAI</strong></div>
        <small>{t("platform.login.subtitle")}</small>
      </header>
      <div className="access-layout">
        <div className="access-form-wrap">
          <div className="access-card">
            <span className="access-card-label"><ShieldCheck size={15} /> {info ? t("platform.join.agency") : t("platform.login.title")}</span>
            {unknown && <><h2>{t("platform.join.unknown")}</h2><Alert>{t("platform.join.unknown")}</Alert></>}
            {!unknown && !info && <p role="status"><LoaderCircle className="spin" size={17} /> {t("platform.join.loading")}</p>}
            {info && unusable && (
              <>
                <h2>{t("platform.join.title", { agency: info.agency_name })}</h2>
                <Alert>{info.status === "expired" ? t("platform.join.expired") : t("platform.join.used")}</Alert>
              </>
            )}
            {info && !unusable && (
              <>
                <h2>{t("platform.join.title", { agency: info.agency_name })}</h2>
                <p>{t("platform.join.subtitle")}</p>
                <form onSubmit={submit} className="access-form">
                  <label>{t("platform.join.email")}<input value={info.email} disabled /></label>
                  <label>{t("platform.join.name")}<input name="name" defaultValue={info.name} minLength={2} /></label>
                  <label>{t("platform.join.password")}<PasswordInput name="password" required minLength={8} autoComplete="new-password" /></label>
                  {error && <Alert>{error}</Alert>}
                  <button className="button primary full" disabled={busy}>{busy && <LoaderCircle className="spin" size={17} />}{t("platform.join.submit")}</button>
                </form>
              </>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
