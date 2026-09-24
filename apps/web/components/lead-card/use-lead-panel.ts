"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "openlivery.lead-panel";
/** Below this width of the inbox itself there is no room for list, thread and panel side by side (list 240 + thread 380 + panel 360), so the panel takes the whole screen instead. */
export const LEAD_OVERLAY_BELOW = 1000;

function readStored(): boolean {
  try { return window.localStorage.getItem(STORAGE_KEY) === "1"; } catch { return false; }
}

/** Whether the lead card is open next to the thread.
 *
 * On a wide inbox the choice is remembered in this browser and stays while the
 * person moves between conversations. Where the panel becomes a full-screen
 * overlay it starts closed each time instead: a remembered "open" would cover
 * the list on every click. Give `attachLayout` to the inbox's outer element: its
 * width decides between the two, and the element gets the `lead-overlay` class
 * (see globals.css) when the overlay applies. */
export function useLeadPanel() {
  const [layoutEl, setLayoutEl] = useState<HTMLElement | null>(null);
  const [wideOpen, setWideOpen] = useState(readStored);
  const [overlayOpen, setOverlayOpen] = useState(false);
  const [overlay, setOverlay] = useState(false);

  useEffect(() => {
    if (!layoutEl) return;
    const observer = new ResizeObserver(() => setOverlay(layoutEl.clientWidth < LEAD_OVERLAY_BELOW));
    observer.observe(layoutEl);
    return () => observer.disconnect();
  }, [layoutEl]);

  const open = overlay ? overlayOpen : wideOpen;
  const setOpen = useCallback((next: boolean) => {
    if (overlay) { setOverlayOpen(next); return; }
    setWideOpen(next);
    try { window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0"); } catch { /* the panel still toggles; only the memory of it is lost */ }
  }, [overlay]);

  return { open, setOpen, overlay, attachLayout: setLayoutEl };
}
