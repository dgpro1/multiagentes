"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle, ShieldCheck } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Alert } from "@/components/ui";
import { PasswordInput } from "@/components/password-input";

export default function PlatformLoginPage() {
  const t = useT();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = Object.fromEntries(new FormData(event.currentTarget));
    try {
      await api("/platform/auth/login", { method: "POST", body: JSON.stringify(data) });
      router.push("/superadmin");
      router.refresh();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="access-page platform-access">
      <header className="access-topbar">
        <div className="access-brand"><span className="hunterai-icon"><img src="/brand/hunterai-icon.png" alt="" /></span><strong>HunterAI</strong></div>
        <small>{t("platform.login.subtitle")}</small>
      </header>
      <div className="access-layout">
        <div className="access-form-wrap">
          <div className="access-card">
            <span className="access-card-label"><ShieldCheck size={15} /> {t("platform.login.title")}</span>
            <h2>{t("platform.login.title")}</h2>
            <form onSubmit={submit} className="access-form">
              <label>{t("platform.login.email")}<input name="email" required type="email" placeholder={t("platform.login.emailPlaceholder")} /></label>
              <label>{t("platform.login.password")}<PasswordInput name="password" required placeholder={t("platform.login.passwordPlaceholder")} /></label>
              {error && <Alert>{error}</Alert>}
              <button className="button primary full" disabled={busy}>{busy && <LoaderCircle className="spin" size={17} />}{t("platform.login.submit")}</button>
            </form>
          </div>
        </div>
      </div>
    </main>
  );
}
