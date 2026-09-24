"use client";

import { useState } from "react";
import { Check, Plus, X } from "lucide-react";
import { useT } from "@/lib/i18n";
import { tagStyle } from "@/lib/tags";
import { fold, foldIncludes } from "@/lib/text";
import type { ContactTag } from "@/types";

export type TagLike = { id: string; name: string; color: string };

/** A contact's tag chips (each removable) and the "+ Tag" picker that searches
 * the client's tag catalog and, where allowed, creates a new one from what was
 * typed. Shared by the portal's Contacts screen and the lead card. The parent
 * owns the data: it applies `onToggle` (a tag chosen or removed) and `onCreate`
 * (a new name typed) and passes back the resulting `value`. */
export function TagEditor({ tags, value, onToggle, onCreate, canCreate = false, busy = false, readOnly = false }: {
  /** The client's tag catalog. */
  tags: ContactTag[];
  /** The tags the contact has. */
  value: TagLike[];
  onToggle: (tag: TagLike) => void;
  onCreate?: (name: string) => void | Promise<void>;
  canCreate?: boolean;
  busy?: boolean;
  /** Chips only: no remove buttons and no picker. */
  readOnly?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const trimmed = query.trim();
  const matches = tags.filter((tag) => !trimmed || foldIncludes(tag.name, trimmed));
  const exact = tags.some((tag) => fold(tag.name) === fold(trimmed));
  const mayCreate = canCreate && Boolean(onCreate);
  const create = async () => {
    if (!trimmed || !onCreate) return;
    await onCreate(trimmed);
    setQuery("");
  };

  return <div className="tag-chips large">
    {value.map((tag) => <span key={tag.id} className="tag-chip" style={tagStyle(tag.color)}>{tag.name}{!readOnly && <button type="button" onClick={() => onToggle(tag)} disabled={busy} title={t("portal.contacts.tags.remove")} aria-label={t("portal.contacts.tags.remove")}><X size={11} /></button>}</span>)}
    {!readOnly && <div className="start-line-wrap">
      <button type="button" className="tag-add" onClick={() => { setQuery(""); setOpen((v) => !v); }} aria-haspopup="menu" aria-expanded={open}><Plus size={13} /> {t("portal.contacts.tags.add")}</button>
      {open && <>
        <div className="menu-backdrop" onClick={() => setOpen(false)} />
        <div className="start-line-menu tag-picker" role="menu">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={mayCreate ? t("portal.contacts.tags.searchOrCreate") : t("portal.contacts.tags.search")} autoFocus onKeyDown={(e) => { if (e.key === "Enter" && mayCreate && trimmed && !exact) { e.preventDefault(); void create(); } if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } }} />
          {matches.map((tag) => { const has = value.some((item) => item.id === tag.id); return <button type="button" key={tag.id} role="menuitemcheckbox" aria-checked={has} onClick={() => onToggle(tag)} disabled={busy}><i className="tag-dot" style={tagStyle(tag.color)} /><span><strong>{tag.name}</strong>{(tag.route_assignee_name || tag.route_team_name) && <small>{t("portal.contacts.tags.routedTo", { team: tag.route_assignee_name ?? tag.route_team_name ?? "" })}</small>}</span>{has && <Check size={14} />}</button>; })}
          {mayCreate && trimmed && !exact && <button type="button" role="menuitem" className="tag-create" onClick={() => void create()} disabled={busy}><Plus size={14} /><span><strong>{t("portal.contacts.tags.create", { name: trimmed })}</strong></span></button>}
          {!tags.length && !trimmed && <small>{t("portal.contacts.tags.emptyHint")}</small>}
        </div>
      </>}
    </div>}
  </div>;
}
