"use client";

// The modules the platform left this agency, read once per page load.
//
// The server refuses a line of a channel whose module is switched off
// (app/channel_quotas.py), so the panel has to know before it offers the
// option: a button that fails when it is pressed is worse than no button. The
// list travels on the session, and it is cached for the page because every
// channel screen on screen wants the same answer and the answer cannot change
// while the page is open.

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { User } from "@/types";

let cached: string[] | null = null;
let pending: Promise<string[] | null> | null = null;

/** The one request every screen on the page shares.
 *
 * A promise and not a boolean guard, because development mounts effects twice:
 * the first mount starts the request and is then torn down, and the second one
 * would find the work "already done" with nothing to show for it. Sharing the
 * promise means both mounts read the same answer whenever it lands. */
function loadModules(): Promise<string[] | null> {
  pending ??= api<User>("/auth/me")
    .then((user) => (cached = user.agency?.modules?.length ? user.agency.modules : null))
    .catch(() => (cached = null));
  return pending;
}

export function useAgencyModules(enabled: boolean): string[] | null {
  const [modules, setModules] = useState<string[] | null>(cached);

  useEffect(() => {
    // The portal has its own session and its own list of functions; this is the
    // agency's, and asking for it there would fail.
    if (!enabled) return;
    let active = true;
    void loadModules().then((value) => { if (active) setModules(value); });
    return () => { active = false; };
  }, [enabled]);

  return modules;
}

/** Whether a module is on. ``null`` means the catalog has not arrived yet, and
 * the caller should offer everything rather than hide on a guess. */
export function moduleAllowed(modules: string[] | null, key: string): boolean {
  return modules === null || modules.includes(key);
}

/** The module behind a channel type. The catalog writes "whatsapp_cloud" where
 * the route says "whatsapp-cloud", and guessing the join is how a switch ends
 * up read against the wrong name. */
const MODULE_OF_CHANNEL: Record<string, string> = {
  whatsapp: "channels.whatsapp",
  "whatsapp-cloud": "channels.whatsapp_cloud",
  webchat: "channels.webchat",
  instagram: "channels.instagram",
  messenger: "channels.messenger",
};

export function moduleForChannel(type: string): string {
  return MODULE_OF_CHANNEL[type] ?? `channels.${type.replace(/-/g, "_")}`;
}
