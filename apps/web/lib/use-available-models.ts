"use client";

// Which models this workspace can actually pick, asked from the API: the
// catalog is what OpenRouter serves right now, and a deployment may narrow it
// (to what a shared key covers, say). While loading or on error the static
// seed lists apply unchanged.

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { setLiveModels, type LiveModel } from "@/lib/providers";

export type AvailableModels = {
  chat: Record<string, string[]>;
  image: string[];
  audio: string[];
  embedding?: string[];
};

// Kept per API prefix: the agency panel and a client portal ask different
// routes (the portal's live under /portal/{slug}/manage) and may not see the same list.
const cached = new Map<string, AvailableModels>();
const metadataLoaded = new Set<string>();

export function useAvailableModels(apiBase = ""): AvailableModels | null {
  const [data, setData] = useState<AvailableModels | null>(cached.get(apiBase) ?? null);
  useEffect(() => {
    // Names, context windows and prices for every model on offer, so the
    // pickers can label what they list.
    if (!metadataLoaded.has(apiBase)) {
      metadataLoaded.add(apiBase);
      api<LiveModel[]>(`${apiBase}/catalog/models`).then(setLiveModels).catch(() => { metadataLoaded.delete(apiBase); });
    }
    const hit = cached.get(apiBase);
    if (hit) { setData(hit); return; }
    api<AvailableModels>(`${apiBase}/catalog/available`)
      .then((payload) => {
        cached.set(apiBase, payload);
        setData(payload);
      })
      .catch(() => {});
  }, [apiBase]);
  return data;
}

/** The ids the API allows, with the ones the static seed knows first (they
 * carry curated labels and the recommended default), then everything else
 * OpenRouter serves. An unknown or empty answer keeps the static list, so the
 * UI never ends up with nothing to offer. */
export function narrowModels(list: readonly string[], allowed?: string[] | null): string[] {
  if (!allowed || !allowed.length) return [...list];
  const set = new Set(allowed);
  const known = list.filter((id) => set.has(id));
  const knownSet = new Set(known);
  return [...known, ...allowed.filter((id) => !knownSet.has(id))];
}
