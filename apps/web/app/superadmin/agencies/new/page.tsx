"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { LoaderCircle } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { PlatformShell } from "@/components/platform-shell";
import { Alert, PageHead } from "@/components/ui";
import { platformAgencyPath } from "@/lib/routes";
import type { PlatformInvitation } from "@/types";

type Created = {
  agency: { id: string; slug: string; name: string };
  invitation: PlatformInvitation;
};

export default function PlatformNewAgencyPage() {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<Created | null>(null);
  const [copied, setCopied] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = Object.fromEntries(new FormData(event.currentTarget)) as Record<string, string>;
    try {
      const body = await api<Created>("/platform/agencies", {
        method: "POST",
        body: JSON.stringify({
          name: data.name,
          slug: data.slug || null,
          admin_name: data.admin_name,
          admin_email: data.admin_email,
        }),
      });
      setCreated(body);
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!created?.invitation.url) return;
    await navigator.clipboard.writeText(created.invitation.url);
    setCopied(true);
  }

  if (created) {
    return (
      <PlatformShell>
        <PageHead
          eyebrow={t("platform.create.title")}
          title={t("platform.create.createdTitle")}
          description={t("platform.create.createdCopy", { name: created.invitation.name })}
        />
        <section className="table-shell" style={{ padding: "16px" }}>
          <p style={{ fontSize: 13, color: "var(--muted)", margin: "0 0 6px" }}>{t("platform.create.inviteLink")}</p>
          <p style={{ fontSize: 14, wordBreak: "break-all" }}><strong>{created.invitation.url}</strong></p>
          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            <button className="button primary" onClick={copy}>{copied ? t("platform.create.copied") : t("platform.create.copy")}</button>
            <Link className="button" href={platformAgencyPath(created.agency.slug)}>{t("platform.create.openAgency")}</Link>
            <button className="button" onClick={() => { setCreated(null); setCopied(false); }}>{t("platform.create.createAnother")}</button>
          </div>
        </section>
      </PlatformShell>
    );
  }

  return (
    <PlatformShell>
      <PageHead eyebrow={t("platform.overview.eyebrow")} title={t("platform.create.title")} description={t("platform.create.description")} />
      <form className="page-form" onSubmit={submit}>
        <section className="form-section">
          <div className="section-copy"><h2>{t("platform.create.title")}</h2><p>{t("platform.create.description")}</p></div>
          <div className="form-fields">
            <div className="form-grid">
              <label>{t("platform.create.name")}<input name="name" required minLength={2} /></label>
              <label>{t("platform.create.slug")}<input name="slug" /><small>{t("platform.create.slugHint")}</small></label>
              <label>{t("platform.create.adminName")}<input name="admin_name" required minLength={2} /></label>
              <label>{t("platform.create.adminEmail")}<input name="admin_email" required type="email" /></label>
            </div>
            {error && <Alert>{error}</Alert>}
            <button className="button primary align-start" disabled={busy}>{busy && <LoaderCircle className="spin" size={17} />}{t("platform.create.submit")}</button>
          </div>
        </section>
      </form>
    </PlatformShell>
  );
}
