"use client";

// Where the lead card runs. The agency's inboxes and the client portal's share
// one implementation (components/lead-card/*); what differs is the API path of
// every call and what the person may do, so both travel here instead of being
// hard-coded in the card.
//
//   Agency  /conversations/{id}/lead, /clients/{clientId}/lead-fields, ...
//   Portal  /portal/{slug}/conversations/{id}/lead, /portal/{slug}/lead-fields, ...
//
// The scope object is a dependency of the card's loaders: build it with useMemo
// (or the helpers below inside one) so its identity only changes with the client.

import { createContext, useContext, type ReactNode } from "react";

export type LeadScope = {
  /** GET (the card) and PATCH (responsible user, custom values) of one lead. */
  leadPath: (conversationId: string) => string;
  /** PATCH of the lead's stage and budget. */
  pipelinePath: (conversationId: string) => string;
  /** PATCH of a contact's own fields. */
  contactPath: (contactId: string) => string;
  /** PUT of a contact's whole tag set. */
  contactTagsPath: (contactId: string) => string;
  /** Base of the client's lead routes (merge candidates and merge): `{leadsBase}/leads/...`. */
  leadsBase: string;
  /** GET/POST of the client's custom fields. */
  fieldsPath: string;
  /** PATCH/DELETE of one custom field. */
  fieldPath: (fieldId: string) => string;
  /** GET of the client's pipeline stages. */
  stagesPath: string;
  /** GET of the people a lead can be assigned to. */
  membersPath: string;
  /** GET/POST of the client's tag catalog. */
  tagCatalogPath: string;
  /** The Configure button and the field manager. */
  canManageFields: boolean;
  /** Inline editing of the contact's name, phone, email, company and tags. */
  canEditContact: boolean;
  /** Creating a new tag from the picker. */
  canCreateTags: boolean;
  /** Address of the lead a person can paste elsewhere; null when there is none to offer. */
  leadLink: ((number: number) => string) | null;
};

/** The agency's scope for one client; the leads of every client are reached by the conversation's id alone. */
export function agencyLeadScope(clientId: string): LeadScope {
  const client = `/clients/${clientId}`;
  return {
    leadsBase: client,
    leadPath: (id) => `/conversations/${id}/lead`,
    pipelinePath: (id) => `/conversations/${id}/pipeline`,
    contactPath: (id) => `${client}/contacts/${id}`,
    contactTagsPath: (id) => `${client}/contacts/${id}/tags`,
    fieldsPath: `${client}/lead-fields`,
    fieldPath: (id) => `${client}/lead-fields/${id}`,
    stagesPath: `${client}/pipeline/stages`,
    membersPath: `${client}/members`,
    tagCatalogPath: `${client}/contact-tags`,
    canManageFields: true,
    canEditContact: true,
    canCreateTags: true,
    leadLink: (number) => `${window.location.origin}/clients/${clientId}/inbox/${number}`,
  };
}

/** The portal's scope; `can` is the portal's permission check and `leadHref` builds the lead's address (portalPath). */
export function portalLeadScope(slug: string, can: (key: string) => boolean, leadHref: (number: number) => string): LeadScope {
  const base = `/portal/${slug}`;
  return {
    leadsBase: base,
    leadPath: (id) => `${base}/conversations/${id}/lead`,
    pipelinePath: (id) => `${base}/conversations/${id}/pipeline`,
    contactPath: (id) => `${base}/contacts/${id}`,
    contactTagsPath: (id) => `${base}/contacts/${id}/tags`,
    fieldsPath: `${base}/lead-fields`,
    fieldPath: (id) => `${base}/lead-fields/${id}`,
    stagesPath: `${base}/pipeline/stages`,
    membersPath: `${base}/members`,
    tagCatalogPath: `${base}/tags`,
    canManageFields: can("fields.manage"),
    canEditContact: can("contacts.manage"),
    canCreateTags: can("tags.manage"),
    leadLink: (number) => `${window.location.origin}${leadHref(number)}`,
  };
}

const ScopeContext = createContext<LeadScope | null>(null);

export function LeadScopeProvider({ scope, children }: { scope: LeadScope; children: ReactNode }) {
  return <ScopeContext.Provider value={scope}>{children}</ScopeContext.Provider>;
}

export function useLeadScope(): LeadScope {
  const scope = useContext(ScopeContext);
  if (!scope) throw new Error("useLeadScope must be used within a LeadScopeProvider");
  return scope;
}
