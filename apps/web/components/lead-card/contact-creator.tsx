"use client";

// The door out of a lead that arrived without a person.
//
// A lead born on a channel with nothing to identify the writer (a playground
// rehearsal, a visitor who left no number) has no contact, and a lead with no
// contact has nothing to work with: the name is a placeholder, there is nobody
// to tag and nobody to block. Kommo answers the same state with an "Add
// contact" control rather than a card that has gone quiet, and so does this.
//
// The phone is the part that matters: a number that already belongs to somebody
// finds that person, so a lead from a visitor you already know joins their
// record instead of starting a second one. A name on its own still works, it
// simply has nothing to match on.

import { useState } from "react";
import { Plus, UserPlus } from "lucide-react";

import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useToast } from "@/components/toast";
import type { LeadCard } from "@/types";

export function ContactCreator({ onAttached, path }: {
  /** The card comes back with the person in it, so the host swaps it in and
   * every part the contact unlocks opens at once. */
  onAttached: (card: LeadCard) => void;
  /** Where to POST; the scope builds it, the same way it builds every other call. */
  path: string;
}) {
  const t = useT();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    if (busy) return;
    setBusy(true);
    try {
      const card = await api<LeadCard>(path, {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), phone: phone.trim() }),
      });
      onAttached(card);
      setOpen(false);
      toast.success(t("lead.contactAdded"));
    } catch (err) {
      toast.error(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="tag-add" onClick={() => setOpen(true)}>
        <UserPlus size={13} /> {t("lead.addContact")}
      </button>
    );
  }
  return (
    <div className="lead-contact-creator">
      <label>
        {t("lead.addContactName")}
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={180} autoFocus
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } }} />
      </label>
      <label>
        {t("lead.addContactPhone")}
        <input value={phone} onChange={(e) => setPhone(e.target.value)} maxLength={40} inputMode="tel"
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } }} />
      </label>
      <div className="lead-contact-creator-actions">
        <button type="button" className="button secondary small" onClick={() => setOpen(false)} disabled={busy}>{t("common.cancel")}</button>
        <button type="button" className="button primary small" onClick={() => void save()} disabled={busy || (!name.trim() && !phone.trim())}>
          <Plus size={13} /> {t("lead.addContactSave")}
        </button>
      </div>
    </div>
  );
}
