"use client";

import { FormEvent, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ImagePlus, LoaderCircle, Save, Trash2 } from "lucide-react";
import { Alert, Modal } from "@/components/ui";
import { IndustryPicker, isBusinessComplete, type IndustryValue } from "@/components/industry-picker";
import { AiHint } from "@/components/ai-hint";
import { Combobox } from "@/components/combobox";
import { useToast } from "@/components/toast";
import { TIMEZONES } from "@/lib/timezones";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { PortalClientDetails } from "@/types";

type DeletionPreview = { agents: number; channels: number; conversations: number; contacts: number; portal_users: number };

type Editable = PortalClientDetails & { is_active?: boolean };

type Props<T extends Editable> = {
  /** The client as last saved. */
  client: T;
  /** Called with the client as the server returned it after every save or logo change. */
  onChange: (client: T) => void;
} & (
  // The agency panel: every field, the active switch and deleting the client, on the agency's own routes.
  | { mode: "agency"; clientId: string }
  // The client's portal: logo, business, name and timezone only, on `/portal/{slug}/client`.
  | { mode: "portal"; slug: string }
);

/** The client's own details form, shared by the agency's Details tab and the portal's Details screen. */
export function ClientDetails<T extends Editable>(props: Props<T>) {
  const { client, onChange } = props;
  const t = useT();
  const toast = useToast();
  const router = useRouter();
  const agency = props.mode === "agency";
  const apiBase = props.mode === "agency" ? `/clients/${props.clientId}` : `/portal/${props.slug}/client`;
  // A portal cannot read the agency's catalog; it has its own copy of it.
  const catalogPath = props.mode === "portal" ? `/portal/${props.slug}/industries` : undefined;
  const [business, setBusiness] = useState<IndustryValue>({ industry: client.industry, businessType: client.business_type, custom: client.business_custom });
  const [timezone, setTimezone] = useState(client.timezone || "UTC");
  const [busy, setBusy] = useState(false);
  const [logoVersion, setLogoVersion] = useState(0);
  const logoRef = useRef<HTMLInputElement>(null);
  const active = client.is_active ?? true;
  const name = client.name;

  async function uploadLogo(file?: File) {
    if (!file) return;
    setBusy(true);
    const data = new FormData(); data.append("file", file);
    try { onChange(await api<T>(`${apiBase}/logo`, { method: "POST", body: data })); setLogoVersion((v) => v + 1); toast.success(t("clients.detail.logoUpdated")); }
    catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); if (logoRef.current) logoRef.current.value = ""; }
  }
  async function deleteLogo() {
    try {
      await api(`${apiBase}/logo`, { method: "DELETE" });
      setLogoVersion((v) => v + 1);
      // The agency route answers 204 and the portal one the client; both are read back the same way.
      onChange(await api<T>(apiBase));
    } catch (err) { toast.error(messageFrom(err)); }
  }

  async function saveDetails(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    const data = new FormData(event.currentTarget);
    const body: Record<string, unknown> = { name: data.get("name"), industry: business.industry, business_type: business.businessType, business_custom: business.custom, timezone };
    if (agency) body.is_active = data.get("is_active") === "on";
    try { onChange(await api<T>(apiBase, { method: "PATCH", body: JSON.stringify(body) })); toast.success(t("clients.detail.detailsSaved")); }
    catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

  // Deleting takes everything under the client with it, so the dialog shows
  // the counts first and only arms the button once the client's name is typed.
  const [deletePreview, setDeletePreview] = useState<DeletionPreview | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteName, setDeleteName] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  async function openDelete() {
    setDeleteName(""); setDeleteError(null); setDeletePreview(null); setDeleteOpen(true);
    try { setDeletePreview(await api<DeletionPreview>(`${apiBase}/deletion-preview`)); } catch (err) { setDeleteError(messageFrom(err)); }
  }
  async function remove() {
    if (deleteName.trim() !== name.trim()) return;
    setBusy(true); setDeleteError(null);
    try { await api(apiBase, { method: "DELETE" }); toast.success(t("clients.detail.clientDeleted", { name })); router.push("/clients"); }
    catch (err) { setDeleteError(messageFrom(err)); setBusy(false); }
  }

  const logoSrc = client.logo_url ? `${client.logo_url}${client.logo_url.includes("?") ? "&" : "?"}r=${logoVersion}` : null;

  return <>
    <form className="page-form" onSubmit={saveDetails}>
      <section className="form-section">
        <div className="section-copy"><h2>{t("clients.detail.clientInfo")}</h2><p>{t("clients.detail.clientInfoCopy")}</p></div>
        <div className="form-fields">
          <div className="logo-editor">
            <button type="button" className="logo-preview" onClick={() => logoRef.current?.click()}>{logoSrc ? <img src={logoSrc} alt={t("clients.detail.logoAlt")} /> : <ImagePlus size={24} />}</button>
            <div>
              <strong>{t("clients.detail.logoLabel")}</strong>
              <small>{t("clients.detail.logoHint")}</small>
              <div>
                <button type="button" className="text-button" onClick={() => logoRef.current?.click()}>{t("clients.detail.logoChange")}</button>
                {client.logo_url && <button type="button" className="text-button danger-text" onClick={deleteLogo}><Trash2 size={14} /> {t("clients.detail.logoRemove")}</button>}
              </div>
            </div>
            <input ref={logoRef} hidden type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" onChange={(e) => uploadLogo(e.target.files?.[0])} />
          </div>
          <IndustryPicker value={business} onChange={setBusiness} catalogPath={catalogPath} />
          <label><span className="label-row">{t("clients.detail.name")} <AiHint text={t("aiContext.businessName")} /></span><input name="name" required defaultValue={client.name} /></label>
          <label>{t("clients.detail.timezoneLabel")}<Combobox value={timezone} onChange={setTimezone} options={TIMEZONES} placeholder={t("clients.detail.timezoneLabel")} /><span className="field-help">{t("clients.detail.timezoneHint")}</span></label>
          {agency && <label className="switch-row"><span><strong>{t("clients.detail.activeClient")}</strong><small>{t("clients.detail.activeClientHint")}</small></span><input name="is_active" type="checkbox" defaultChecked={active} /></label>}
        </div>
      </section>
      <div className={agency ? "form-footer split" : "form-footer"}>
        {agency && <button type="button" className="button danger" onClick={openDelete}><Trash2 size={16} /> {t("clients.detail.deleteClient")}</button>}
        <button className="button primary" disabled={busy || !isBusinessComplete(business)}>{busy ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />} {t("clients.detail.saveChanges")}</button>
      </div>
    </form>

    {agency && <Modal open={deleteOpen} title={t("clients.detail.deleteTitle", { name })} onClose={() => setDeleteOpen(false)}>
      <div className="modal-form">
        <p className="modal-copy">{t("clients.detail.deleteCopy")}</p>
        {deletePreview ? <ul className="deletion-list">
          <li><strong>{deletePreview.agents}</strong> {t("clients.detail.deleteCountAgents")}</li>
          <li><strong>{deletePreview.channels}</strong> {t("clients.detail.deleteCountChannels")}</li>
          <li><strong>{deletePreview.conversations}</strong> {t("clients.detail.deleteCountConversations")}</li>
          <li><strong>{deletePreview.contacts}</strong> {t("clients.detail.deleteCountContacts")}</li>
          <li><strong>{deletePreview.portal_users}</strong> {t("clients.detail.deleteCountPortalUsers")}</li>
        </ul> : !deleteError && <p className="field-help"><LoaderCircle className="spin" size={14} /></p>}
        <label>{t("clients.detail.deleteTypeName", { name })}<input value={deleteName} onChange={(e) => setDeleteName(e.target.value)} autoComplete="off" placeholder={name} /></label>
        {deleteError && <Alert>{deleteError}</Alert>}
        <div className="modal-actions"><button type="button" className="button" onClick={() => setDeleteOpen(false)}>{t("common.cancel")}</button><button type="button" className="button danger" disabled={busy || !deletePreview || deleteName.trim() !== name.trim()} onClick={remove}>{busy ? <LoaderCircle className="spin" size={16} /> : <><Trash2 size={15} /> {t("clients.detail.deleteClient")}</>}</button></div>
      </div>
    </Modal>}
  </>;
}
