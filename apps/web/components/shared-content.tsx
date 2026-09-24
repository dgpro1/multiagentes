"use client";

import { useEffect, useMemo, useState } from "react";
import { FileText, Link2 } from "lucide-react";
import { AudioBubble, Lightbox, type GalleryImage } from "@/components/attachments";
import { useT } from "@/lib/i18n";
import type { Attachment, Message } from "@/types";

function linkHost(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

const URL_PATTERN = /https?:\/\/[^\s<>")\]]+/g;

/** Everything sent in a chat grouped into Media (images/videos/audio), Links and
 * Docs tabs. Used by the WhatsApp-style slide-over (MediaPanel) and by the Files
 * tab of the lead card. `onPreviewChange` reports when the image preview opens,
 * so a parent that closes on Escape can leave that key to the preview. */
export function SharedContentList({ messages, urlFor, onPreviewChange }: {
  messages: Message[];
  urlFor: (attachment: Attachment) => string;
  onPreviewChange?: (open: boolean) => void;
}) {
  const t = useT();
  const [tab, setTab] = useState<"media" | "links" | "docs">("media");
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  useEffect(() => {
    onPreviewChange?.(previewIndex !== null);
    return () => onPreviewChange?.(false);
  }, [previewIndex, onPreviewChange]);

  // Newest first, like WhatsApp's shared-content view.
  const media = useMemo(
    () => messages.flatMap((message) => (message.attachments ?? []).filter((a) => a.kind === "image" || a.kind === "video" || a.kind === "audio")).reverse(),
    [messages],
  );
  // Docs = plain files (pdf/txt/…) — anything that is not media or a link.
  const docs = useMemo(
    () => messages.flatMap((message) => (message.attachments ?? []).filter((a) => a.kind === "file")).reverse(),
    [messages],
  );
  const links = useMemo(() => {
    const seen = new Set<string>();
    const found: string[] = [];
    for (const message of messages) {
      for (const url of message.content?.match(URL_PATTERN) ?? []) {
        if (!seen.has(url)) { seen.add(url); found.push(url); }
      }
    }
    return found.reverse();
  }, [messages]);
  const gallery: GalleryImage[] = useMemo(
    () => media.filter((a) => a.kind === "image").map((a) => ({ id: a.id, url: urlFor(a), name: a.filename })),
    [media, urlFor],
  );

  return <>
    <div className="inbox-tabs">
      <button type="button" className={tab === "media" ? "active" : ""} onClick={() => setTab("media")}>{t("chat.tabMedia")}</button>
      <button type="button" className={tab === "links" ? "active" : ""} onClick={() => setTab("links")}>{t("chat.tabLinks")}</button>
      <button type="button" className={tab === "docs" ? "active" : ""} onClick={() => setTab("docs")}>{t("chat.tabDocs")}</button>
    </div>
    <div className="media-panel-body">
      {tab === "media" && (media.length ? (
        <div className="media-panel-grid">
          {media.map((attachment) => attachment.kind === "image" ? (
            <button
              key={attachment.id}
              type="button"
              onClick={() => setPreviewIndex(Math.max(0, gallery.findIndex((item) => item.id === attachment.id)))}
            >
              <img src={urlFor(attachment)} alt={attachment.filename || "attachment"} loading="lazy" />
            </button>
          ) : attachment.kind === "audio" ? (
            <div key={attachment.id} className="media-panel-audio"><AudioBubble src={urlFor(attachment)} /></div>
          ) : (
            <video key={attachment.id} src={urlFor(attachment)} controls preload="metadata" />
          ))}
        </div>
      ) : <p className="media-panel-empty">{t("chat.emptyShared")}</p>)}
      {tab === "links" && (links.length ? (
        <ul className="media-panel-links">
          {links.map((url) => (
            <li key={url}>
              <a href={url} target="_blank" rel="noreferrer">
                <span className="media-panel-link-icon"><Link2 size={15} /></span>
                <span><strong>{linkHost(url)}</strong><small>{url}</small></span>
              </a>
            </li>
          ))}
        </ul>
      ) : <p className="media-panel-empty">{t("chat.emptyShared")}</p>)}
      {tab === "docs" && (docs.length ? (
        <ul className="media-panel-links">
          {docs.map((attachment) => (
            <li key={attachment.id}>
              <a href={urlFor(attachment)} target="_blank" rel="noreferrer">
                <span className="media-panel-link-icon"><FileText size={15} /></span>
                <span><strong>{attachment.filename || "file"}</strong><small>{attachment.mime}</small></span>
              </a>
            </li>
          ))}
        </ul>
      ) : <p className="media-panel-empty">{t("chat.emptyShared")}</p>)}
    </div>
    {previewIndex !== null && (
      <Lightbox items={gallery} index={previewIndex} onIndex={setPreviewIndex} onClose={() => setPreviewIndex(null)} />
    )}
  </>;
}
