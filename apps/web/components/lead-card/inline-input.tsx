"use client";

import { useEffect, useRef, useState, type HTMLAttributes } from "react";

/** A text box that looks like plain text until it is hovered or focused, and
 * saves by itself: on blur or Enter when the text changed, never on Escape
 * (which puts the old text back). `display` writes the value another way while
 * the box is not being edited (a formatted amount, say). */
export function InlineInput({ value, onCommit, placeholder, ariaLabel, type = "text", inputMode, readOnly = false, maxLength, display }: {
  value: string;
  onCommit: (next: string) => void | Promise<void>;
  placeholder?: string;
  ariaLabel: string;
  type?: "text" | "number";
  inputMode?: HTMLAttributes<HTMLInputElement>["inputMode"];
  readOnly?: boolean;
  maxLength?: number;
  display?: (value: string) => string;
}) {
  const [draft, setDraft] = useState(value);
  const [focused, setFocused] = useState(false);
  // Escape sets this so the blur it causes does not save what was typed.
  const cancelled = useRef(false);
  // A save from elsewhere (another field, a reload) puts the new value in the box.
  useEffect(() => { setDraft(value); }, [value]);

  function commit() {
    setFocused(false);
    if (cancelled.current) { cancelled.current = false; setDraft(value); return; }
    const next = draft.trim();
    if (next !== value.trim()) void onCommit(next);
  }

  return <input
    className="lead-input"
    type={type}
    inputMode={inputMode}
    step={type === "number" ? "any" : undefined}
    value={focused || !display ? draft : display(draft)}
    placeholder={placeholder}
    aria-label={ariaLabel}
    maxLength={maxLength}
    readOnly={readOnly}
    onFocus={() => setFocused(true)}
    onChange={(event) => setDraft(event.target.value)}
    onBlur={commit}
    onKeyDown={(event) => {
      if (event.key === "Enter") event.currentTarget.blur();
      else if (event.key === "Escape") { cancelled.current = true; event.stopPropagation(); event.currentTarget.blur(); }
    }}
  />;
}

/** "1,234.56", "1.234,56", "1234.5" and "$ 1 200" as a number; null for empty, undefined when it is not an amount. */
export function parseAmount(text: string): number | null | undefined {
  const cleaned = text.replace(/[^\d.,-]/g, "");
  if (!cleaned) return null;
  const lastDot = cleaned.lastIndexOf(".");
  const lastComma = cleaned.lastIndexOf(",");
  let normalized: string;
  if (lastDot >= 0 && lastComma >= 0) {
    const decimal = lastDot > lastComma ? "." : ",";
    normalized = cleaned.split(decimal === "." ? "," : ".").join("").replace(decimal, ".");
  } else if (lastComma >= 0 || lastDot >= 0) {
    const sep = lastComma >= 0 ? "," : ".";
    const parts = cleaned.split(sep);
    // One separator followed by one or two digits is a decimal point; anything else groups thousands.
    normalized = parts.length === 2 && parts[1].length <= 2 ? `${parts[0]}.${parts[1]}` : parts.join("");
  } else {
    normalized = cleaned;
  }
  const value = Number(normalized);
  return Number.isFinite(value) && value >= 0 ? value : undefined;
}
