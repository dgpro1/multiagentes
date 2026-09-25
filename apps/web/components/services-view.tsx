"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Briefcase, Clock, Home, Laptop, LoaderCircle, MapPin, Pencil, Plus, Trash2 } from "lucide-react";
import { Alert, EmptyState, Modal } from "@/components/ui";
import { useToast } from "@/components/toast";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Service, ServiceModality } from "@/types";

const DURATION_PRESETS = [15, 20, 30, 45, 60, 90, 120, 180, 240] as const;

type Draft = {
  name: string;
  description: string;
  price: number;
  currency: string;
  durationMinutes: number;
  modality: ServiceModality;
  requiresDeposit: boolean;
  depositAmount: number | "";
  requirements: string;
  isActive: boolean;
};

export function ServicesView({
  apiBase,
  canManage,
  currency = "USD",
}: {
  apiBase: string;
  canManage: boolean;
  currency?: string;
}) {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Service | "new" | null>(null);
  const [deleting, setDeleting] = useState<Service | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);

  const load = useCallback(async () => {
    setItems(await api<Service[]>(`${apiBase}/services`));
  }, [apiBase]);

  useEffect(() => {
    setLoading(true);
    load()
      .catch((err) => setError(messageFrom(err)))
      .finally(() => setLoading(false));
  }, [load]);

  function openEditor(target: Service | "new") {
    setError("");
    setEditing(target);
    if (target === "new") {
      setDraft({
        name: "",
        description: "",
        price: 0,
        currency,
        durationMinutes: 30,
        modality: "presencial",
        requiresDeposit: false,
        depositAmount: "",
        requirements: "",
        isActive: true,
      });
    } else {
      setDraft({
        name: target.name,
        description: target.description ?? "",
        price: target.price,
        currency: target.currency || currency,
        durationMinutes: target.duration_minutes,
        modality: target.modality,
        requiresDeposit: target.requires_deposit,
        depositAmount: target.deposit_amount ?? "",
        requirements: target.requirements ?? "",
        isActive: target.is_active,
      });
    }
  }

  const closeEditor = () => {
    setEditing(null);
    setDraft(null);
    setError("");
  };

  const patch = (changes: Partial<Draft>) => {
    setDraft((current) => (current ? { ...current, ...changes } : current));
  };

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft || !editing) return;
    const name = draft.name.trim();
    if (!name) {
      setError(t("services.errors.nameRequired"));
      return;
    }
    const depositNum = draft.depositAmount === "" ? null : Number(draft.depositAmount);
    if (draft.requiresDeposit && depositNum !== null && draft.price > 0 && depositNum > draft.price) {
      setError(t("services.errors.depositExceedsPrice"));
      return;
    }

    setBusy(true);
    setError("");
    const payload = {
      name,
      description: draft.description.trim(),
      price: Number(draft.price) || 0,
      currency: draft.currency || currency,
      duration_minutes: Number(draft.durationMinutes) || 30,
      modality: draft.modality,
      requires_deposit: draft.requiresDeposit,
      deposit_amount: draft.requiresDeposit ? depositNum : null,
      requirements: draft.requirements.trim(),
      is_active: draft.isActive,
    };

    try {
      if (editing === "new") {
        await api<Service>(`${apiBase}/services`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        toast.success(t("services.saved"));
      } else {
        await api<Service>(`${apiBase}/services/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        toast.success(t("services.saved"));
      }
      closeEditor();
      await load();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    setError("");
    try {
      await api(`${apiBase}/services/${deleting.id}`, { method: "DELETE" });
      toast.success(t("services.deleted"));
      setDeleting(null);
      await load();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  const modalityIcon = (modality: ServiceModality) => {
    switch (modality) {
      case "presencial":
        return <MapPin size={13} />;
      case "online":
        return <Laptop size={13} />;
      case "a_domicilio":
        return <Home size={13} />;
    }
  };

  return (
    <>
      <div className="section-head">
        <div>
          <h2>{t("services.title")}</h2>
          <p>{t("services.count", { count: items.length })}</p>
        </div>
        {canManage && (
          <button type="button" className="button primary" onClick={() => openEditor("new")}>
            <Plus size={16} /> {t("services.add")}
          </button>
        )}
      </div>

      <div className="table-shell" style={{ marginTop: 12 }}>
        {loading ? (
          <div className="page-loading">
            <LoaderCircle className="spin" size={24} />
          </div>
        ) : items.length ? (
          <ul className="professionals-list">
            {items.map((svc) => (
              <li key={svc.id} className="professionals-card">
                <div
                  className="professionals-avatar"
                  style={{ background: "#2563eb", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center" }}
                >
                  <Briefcase size={20} />
                </div>
                <div className="professionals-info" style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <strong>{svc.name}</strong>
                    <span className={`mini-badge ${svc.is_active ? "human" : "resolved"}`}>
                      {svc.is_active ? t("services.active") : t("services.inactive")}
                    </span>
                    <span className="mini-badge channel-badge" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                      {modalityIcon(svc.modality)} {t(`services.modality.${svc.modality}`)}
                    </span>
                  </div>

                  {svc.description && (
                    <small style={{ color: "var(--muted)", margin: "4px 0" }}>{svc.description}</small>
                  )}

                  <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 4, fontSize: 13 }}>
                    <span style={{ fontWeight: 600, color: "var(--foreground)" }}>
                      {svc.currency} {svc.price.toFixed(2)}
                    </span>
                    <span style={{ color: "var(--muted)", display: "inline-flex", alignItems: "center", gap: 4 }}>
                      <Clock size={13} /> {t("services.durationMinutes", { minutes: svc.duration_minutes })}
                    </span>
                    {svc.requires_deposit && (
                      <span className="mini-badge" style={{ background: "rgba(234, 88, 12, 0.12)", color: "#ea580c" }}>
                        {svc.deposit_amount !== null
                          ? t("services.depositBadge", { amount: `${svc.currency} ${svc.deposit_amount.toFixed(2)}` })
                          : t("services.deposit")}
                      </span>
                    )}
                  </div>

                  {svc.requirements && (
                    <small style={{ color: "var(--muted-dark)", marginTop: 4, display: "block" }}>
                      <strong>{t("services.requirements")}:</strong> {svc.requirements}
                    </small>
                  )}
                </div>

                {canManage && (
                  <div className="professionals-actions">
                    <button
                      type="button"
                      className="icon-button"
                      onClick={() => openEditor(svc)}
                      title={t("services.edit")}
                      aria-label={t("services.edit")}
                    >
                      <Pencil size={15} />
                    </button>
                    <button
                      type="button"
                      className="icon-button danger"
                      onClick={() => {
                        setError("");
                        setDeleting(svc);
                      }}
                      title={t("services.delete")}
                      aria-label={t("services.delete")}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={<Briefcase />}
            title={t("services.emptyTitle")}
            description={canManage ? t("services.emptyDescription") : t("services.emptyReadOnly")}
            action={
              canManage ? (
                <button type="button" className="button primary" onClick={() => openEditor("new")}>
                  <Plus size={16} /> {t("services.add")}
                </button>
              ) : undefined
            }
          />
        )}
      </div>

      {/* Editor Modal */}
      <Modal
        open={editing !== null}
        title={editing === "new" ? t("services.form.newTitle") : t("services.form.editTitle")}
        onClose={closeEditor}
      >
        {draft && (
          <form className="modal-form" onSubmit={save}>
            <div className="form-grid">
              <label>
                {t("services.form.name")}
                <input
                  value={draft.name}
                  required
                  maxLength={180}
                  onChange={(e) => patch({ name: e.target.value })}
                  placeholder={t("services.form.namePlaceholder")}
                  autoFocus
                />
              </label>
              <label>
                {t("services.form.modality")}
                <select
                  value={draft.modality}
                  onChange={(e) => patch({ modality: e.target.value as ServiceModality })}
                >
                  <option value="presencial">{t("services.modality.presencial")}</option>
                  <option value="online">{t("services.modality.online")}</option>
                  <option value="a_domicilio">{t("services.modality.a_domicilio")}</option>
                </select>
              </label>
            </div>

            <label>
              {t("services.form.description")}
              <textarea
                value={draft.description}
                rows={2}
                maxLength={2000}
                onChange={(e) => patch({ description: e.target.value })}
                placeholder={t("services.form.descriptionPlaceholder")}
              />
            </label>

            <div className="form-grid">
              <label>
                {t("services.form.price")} ({draft.currency})
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={draft.price}
                  onChange={(e) => patch({ price: parseFloat(e.target.value) || 0 })}
                />
              </label>

              <label>
                {t("services.form.duration")}
                <select
                  value={draft.durationMinutes}
                  onChange={(e) => patch({ durationMinutes: Number(e.target.value) })}
                >
                  {DURATION_PRESETS.map((m) => (
                    <option key={m} value={m}>
                      {t("services.durationMinutes", { minutes: m })}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12, marginTop: 4 }}>
              <label className="switch-row" style={{ marginBottom: draft.requiresDeposit ? 10 : 0 }}>
                <span>
                  <strong>{t("services.form.requiresDeposit")}</strong>
                  <small>{t("services.form.requiresDepositHint")}</small>
                </span>
                <input
                  type="checkbox"
                  checked={draft.requiresDeposit}
                  onChange={(e) => patch({ requiresDeposit: e.target.checked })}
                />
              </label>

              {draft.requiresDeposit && (
                <label style={{ marginTop: 8 }}>
                  {t("services.form.depositAmount")} ({draft.currency})
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={draft.depositAmount}
                    onChange={(e) => patch({ depositAmount: e.target.value === "" ? "" : parseFloat(e.target.value) || 0 })}
                    placeholder="0.00"
                  />
                </label>
              )}
            </div>

            <label>
              {t("services.form.requirements")}
              <textarea
                value={draft.requirements}
                rows={2}
                maxLength={2000}
                onChange={(e) => patch({ requirements: e.target.value })}
                placeholder={t("services.form.requirementsPlaceholder")}
              />
            </label>

            <label className="switch-row">
              <span>
                <strong>{t("services.form.active")}</strong>
                <small>{t("services.form.activeHint")}</small>
              </span>
              <input
                type="checkbox"
                checked={draft.isActive}
                onChange={(e) => patch({ isActive: e.target.checked })}
              />
            </label>

            {error && <Alert>{error}</Alert>}

            <div className="modal-actions">
              <button type="button" className="button" onClick={closeEditor}>
                {t("common.cancel")}
              </button>
              <button className="button primary" disabled={busy}>
                {busy ? <LoaderCircle className="spin" size={16} /> : t("services.form.save")}
              </button>
            </div>
          </form>
        )}
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        open={deleting !== null}
        title={t("services.deleteTitle", { name: deleting?.name ?? "" })}
        description={t("services.deleteDescription")}
        onClose={() => setDeleting(null)}
      >
        <div className="modal-form">
          {error && <Alert>{error}</Alert>}
          <div className="modal-actions">
            <button type="button" className="button" onClick={() => setDeleting(null)}>
              {t("common.cancel")}
            </button>
            <button type="button" className="button danger" disabled={busy} onClick={remove}>
              {busy ? <LoaderCircle className="spin" size={16} /> : <><Trash2 size={15} /> {t("services.deleteConfirm")}</>}
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
}
