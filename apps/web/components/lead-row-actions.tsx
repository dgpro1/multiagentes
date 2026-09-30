"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Copy, MoreHorizontal, Pin, PinOff } from "lucide-react";
import { useEscape } from "@/components/messages/use-escape";
import { useToast } from "@/components/toast";
import { messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** Pinned rows first, most recent pin first, everything else left in the order
 * it came in. Every inbox orders its list the same way on the server, so this
 * is how a row moves the moment it is pinned, before the list is read again.
 * The sort is stable, which is what keeps the untouched rows in place. */
export function pinnedFirst<T extends { pinned_at?: string | null }>(rows: T[]): T[] {
  const at = (row: T) => (row.pinned_at ? Date.parse(row.pinned_at) : NaN);
  return [...rows].sort((a, b) => {
    const pa = at(a);
    const pb = at(b);
    if (Number.isNaN(pa)) return Number.isNaN(pb) ? 0 : 1;
    if (Number.isNaN(pb)) return -1;
    return pb - pa;
  });
}

/** The three-dot menu on a lead row: resolve it, pin it, or copy the lead's
 * own address. The trigger, the backdrop, Escape and the focus handling are the
 * same in every inbox, so they live here; what each surface *does* with an
 * action (which endpoint, which address) is passed in by its inbox. */
export function LeadRowActions({
  pinned = false,
  onResolve,
  onTogglePin,
  /** The lead's address, copied as it is written. Absolute, or the browser
   * resolves it against the page. */
  href,
  disabled = false,
}: {
  pinned?: boolean;
  onResolve: () => void;
  onTogglePin: () => void;
  href: string;
  disabled?: boolean;
}) {
  const t = useT();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  const close = useCallback((returnFocus = false) => {
    setOpen(false);
    if (returnFocus) trigger.current?.focus();
  }, []);

  useEscape(open, () => close(true));

  // The menu takes focus when it opens and gives it back when it closes, so the
  // keyboard never lands on the row behind it.
  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLElement>("[role='menuitem']")?.focus();
  }, [open]);

  // A click or a Tab outside the wrap dismisses the menu, the way a menu is
  // expected to behave when the focus moves away from it.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    };
    const onFocusIn = (event: FocusEvent) => {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("focusin", onFocusIn);
    };
  }, [open]);

  const items = [
    { key: "resolve", label: t("inbox.markResolved"), icon: <CheckCircle2 size={15} />, run: onResolve },
    {
      key: "pin",
      label: pinned ? t("inbox.unpin") : t("inbox.pin"),
      icon: pinned ? <PinOff size={15} /> : <Pin size={15} />,
      run: onTogglePin,
    },
  ];

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Tab") { close(); return; }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const buttons = Array.from(menu.current?.querySelectorAll<HTMLElement>("[role='menuitem']") ?? []);
      if (!buttons.length) return;
      const at = buttons.indexOf(document.activeElement as HTMLElement);
      const next = event.key === "ArrowDown"
        ? (at + 1) % buttons.length
        : (at - 1 + buttons.length) % buttons.length;
      buttons[next].focus();
    }
  }

  function run(action: () => void) {
    close(true);
    action();
  }

  function copy() {
    const absolute = new URL(href, window.location.origin).toString();
    navigator.clipboard?.writeText(absolute).then(() => toast.success(t("lead.linkCopied"))).catch((err) => toast.error(messageFrom(err)));
  }

  return (
    <span className="start-line-wrap" ref={wrap}>
      <button
        type="button"
        className="icon-button"
        ref={trigger}
        disabled={disabled}
        onClick={() => (open ? close(true) : setOpen(true))}
        onKeyDown={(event) => { if (event.key === "ArrowDown") { event.preventDefault(); setOpen(true); } }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("lead.menu")}
        title={t("lead.menu")}
      >
        <MoreHorizontal size={16} />
      </button>
      {open && <>
        <div className="lead-row-backdrop" onClick={() => close()} />
        <div className="start-line-menu lead-menu" role="menu" ref={menu} onKeyDown={onKeyDown}>
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              role="menuitem"
              disabled={disabled}
              onClick={() => run(item.run)}
            >
              {item.icon}
              <span>{item.label}</span>
            </button>
          ))}
          <button type="button" role="menuitem" disabled={disabled} onClick={() => { close(true); copy(); }}>
            <Copy size={15} />
            <span>{t("lead.copyLink")}</span>
          </button>
        </div>
      </>}
    </span>
  );
}
