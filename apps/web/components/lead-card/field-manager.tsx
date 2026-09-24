"use client";

import { FormEvent, useEffect, useState } from "react";
import { ArrowDown, ArrowUp, LoaderCircle, Pencil, Plus, Trash2, X } from "lucide-react";
import { Modal } from "@/components/ui";
import { useToast } from "@/components/toast";
import { useLeadScope } from "@/components/lead-card/scope";
import { api, messageFrom } from "@/lib/api";
import { useT, type I18nKey } from "@/lib/i18n";
import type { LeadField, LeadFieldType } from "@/types";

export const LEAD_FIELD_TYPES: readonly LeadFieldType[] = ["text", "number", "date", "select", "checkbox"];

const TYPE_LABEL: Record<LeadFieldType, I18nKey> = {
  text: "lead.fields.typeText",
  number: "lead.fields.typeNumber",
  date: "lead.fields.typeDate",
  select: "lead.fields.typeSelect",
  checkbox: "lead.fields.typeCheckbox",
};

/** One option per line, without blanks or repeats. */
function parseOptions(text: string): string[] {
  const seen = new Set<string>();
  const options: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const option = line.trim();
    if (option && !seen.has(option)) { seen.add(option); options.push(option); }
  }
  return options;
}

/** The "Configure" screen of the lead card: the client's custom fields, in the
 * order the card shows them, with add, edit (label and options; the type is set
 * once), reorder and delete. Every change is saved as it is made. */
export function FieldManager({ open, fields, onClose, onChanged }: {
  open: boolean;
  fields: LeadField[];
  onClose: () => void;
  /** Called after any change, so the card reloads its fields and values. */
  onChanged: () => void;
}) {
  const t = useT();
  const toast = useToast();
  const scope = useLeadScope();
  // The list as shown: it follows the card's fields, and reorders at once while the server catches up.
  const [items, setItems] = useState<LeadField[]>(fields);
  useEffect(() => { setItems(fields); }, [fields]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [editOptions, setEditOptions] = useState("");
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [newType, setNewType] = useState<LeadFieldType>("text");
  const [newOptions, setNewOptions] = useState("");
  const [adding, setAdding] = useState(false);

  function startEdit(field: LeadField) {
    setConfirmingId(null);
    setEditingId(field.id);
    setEditLabel(field.label);
    setEditOptions(field.options.join("\n"));
  }

  async function saveEdit(event: FormEvent<HTMLFormElement>, field: LeadField) {
    event.preventDefault();
    const label = editLabel.trim();
    if (!label) return;
    const body: Record<string, unknown> = { label };
    if (field.type === "select") {
      const options = parseOptions(editOptions);
      if (!options.length) { toast.error(t("lead.fields.optionsRequired")); return; }
      body.options = options;
    }
    setBusyId(field.id);
    try {
      await api<LeadField>(scope.fieldPath(field.id), { method: "PATCH", body: JSON.stringify(body) });
      setEditingId(null);
      toast.success(t("lead.fields.saved"));
      onChanged();
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusyId(null); }
  }

  async function remove(field: LeadField) {
    setBusyId(field.id);
    try {
      await api(scope.fieldPath(field.id), { method: "DELETE" });
      setConfirmingId(null);
      toast.success(t("lead.fields.deleted"));
      onChanged();
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusyId(null); }
  }

  // Swaps two neighbours' `position` values. When they happen to be equal the
  // list index stands in, so a swap always changes the order.
  async function move(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= items.length) return;
    const a = items[index];
    const b = items[target];
    const [posA, posB] = a.position === b.position ? [target, index] : [b.position, a.position];
    const reordered = [...items];
    reordered[index] = { ...b, position: posB };
    reordered[target] = { ...a, position: posA };
    setItems(reordered);
    try {
      await Promise.all([
        api(scope.fieldPath(a.id), { method: "PATCH", body: JSON.stringify({ position: posA }) }),
        api(scope.fieldPath(b.id), { method: "PATCH", body: JSON.stringify({ position: posB }) }),
      ]);
      onChanged();
    } catch (err) { toast.error(messageFrom(err)); setItems(fields); onChanged(); }
  }

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const label = newLabel.trim();
    if (!label || adding) return;
    const body: Record<string, unknown> = { label, type: newType };
    if (newType === "select") {
      const options = parseOptions(newOptions);
      if (!options.length) { toast.error(t("lead.fields.optionsRequired")); return; }
      body.options = options;
    }
    setAdding(true);
    try {
      await api<LeadField>(scope.fieldsPath, { method: "POST", body: JSON.stringify(body) });
      setNewLabel(""); setNewOptions(""); setNewType("text");
      toast.success(t("lead.fields.added"));
      onChanged();
    } catch (err) { toast.error(messageFrom(err)); } finally { setAdding(false); }
  }

  return <Modal open={open} title={t("lead.fields.title")} description={t("lead.fields.copy")} onClose={onClose} wide>
    <div className="modal-form lead-fields-manager">
      <div className="pipeline-automation-list">
        {items.length === 0 && <p className="field-help">{t("lead.fields.empty")}</p>}
        {items.map((field, index) => editingId === field.id
          ? <form key={field.id} className="lead-field-edit" onSubmit={(event) => saveEdit(event, field)}>
              <label>{t("lead.fields.label")}<input value={editLabel} onChange={(e) => setEditLabel(e.target.value)} maxLength={80} autoFocus required /></label>
              <label>{t("lead.fields.type")}<input value={t(TYPE_LABEL[field.type])} readOnly disabled /><span className="field-help">{t("lead.fields.typeLocked")}</span></label>
              {field.type === "select" && <label>{t("lead.fields.options")}<textarea rows={4} value={editOptions} onChange={(e) => setEditOptions(e.target.value)} /></label>}
              <div className="lead-field-edit-actions">
                <button type="button" className="button" onClick={() => setEditingId(null)}>{t("common.cancel")}</button>
                <button className="button primary" disabled={busyId === field.id || !editLabel.trim()}>{busyId === field.id ? <LoaderCircle className="spin" size={15} /> : t("lead.fields.save")}</button>
              </div>
            </form>
          : <div key={field.id} className="pipeline-automation-row lead-field-row">
              <div className="pipeline-automation-order">
                <button type="button" className="icon-button small" disabled={index === 0} onClick={() => move(index, -1)} aria-label={t("lead.fields.moveUp")} title={t("lead.fields.moveUp")}><ArrowUp size={13} /></button>
                <button type="button" className="icon-button small" disabled={index === items.length - 1} onClick={() => move(index, 1)} aria-label={t("lead.fields.moveDown")} title={t("lead.fields.moveDown")}><ArrowDown size={13} /></button>
              </div>
              <span className="lead-field-name"><strong>{field.label}</strong><small>{t(TYPE_LABEL[field.type])}{field.type === "select" && field.options.length > 0 ? ` · ${field.options.join(", ")}` : ""}</small></span>
              {busyId === field.id && <LoaderCircle size={14} className="spin" />}
              {confirmingId === field.id
                ? <span className="pipeline-automation-confirm">
                    <span className="field-help">{t("lead.fields.deleteCopy")}</span>
                    <button type="button" className="button danger small" disabled={busyId === field.id} onClick={() => remove(field)}>{t("lead.fields.deleteConfirm")}</button>
                    <button type="button" className="icon-button small" onClick={() => setConfirmingId(null)} aria-label={t("common.cancel")}><X size={13} /></button>
                  </span>
                : <span className="pipeline-automation-confirm">
                    <button type="button" className="icon-button small" onClick={() => startEdit(field)} aria-label={t("lead.fields.edit")} title={t("lead.fields.edit")}><Pencil size={14} /></button>
                    <button type="button" className="icon-button small danger-icon" onClick={() => { setEditingId(null); setConfirmingId(field.id); }} aria-label={t("lead.fields.delete")} title={t("lead.fields.delete")}><Trash2 size={14} /></button>
                  </span>}
            </div>)}
      </div>
      <form className="lead-field-add" onSubmit={add}>
        <div className="form-grid">
          <label>{t("lead.fields.label")}<input value={newLabel} onChange={(e) => setNewLabel(e.target.value)} placeholder={t("lead.fields.labelPlaceholder")} maxLength={80} /></label>
          <label>{t("lead.fields.type")}<select value={newType} onChange={(e) => setNewType(e.target.value as LeadFieldType)}>{LEAD_FIELD_TYPES.map((type) => <option key={type} value={type}>{t(TYPE_LABEL[type])}</option>)}</select></label>
        </div>
        {newType === "select" && <label>{t("lead.fields.options")}<textarea rows={4} value={newOptions} onChange={(e) => setNewOptions(e.target.value)} /></label>}
        <div><button type="submit" className="button secondary" disabled={adding || !newLabel.trim()}>{adding ? <LoaderCircle size={15} className="spin" /> : <Plus size={15} />} {t("lead.fields.add")}</button></div>
      </form>
    </div>
  </Modal>;
}
