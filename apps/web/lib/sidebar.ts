"use client";

import { useCallback, useState } from "react";

// Which shell is folding its navigation: the agency app or the client portal.
export type SidebarSurface = "agency" | "portal";

// One preference per surface: the two are used by different people on the same
// browser, so folding one must not move the other. Both stay on this browser
// and never travel to the agency or to the client.
const STORAGE_KEYS: Record<SidebarSurface, string> = {
  agency: "hunterai.sidebar",
  portal: "hunterai.portal-sidebar",
};

const LEGACY_STORAGE_KEYS: Record<SidebarSurface, string> = {
  agency: "openlivery.sidebar",
  portal: "openlivery.portal-sidebar",
};

// Carried by the shell root (`.app-layout` / `.portal-app`); the stylesheet
// folds the navigation column from it, and only above 901px.
export const NAV_COLLAPSED_CLASS = "nav-collapsed";

const COLLAPSED = "collapsed";
const EXPANDED = "expanded";

// Unlike the theme, nothing stamps this before paint: both shells render their
// navigation on the client only — AppShell behind `/auth/me`, the portal behind
// its own session — so the first render already knows the answer and a reload
// never flashes the expanded column. A future change that server-renders either
// shell would have to move this to the theme's pattern (an attribute on <html>
// stamped by lib/theme-script.ts).
function readStored(surface: SidebarSurface): boolean {
  if (typeof window === "undefined") return false;
  try {
    const val = window.localStorage.getItem(STORAGE_KEYS[surface]) ?? window.localStorage.getItem(LEGACY_STORAGE_KEYS[surface]);
    return val === COLLAPSED;
  } catch {
    // Storage can be unavailable (private mode, blocked site data).
    return false;
  }
}

export function useCollapsibleNav(surface: SidebarSurface) {
  const [collapsed, setCollapsed] = useState(() => readStored(surface));

  const toggle = useCallback(() => {
    setCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(STORAGE_KEYS[surface], next ? COLLAPSED : EXPANDED);
      } catch {
        // The navigation still folds; only the memory of it is lost.
      }
      return next;
    });
  }, [surface]);

  return { collapsed, toggle };
}
