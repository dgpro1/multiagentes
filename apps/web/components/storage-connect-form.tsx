"use client";

import { FormEvent, useState } from "react";
import { ExternalLink, LoaderCircle, PlugZap } from "lucide-react";
import { Alert } from "@/components/ui";
import { PasswordInput } from "@/components/password-input";
import { messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { StorageConnectPayload } from "@/types";

const ACCOUNT_ID = /^[0-9a-f]{32}$/;

/** The four-step guide and the credentials form for the client's own Cloudflare R2
 * bucket. Shared by the Library tab and the public link a business owner opens;
 * `onConnect` sends the payload wherever the caller's route is. */
export function StorageConnectForm({ onConnect, exampleBucket = "my-business-files" }: {
  onConnect: (payload: StorageConnectPayload) => Promise<void>;
  exampleBucket?: string;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const payload: StorageConnectPayload = {
      account_id: String(data.get("account_id") || "").trim().toLowerCase(),
      access_key_id: String(data.get("access_key_id") || "").trim(),
      secret_access_key: String(data.get("secret_access_key") || "").trim(),
      bucket: String(data.get("bucket") || "").trim().toLowerCase(),
    };
    if (!ACCOUNT_ID.test(payload.account_id)) { setError(t("resources.connect.accountIdHint")); return; }
    setBusy(true);
    setError("");
    try { await onConnect(payload); }
    catch (err) { setError(messageFrom(err)); }
    finally { setBusy(false); }
  }

  const steps = [
    [t("resources.connect.step1Title"), t("resources.connect.step1", { example: exampleBucket })],
    [t("resources.connect.step2Title"), t("resources.connect.step2")],
    [t("resources.connect.step3Title"), t("resources.connect.step3")],
    [t("resources.connect.step4Title"), t("resources.connect.step4")],
  ];

  return <div className="storage-connect">
    <p className="field-help" style={{ margin: "0 0 4px 0", fontSize: 13, color: "var(--muted)" }}>{t("resources.connect.intro")}</p>
    <ol className="storage-steps">
      {steps.map(([title, copy], idx) => (
        <li key={title} className="storage-step-tile">
          <div className="storage-step-badge">{idx + 1}</div>
          <div className="storage-step-text">
            <strong>{title}</strong>
            <span>{copy}</span>
          </div>
        </li>
      ))}
    </ol>
    <div style={{ marginBottom: 12 }}>
      <a className="button secondary small" style={{ borderRadius: 9999, display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 16px" }} href="https://dash.cloudflare.com/?to=/:account/r2/overview" target="_blank" rel="noreferrer">
        <ExternalLink size={14} /> {t("resources.connect.openCloudflare")}
      </a>
    </div>
    <form className="modal-form" onSubmit={submit} autoComplete="off">
      <div className="form-grid">
        <label>{t("resources.connect.accountId")}<input name="account_id" required minLength={32} maxLength={32} spellCheck={false} placeholder="0123456789abcdef0123456789abcdef" /><span className="field-help">{t("resources.connect.accountIdHint")}</span></label>
        <label>{t("resources.connect.bucket")}<input name="bucket" required minLength={3} maxLength={63} spellCheck={false} placeholder={exampleBucket} /></label>
      </div>
      <div className="form-grid">
        <label>{t("resources.connect.accessKeyId")}<input name="access_key_id" required minLength={8} maxLength={128} spellCheck={false} /></label>
        <label>{t("resources.connect.secret")}<PasswordInput name="secret_access_key" required minLength={8} maxLength={256} autoComplete="new-password" /></label>
      </div>
      {error && <Alert>{error}</Alert>}
      <div className="modal-actions" style={{ justifyContent: "flex-start", marginTop: 14 }}>
        <button className="stitch-action-pill" style={{ background: "#00876c" }} disabled={busy}>
          {busy ? <><LoaderCircle className="spin" size={16} /> {t("resources.connect.testing")}</> : <><PlugZap size={16} /> {t("resources.connect.submit")}</>}
        </button>
      </div>
    </form>
  </div>;
}
