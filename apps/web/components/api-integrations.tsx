"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, ClipboardCopy, KeyRound, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { Alert, Modal } from "@/components/ui";
import { ConfirmModal } from "@/components/confirm-modal";
import { ListRowsSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { ApiIntegration, ApiScopes, ApiTokenIssued, Client } from "@/types";

const EXPIRY_OPTIONS = [7, 30, 90, 365, 1825];

/** API integrations: the credentials handed to a third party. Shared by the
 * agency Settings and each client's API tab; with `clientId` the list is
 * filtered and new integrations are confined to that client. A token secret
 * is shown once, right after issuing it. */
export function ApiIntegrations({ clientId, clientName }: { clientId?: string; clientName?: string }) {
  const t = useT();
  const toast = useToast();
  const [catalog, setCatalog] = useState<ApiScopes | null>(null);
  const [items, setItems] = useState<ApiIntegration[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<ApiIntegration | null>(null);
  const [issuing, setIssuing] = useState<ApiIntegration | null>(null);
  const [issued, setIssued] = useState<ApiTokenIssued | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirming, setConfirming] = useState<{ kind: "integration" | "token"; integration: ApiIntegration; tokenId?: string } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [scopes, rows] = await Promise.all([api<ApiScopes>("/integrations/scopes"), api<ApiIntegration[]>("/integrations")]);
      setCatalog(scopes); setItems(rows);
      if (!clientId) setClients(await api<Client[]>("/clients"));
    } catch (err) { setError(messageFrom(err)); }
  }, [clientId]);
  useEffect(() => { load().finally(() => setLoading(false)); }, [load]);

  const visible = useMemo(
    () => (clientId ? items.filter((item) => item.client_id === clientId) : items),
    [items, clientId],
  );

  async function copySecret(secret: string) {
    await navigator.clipboard.writeText(secret);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  async function confirm() {
    if (!confirming) return;
    try {
      if (confirming.kind === "integration") {
        await api(`/integrations/${confirming.integration.id}`, { method: "DELETE" });
        setItems((rows) => rows.filter((row) => row.id !== confirming.integration.id));
      } else if (confirming.tokenId) {
        await api(`/integrations/${confirming.integration.id}/tokens/${confirming.tokenId}`, { method: "DELETE" });
        const rows = await api<ApiIntegration[]>("/integrations");
        setItems(rows);
      }
      setConfirming(null);
    } catch (err) { toast.error(messageFrom(err)); }
  }

  if (loading) return <ListRowsSkeleton rows={3} />;
  if (error && !catalog) return <Alert>{error}</Alert>;

  return <div className="api-integrations">
    <div className="section-copy">
      <h2>{t("settings.integrations.title")}</h2>
      <p>{clientId
        ? t("settings.integrations.clientCopy", { name: clientName ?? "" })
        : t("settings.integrations.copy")}</p>
    </div>
    {error && <Alert>{error}</Alert>}
    <div className="form-fields">
      <button type="button" className="button primary align-start" onClick={() => { setEditing(null); setFormOpen(true); }}>
        <Plus size={15} /> {t("settings.integrations.new")}
      </button>
    </div>
    {visible.length === 0
      ? <p className="social-meta">{t("settings.integrations.empty")}</p>
      : <div className="table-shell"><table className="data-table"><thead><tr>
        <th>{t("settings.integrations.colName")}</th>
        {!clientId && <th>{t("settings.integrations.colClient")}</th>}
        <th>{t("settings.integrations.colScopes")}</th>
        <th>{t("settings.integrations.colTokens")}</th>
        <th>{t("settings.integrations.colLastUsed")}</th>
        <th />
      </tr></thead><tbody>
        {visible.map((item) => <IntegrationRow key={item.id} item={item} showClient={!clientId}
          expanded={expanded === item.id} onToggle={() => setExpanded((id) => (id === item.id ? null : item.id))}
          onEdit={() => { setEditing(item); setFormOpen(true); }}
          onIssue={() => { setIssuing(item); setIssued(null); setCopied(false); }}
          onRevoke={(tokenId) => setConfirming({ kind: "token", integration: item, tokenId })}
          onDelete={() => setConfirming({ kind: "integration", integration: item })} />)}
      </tbody></table></div>}

    {catalog && <IntegrationFormModal open={formOpen} catalog={catalog} clients={clients}
      fixedClientId={clientId} initial={editing}
      onClose={() => { setFormOpen(false); setEditing(null); }}
      onSaved={(saved) => {
        setItems((rows) => rows.some((row) => row.id === saved.id) ? rows.map((row) => (row.id === saved.id ? saved : row)) : [...rows, saved]);
        setFormOpen(false); setEditing(null);
      }} />}

    {issuing && <IssueTokenModal integration={issuing} issued={issued}
      onClose={() => { setIssuing(null); setIssued(null); }}
      onIssued={async (days) => {
        const result = await api<ApiTokenIssued>(`/integrations/${issuing.id}/tokens`, {
          method: "POST", body: JSON.stringify({ expires_in_days: days }),
        });
        setIssued(result);
        const rows = await api<ApiIntegration[]>("/integrations");
        setItems(rows);
      }}
      onCopy={copySecret} copied={copied} />}

    {confirming && <ConfirmModal
      title={t(confirming.kind === "integration" ? "settings.integrations.deleteTitle" : "settings.integrations.revokeTitle")}
      message={t(confirming.kind === "integration" ? "settings.integrations.deleteCopy" : "settings.integrations.revokeCopy")}
      confirmLabel={t(confirming.kind === "integration" ? "settings.integrations.delete" : "settings.integrations.revoke")}
      cancelLabel={t("common.cancel")} confirmIcon={<Trash2 size={15} />}
      onConfirm={confirm} onClose={() => setConfirming(null)} />}
  </div>;
}

function IntegrationRow({ item, showClient, expanded, onToggle, onEdit, onIssue, onRevoke, onDelete }: {
  item: ApiIntegration; showClient: boolean; expanded: boolean;
  onToggle: () => void; onEdit: () => void; onIssue: () => void;
  onRevoke: (tokenId: string) => void; onDelete: () => void;
}) {
  const t = useT();
  return <>
    <tr>
      <td><button type="button" className="text-button" onClick={onToggle} aria-expanded={expanded}>
        <strong>{item.name}</strong></button>
        <small className="soft">{item.tokens.length === 1
          ? t("settings.integrations.tokenCountOne")
          : t("settings.integrations.tokenCount", { count: item.tokens.length })}</small></td>
      {showClient && <td>{item.client_name ?? t("settings.integrations.agencyWide")}</td>}
      <td><small className="soft">{t("settings.integrations.scopeCount", { count: item.scopes.length })}</small></td>
      <td>{item.tokens.map((token) => <code key={token.id} className="token-prefix">{token.token_prefix}…</code>)}</td>
      <td><small className="soft">{item.last_used_at ? new Date(item.last_used_at).toLocaleString() : "—"}</small></td>
      <td className="row-actions">
        <button type="button" className="button ghost small" onClick={onIssue}><KeyRound size={14} /> {t("settings.integrations.issue")}</button>
        <button type="button" className="button ghost small" onClick={onEdit}>{t("common.edit")}</button>
        <button type="button" className="icon-button small danger-icon" onClick={onDelete} aria-label={t("settings.integrations.delete")} title={t("settings.integrations.delete")}><Trash2 size={14} /></button>
      </td>
    </tr>
    {expanded && <tr className="integration-detail"><td colSpan={showClient ? 6 : 5}>
      <div className="integration-scopes">{item.scopes.map((scope) => <code key={scope}>{scope}</code>)}</div>
      {item.tokens.length > 0 && <ul className="integration-tokens">
        {item.tokens.map((token) => <li key={token.id}>
          <code>{token.token_prefix}…</code>
          <small className="soft">{t("settings.integrations.tokenMeta", {
            expiry: token.expires_at ? new Date(token.expires_at).toLocaleDateString() : t("settings.integrations.neverExpires"),
            count: token.request_count,
          })}</small>
          <button type="button" className="text-button danger-text" onClick={() => onRevoke(token.id)}>{t("settings.integrations.revoke")}</button>
        </li>)}
      </ul>}
    </td></tr>}
  </>;
}

function IntegrationFormModal({ open, catalog, clients, fixedClientId, initial, onClose, onSaved }: {
  open: boolean; catalog: ApiScopes; clients: Client[]; fixedClientId?: string;
  initial: ApiIntegration | null; onClose: () => void; onSaved: (saved: ApiIntegration) => void;
}) {
  const t = useT();
  // The key remounts the form for create vs each edited integration, so its
  // initial state always matches what the modal opened with.
  return <Modal open={open} title={t(initial ? "settings.integrations.editTitle" : "settings.integrations.newTitle")} onClose={onClose} wide>
    {open && <IntegrationForm key={initial?.id ?? "new"} catalog={catalog} clients={clients}
      fixedClientId={fixedClientId} initial={initial} onClose={onClose} onSaved={onSaved} />}
  </Modal>;
}

function presetLabel(t: ReturnType<typeof useT>, key: string): string {
  if (key === "read_only") return t("settings.integrations.presetReadOnly");
  if (key === "operator") return t("settings.integrations.presetOperator");
  if (key === "full") return t("settings.integrations.presetFull");
  return key;
}

function IntegrationForm({ catalog, clients, fixedClientId, initial, onClose, onSaved }: {
  catalog: ApiScopes; clients: Client[]; fixedClientId?: string;
  initial: ApiIntegration | null; onClose: () => void; onSaved: (saved: ApiIntegration) => void;
}) {
  const t = useT();
  const toast = useToast();
  const [name, setName] = useState(initial?.name ?? "");
  const [preset, setPreset] = useState("operator");
  const [scopes, setScopes] = useState<string[]>(initial?.scopes ?? []);
  const [custom, setCustom] = useState(Boolean(initial));
  const [clientChoice, setClientChoice] = useState(fixedClientId ?? initial?.client_id ?? "");
  const [busy, setBusy] = useState(false);

  function pickPreset(next: string) {
    setPreset(next);
    if (next !== "custom") {
      setScopes(catalog.presets[next] ?? []);
      setCustom(false);
    } else {
      setCustom(true);
    }
  }

  function toggleScope(key: string) {
    setCustom(true);
    setPreset("custom");
    setScopes((current) => current.includes(key) ? current.filter((s) => s !== key) : [...current, key]);
  }

  async function save() {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = custom
        ? { scopes }
        : { preset };
      if (!initial) {
        body.name = name.trim();
        if (fixedClientId) body.client_id = fixedClientId;
        else if (clientChoice) body.client_id = clientChoice;
        const saved = await api<ApiIntegration>("/integrations", { method: "POST", body: JSON.stringify(body) });
        toast.success(t("settings.integrations.created"));
        onSaved(saved);
      } else {
        const saved = await api<ApiIntegration>(`/integrations/${initial.id}`, {
          method: "PATCH", body: JSON.stringify({ name: name.trim(), ...(custom ? { scopes } : { preset }) }),
        });
        toast.success(t("settings.integrations.saved"));
        onSaved(saved);
      }
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

  return <div className="modal-form">
      <div className="wa-cloud-form">
        <label>{t("settings.integrations.name")}<input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} disabled={busy} autoComplete="off" /></label>
        {!fixedClientId && !initial && <label>{t("settings.integrations.client")}
          <select value={clientChoice} onChange={(e) => setClientChoice(e.target.value)} disabled={busy}>
            <option value="">{t("settings.integrations.agencyWide")}</option>
            {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </select></label>}
        <label>{t("settings.integrations.preset")}
          <select value={custom ? "custom" : preset} onChange={(e) => pickPreset(e.target.value)} disabled={busy}>
            {Object.keys(catalog.presets).map((key) => <option key={key} value={key}>{presetLabel(t, key)}</option>)}
            <option value="custom">{t("settings.integrations.presetCustom")}</option>
          </select></label>
      </div>
      <div className="integration-scope-list">
        {catalog.scopes.map((scope) => <label key={scope.key} className="switch-row">
          <input type="checkbox" checked={scopes.includes(scope.key)} onChange={() => toggleScope(scope.key)} disabled={busy} />
          <span><strong><code>{scope.key}</code></strong><small>{scope.description}</small></span>
        </label>)}
      </div>
      <div className="modal-actions">
        <button type="button" className="button" onClick={onClose}>{t("common.cancel")}</button>
        <button type="button" className="button primary" disabled={busy || !name.trim() || scopes.length === 0} onClick={save}>
          {busy ? <LoaderCircle className="spin" size={16} /> : null} {t(initial ? "settings.integrations.save" : "settings.integrations.create")}
        </button>
      </div>
    </div>;
}

function IssueTokenModal({ integration, issued, onClose, onIssued, onCopy, copied }: {
  integration: ApiIntegration; issued: ApiTokenIssued | null;
  onClose: () => void; onIssued: (days: number) => Promise<void>;
  onCopy: (secret: string) => void; copied: boolean;
}) {
  const t = useT();
  const toast = useToast();
  const [days, setDays] = useState(365);
  const [busy, setBusy] = useState(false);
  async function issue() {
    setBusy(true);
    try { await onIssued(days); }
    catch (err) { toast.error(messageFrom(err)); }
    finally { setBusy(false); }
  }
  return <Modal open title={t("settings.integrations.issueTitle", { name: integration.name })} onClose={onClose}>
    <div className="modal-form">
      {issued ? <>
        <p className="modal-copy"><CheckCircle2 size={16} /> {t("settings.integrations.issuedCopy")}</p>
        <div className="wa-copy-field">
          <label>{t("settings.integrations.tokenSecret")}<input readOnly value={issued.token} onFocus={(e) => e.currentTarget.select()} /></label>
          <button type="button" className="button secondary" onClick={() => onCopy(issued.token)}>
            <ClipboardCopy size={15} /> {copied ? t("settings.integrations.copied") : t("settings.integrations.copyButton")}
          </button>
        </div>
        <div className="modal-actions">
          <button type="button" className="button primary" onClick={onClose}>{t("settings.integrations.done")}</button>
        </div>
      </> : <>
        <p className="modal-copy">{t("settings.integrations.issueCopy")}</p>
        <div className="wa-cloud-form">
          <label>{t("settings.integrations.expiry")}
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} disabled={busy}>
              {EXPIRY_OPTIONS.map((option) => <option key={option} value={option}>{t("settings.integrations.expiryDays", { days: option })}</option>)}
            </select></label>
        </div>
        <div className="modal-actions">
          <button type="button" className="button" onClick={onClose}>{t("common.cancel")}</button>
          <button type="button" className="button primary" disabled={busy} onClick={issue}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <KeyRound size={15} />} {t("settings.integrations.issue")}
          </button>
        </div>
      </>}
    </div>
  </Modal>;
}
