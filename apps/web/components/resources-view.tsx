"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, FileText, Film, HardDrive, ImageIcon, Link2, LoaderCircle, Music, Pencil, RefreshCw, Trash2, Unplug, Upload } from "lucide-react";
import { Alert, EmptyState, Modal } from "@/components/ui";
import { ConfirmModal } from "@/components/confirm-modal";
import { StorageConnectForm } from "@/components/storage-connect-form";
import { useToast } from "@/components/toast";
import { api, apiUrl, messageFrom } from "@/lib/api";
import { useT, useApiError } from "@/lib/i18n";
import type { ClientResource, StorageConnection, StorageConnectPayload } from "@/types";

type Draft = {
  kind: "file" | "link";
  name: string;
  description: string;
  url: string;
  template: string;
  isActive: boolean;
  file: File | null;
};

const MB = 1024 * 1024;
// What the browser lets through before uploading; the server has the last word. Images may be
// large because the server fits them to 1600 px (see apps/api/app/services/image_resize.py).
const KIND_LIMIT_MB: Record<string, number> = { image: 20, video: 16, audio: 16, file: 20 };

function mediaKindOf(mime: string): string {
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("audio/") || mime.startsWith("video/ogg")) return "audio";
  if (mime.startsWith("video/")) return "video";
  return "file";
}

function formatBytes(bytes: number): string {
  if (bytes >= MB) return `${(bytes / MB).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** The client's resource library: its own R2 bucket, and the files and links its
 * agents send with [Herramienta: enviar_recurso]. `apiBase` is the client's route
 * prefix (agency `/clients/{id}`), so the portal can point it at its own. */
export function ResourcesView({ apiBase, canManage }: { apiBase: string; canManage: boolean }) {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<ClientResource[]>([]);
  const [storage, setStorage] = useState<StorageConnection | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<ClientResource | "file" | "link" | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [deleting, setDeleting] = useState<ClientResource | null>(null);

  const friendly = useCallback((err: unknown) => {
    const message = messageFrom(err);
    return message === "storage_not_connected" ? t("resources.errors.notConnected") : message;
  }, [t]);

  const load = useCallback(async () => {
    const [list, conn] = await Promise.all([
      api<ClientResource[]>(`${apiBase}/resources`),
      api<StorageConnection>(`${apiBase}/storage`),
    ]);
    setItems(list);
    setStorage(conn);
  }, [apiBase]);

  useEffect(() => {
    setLoading(true);
    load().catch((err) => setError(friendly(err))).finally(() => setLoading(false));
  }, [load, friendly]);

  const connected = storage?.status === "connected";

  function openEditor(target: ClientResource | "file" | "link") {
    setError("");
    setEditing(target);
    setDraft(typeof target === "string"
      ? { kind: target, name: "", description: "", url: "", template: "", isActive: true, file: null }
      : { kind: target.kind, name: target.name, description: target.description, url: target.url ?? "", template: target.message_template, isActive: target.is_active, file: null });
  }

  const closeEditor = () => { setEditing(null); setDraft(null); setError(""); };
  const patch = (changes: Partial<Draft>) => setDraft((current) => (current ? { ...current, ...changes } : current));

  function pickFile(file: File | null) {
    if (!draft) return;
    const name = draft.name || (file ? file.name.replace(/\.[^.]+$/, "").replace(/[[\]]/g, "").slice(0, 120) : "");
    patch({ file, name });
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft || !editing) return;
    const name = draft.name.trim();
    if (!name) { setError(t("resources.errors.nameRequired")); return; }
    if (/[[\]]/.test(name)) { setError(t("resources.errors.nameBrackets")); return; }
    setBusy(true);
    setError("");
    try {
      if (editing === "file") {
        if (!draft.file) { setError(t("resources.errors.fileRequired")); setBusy(false); return; }
        const kind = mediaKindOf(draft.file.type);
        const limit = kind === "image" ? KIND_LIMIT_MB.image : Math.min(KIND_LIMIT_MB[kind] ?? 20, storage?.max_file_mb ?? 20);
        if (draft.file.size > limit * MB) { setError(t("resources.errors.tooLarge", { max: limit })); setBusy(false); return; }
        const form = new FormData();
        form.append("name", name);
        form.append("description", draft.description.trim());
        form.append("file", draft.file);
        await api<ClientResource>(`${apiBase}/resources/upload`, { method: "POST", body: form });
      } else if (editing === "link") {
        await api<ClientResource>(`${apiBase}/resources`, { method: "POST", body: JSON.stringify({
          kind: "link", name, description: draft.description.trim(), url: draft.url.trim(),
          message_template: draft.template.trim(), is_active: draft.isActive,
        }) });
      } else {
        await api<ClientResource>(`${apiBase}/resources/${editing.id}`, { method: "PATCH", body: JSON.stringify({
          name, description: draft.description.trim(), is_active: draft.isActive,
          ...(editing.kind === "link" ? { url: draft.url.trim(), message_template: draft.template.trim() } : {}),
        }) });
      }
      toast.success(t("resources.saved"));
      closeEditor();
      await load();
    } catch (err) { setError(friendly(err)); }
    finally { setBusy(false); }
  }

  async function remove() {
    if (!deleting) return;
    try {
      await api(`${apiBase}/resources/${deleting.id}`, { method: "DELETE" });
      toast.success(t("resources.deleted"));
      setDeleting(null);
      await load();
    } catch (err) { toast.error(friendly(err)); }
  }

  const iconOf = (row: ClientResource) => {
    if (row.kind === "link") return <Link2 size={20} />;
    if (row.media_kind === "image") return <ImageIcon size={20} />;
    if (row.media_kind === "video") return <Film size={20} />;
    if (row.media_kind === "audio") return <Music size={20} />;
    return <FileText size={20} />;
  };
  const fileUrl = (row: ClientResource) => apiUrl(`${apiBase}/resources/${row.id}/file`);

  return <>
    <StoragePanel apiBase={apiBase} storage={storage} canManage={canManage} onChange={setStorage} friendly={friendly} />

    <div className="stitch-section-header" style={{ marginTop: 24 }}>
      <div><h2>{t("resources.title")}</h2><p>{t("resources.subtitle")}</p></div>
      {canManage && <div className="header-actions">
        <button type="button" className="button secondary small" style={{ borderRadius: "9999px" }} onClick={() => openEditor("link")}><Link2 size={15} /> {t("resources.addLink")}</button>
        <button type="button" className="stitch-action-pill" disabled={!connected} title={connected ? undefined : t("resources.uploadNeedsStorage")} onClick={() => openEditor("file")}><Upload size={15} /> <span>{t("resources.addFile")}</span></button>
      </div>}
    </div>
    {canManage && storage && !connected && <Alert type="info">{t("resources.uploadNeedsStorage")}</Alert>}

    {loading ? (
      <div className="page-loading"><LoaderCircle className="spin" size={24} /></div>
    ) : items.length ? (
      <ul className="stitch-resources-grid">
        {items.map((row) => (
          <li key={row.id} className={`stitch-resource-card${row.is_active ? "" : " inactive"}`}>
            <div className="stitch-resource-card-head">
              <div className="stitch-resource-card-lead">
                {row.kind === "file" && row.media_kind === "image" && connected ? (
                  <img className="stitch-resource-thumb" src={fileUrl(row)} alt="" loading="lazy" />
                ) : (
                  <div className="stitch-resource-icon-box">{iconOf(row)}</div>
                )}
                <div className="stitch-resource-title-col">
                  <div className="stitch-resource-name-row">
                    <h3 className="stitch-resource-name">{row.name}</h3>
                    <span className={row.is_active ? "stitch-badge-active" : "stitch-badge-inactive"}>
                      {row.is_active && <span className="stitch-badge-active-dot" />}
                      {row.is_active ? t("resources.active") : t("resources.inactive")}
                    </span>
                    <span className="stitch-badge-modality">
                      {row.kind === "link" ? t("resources.kindLink") : t(`resources.kind.${row.media_kind ?? "file"}`)}
                    </span>
                  </div>
                  <code className="resource-token" style={{ alignSelf: "flex-start", marginTop: 2 }}>[Recurso: {row.name}]</code>
                </div>
              </div>

              {canManage && (
                <div className="stitch-card-actions">
                  <button
                    type="button"
                    className="stitch-icon-btn"
                    onClick={() => openEditor(row)}
                    title={t("resources.edit")}
                    aria-label={t("resources.edit")}
                  >
                    <Pencil size={15} />
                  </button>
                  <button
                    type="button"
                    className="stitch-icon-btn danger"
                    onClick={() => setDeleting(row)}
                    title={t("resources.delete")}
                    aria-label={t("resources.delete")}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              )}
            </div>

            {row.description && (
              <p className="stitch-resource-description">{row.description}</p>
            )}

            <div className="stitch-resource-meta-row">
              <span className="stitch-resource-meta-info">
                {row.kind === "link" ? (
                  <a href={row.url ?? "#"} target="_blank" rel="noreferrer" className="stitch-resource-link">
                    {row.url}
                  </a>
                ) : (
                  <>
                    <span>{row.filename}</span>
                    <span>·</span>
                    <span>{formatBytes(row.size_bytes)}</span>
                    {connected && (
                      <>
                        <span>·</span>
                        <a href={fileUrl(row)} target="_blank" rel="noreferrer" className="stitch-resource-link">
                          {t("resources.open")}
                        </a>
                      </>
                    )}
                  </>
                )}
              </span>
            </div>
          </li>
        ))}
      </ul>
    ) : (
      <EmptyState
        icon={<HardDrive />}
        title={t("resources.emptyTitle")}
        description={canManage ? t("resources.emptyDescription") : t("resources.emptyReadOnly")}
      />
    )}
    {!editing && error && <Alert>{error}</Alert>}

    <Modal open={editing !== null} title={editing === "file" ? t("resources.form.newFileTitle") : editing === "link" ? t("resources.form.newLinkTitle") : t("resources.form.editTitle")} onClose={closeEditor}>
      {draft && <form className="modal-form" onSubmit={save}>
        {editing === "file" && <label>{t("resources.form.file")}
          <input type="file" required accept="image/*,video/*,audio/*,application/pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip" onChange={(e) => pickFile(e.target.files?.[0] ?? null)} />
          <span className="field-help">{t("resources.form.fileHint", { max: Math.min(storage?.max_file_mb ?? 10, 20) })}</span>
        </label>}
        <label>{t("resources.form.name")}
          <input value={draft.name} required maxLength={120} onChange={(e) => patch({ name: e.target.value })} placeholder={t("resources.form.namePlaceholder")} autoFocus={editing !== "file"} />
          <span className="field-help">{t("resources.form.nameHint")}</span>
        </label>
        <label>{t("resources.form.description")}
          <textarea value={draft.description} rows={2} maxLength={1000} onChange={(e) => patch({ description: e.target.value })} placeholder={t("resources.form.descriptionPlaceholder")} />
        </label>
        {draft.kind === "link" && <>
          <label>{t("resources.form.url")}<input type="url" required value={draft.url} maxLength={2048} onChange={(e) => patch({ url: e.target.value })} placeholder={t("resources.form.urlPlaceholder")} /></label>
          <label>{t("resources.form.template")}
            <textarea value={draft.template} rows={3} maxLength={2000} onChange={(e) => patch({ template: e.target.value })} placeholder={t("resources.form.templatePlaceholder")} />
            <span className="field-help">{t("resources.form.templateHint")}</span>
          </label>
        </>}
        {editing !== "file" && <label className="switch-row">
          <span><strong>{t("resources.form.activeLabel")}</strong><small>{t("resources.form.activeHint")}</small></span>
          <input type="checkbox" checked={draft.isActive} onChange={(e) => patch({ isActive: e.target.checked })} />
        </label>}
        {error && <Alert>{error}</Alert>}
        <div className="modal-actions">
          <button type="button" className="button" onClick={closeEditor}>{t("common.cancel")}</button>
          <button className="button primary" disabled={busy}>{busy ? <><LoaderCircle className="spin" size={16} /> {editing === "file" ? t("resources.form.uploading") : ""}</> : t("resources.form.save")}</button>
        </div>
      </form>}
    </Modal>

    {deleting && <ConfirmModal
      title={t("resources.deleteTitle", { name: deleting.name })}
      message={t("resources.deleteDescription", { name: deleting.name })}
      confirmLabel={t("resources.deleteConfirm")}
      cancelLabel={t("common.cancel")}
      confirmIcon={<Trash2 size={15} />}
      danger
      onConfirm={remove}
      onClose={() => setDeleting(null)}
    />}
  </>;
}

function StoragePanel({ apiBase, storage, canManage, onChange, friendly }: {
  apiBase: string;
  storage: StorageConnection | null;
  canManage: boolean;
  onChange: (value: StorageConnection) => void;
  friendly: (err: unknown) => string;
}) {
  const t = useT();
  const apiError = useApiError();
  const toast = useToast();
  const [replacing, setReplacing] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [checking, setChecking] = useState(false);
  const [link, setLink] = useState("");
  const [limits, setLimits] = useState({ max: 10, quota: 1024 });

  useEffect(() => { if (storage) setLimits({ max: storage.max_file_mb, quota: storage.quota_mb }); }, [storage]);
  if (!storage) return null;

  const connected = storage.status === "connected";

  async function connect(payload: StorageConnectPayload) {
    onChange(await api<StorageConnection>(`${apiBase}/storage`, { method: "PUT", body: JSON.stringify(payload) }));
    setReplacing(false);
    toast.success(t("resources.connect.success"));
  }

  async function check() {
    setChecking(true);
    try {
      const next = await api<StorageConnection>(`${apiBase}/storage/check`, { method: "POST" });
      onChange(next);
      if (next.status === "connected") toast.success(t("resources.storage.checked"));
    } catch (err) { toast.error(friendly(err)); }
    finally { setChecking(false); }
  }

  async function disconnect() {
    try {
      onChange(await api<StorageConnection>(`${apiBase}/storage`, { method: "DELETE" }));
      toast.success(t("resources.storage.disconnected"));
      setConfirmDisconnect(false);
    } catch (err) { toast.error(friendly(err)); }
  }

  async function saveLimits(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      onChange(await api<StorageConnection>(`${apiBase}/storage/limits`, { method: "PATCH", body: JSON.stringify({ max_file_mb: limits.max, quota_mb: limits.quota }) }));
      toast.success(t("resources.storage.limitsSaved"));
    } catch (err) { toast.error(friendly(err)); }
  }

  async function shareLink() {
    try {
      const { connect_url } = await api<{ connect_url: string }>(`${apiBase}/storage/link`, { method: "POST" });
      setLink(connect_url);
      await navigator.clipboard?.writeText(connect_url).then(() => toast.success(t("resources.storage.linkCopied")), () => {});
    } catch (err) { toast.error(friendly(err)); }
  }

  const statusLabel = connected ? t("resources.storage.connected") : storage.status === "error" ? t("resources.storage.error") : t("resources.storage.pending");
  const usedPct = Math.min(100, Math.round((storage.used_bytes / Math.max(1, storage.quota_mb * MB)) * 100));

  return <section className="storage-panel stitch-card">
    <div className="stitch-section-header" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div style={{ width: 44, height: 44, borderRadius: 14, background: "#e6f4f1", color: "#00876c", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <HardDrive size={22} />
        </div>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0, color: "var(--ink)" }}>{t("resources.storage.title")}</h2>
            <span className={`mini-badge ${connected ? "human" : storage.status === "error" ? "danger" : "resolved"}`}>{statusLabel}</span>
          </div>
          <p style={{ margin: "2px 0 0", fontSize: 13, color: "var(--muted)" }}>{t("resources.storage.subtitle")}</p>
        </div>
      </div>
      {canManage && connected && <div className="header-actions">
        <button type="button" className="button secondary small" style={{ borderRadius: 9999 }} onClick={check} disabled={checking}>{checking ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} {t("resources.storage.check")}</button>
        <button type="button" className="button secondary small" style={{ borderRadius: 9999 }} onClick={() => setReplacing((v) => !v)}><Pencil size={14} /> {t("resources.storage.reconnect")}</button>
        <button type="button" className="button danger small" style={{ borderRadius: 9999 }} onClick={() => setConfirmDisconnect(true)}><Unplug size={14} /> {t("resources.storage.disconnect")}</button>
      </div>}
    </div>

    {storage.last_error && <Alert><AlertTriangle size={14} /> {apiError(storage.last_error)}</Alert>}

    {connected && <div className="storage-summary">
      <div><small>{t("resources.storage.bucket")}</small><strong><CheckCircle2 size={14} /> {storage.bucket}</strong></div>
      <div><small>{t("resources.storage.key")}</small><strong>{storage.access_key_hint}</strong></div>
      <div className="storage-usage">
        <small>{t("resources.storage.usage", { used: formatBytes(storage.used_bytes), quota: t("resources.storage.mb", { value: storage.quota_mb }) })}</small>
        <div className="storage-usage-bar"><div style={{ width: `${usedPct}%` }} /></div>
      </div>
    </div>}

    {canManage && connected && <form className="storage-limits" onSubmit={saveLimits}>
      <label>{t("resources.storage.maxFile")}<select value={limits.max} onChange={(e) => setLimits({ ...limits, max: Number(e.target.value) })}>{[5, 10, 16, 20].map((v) => <option key={v} value={v}>{t("resources.storage.mb", { value: v })}</option>)}</select></label>
      <label>{t("resources.storage.quota")}<select value={limits.quota} onChange={(e) => setLimits({ ...limits, quota: Number(e.target.value) })}>{[512, 1024, 2048, 5120, 10240].map((v) => <option key={v} value={v}>{t("resources.storage.mb", { value: v })}</option>)}</select></label>
      <button className="button secondary small">{t("resources.storage.saveLimits")}</button>
    </form>}

    {canManage && (!connected || replacing) && <>
      <StorageConnectForm onConnect={connect} />
      <div className="storage-share">
        <button type="button" className="button secondary small" onClick={shareLink}><Copy size={14} /> {t("resources.storage.shareLink")}</button>
        <span className="field-help">{t("resources.storage.shareLinkHint")}</span>
        {link && <code className="storage-link">{link}</code>}
      </div>
    </>}

    {confirmDisconnect && <ConfirmModal
      title={t("resources.storage.disconnectTitle")}
      message={t("resources.storage.disconnectCopy")}
      confirmLabel={t("resources.storage.disconnect")}
      cancelLabel={t("common.cancel")}
      confirmIcon={<Unplug size={15} />}
      danger
      onConfirm={disconnect}
      onClose={() => setConfirmDisconnect(false)}
    />}
  </section>;
}
