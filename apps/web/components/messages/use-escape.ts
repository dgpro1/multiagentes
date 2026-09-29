"use client";

import { useEffect } from "react";

/** Runs `onEscape` on Escape while `active` (menus that close before the panel does). */
export function useEscape(active: boolean, onEscape: () => void) {
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onEscape(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, onEscape]);
}
