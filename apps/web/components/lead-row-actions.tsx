"use client";

import { Children, useState, type ReactNode } from "react";
import { MoreHorizontal } from "lucide-react";
import { useEscape } from "@/components/messages/use-escape";
import { useT } from "@/lib/i18n";

/** The three-dot menu on a lead row. The entries arrive as children (each a
 * `role="menuitem"` button, which is what `.start-line-menu` styles); the
 * trigger, the backdrop that closes it and Escape are the same everywhere, so
 * they live here instead of in each inbox. */
export function LeadRowActions({ children }: { children?: ReactNode }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  useEscape(open, close);
  const items = Children.count(children);
  return (
    <span className="start-line-wrap">
      <button type="button" className="icon-button" onClick={() => setOpen((v) => !v)} aria-haspopup="menu" aria-expanded={open} aria-label={t("lead.menu")} title={t("lead.menu")}><MoreHorizontal size={16} /></button>
      {open && items > 0 && <>
        <div className="menu-backdrop" onClick={close} />
        <div className="start-line-menu lead-menu" role="menu">
          {children}
        </div>
      </>}
    </span>
  );
}
