"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { SharedContentList } from "@/components/shared-content";
import { useT } from "@/lib/i18n";
import type { Attachment, Message } from "@/types";

/** WhatsApp-style "shared content" drawer for a conversation: everything sent
 * in the chat grouped into Media (images/videos), Links, and Docs tabs. */
export function MediaPanel({ open, onClose, messages, urlFor }: {
  open: boolean;
  onClose: () => void;
  messages: Message[];
  urlFor: (attachment: Attachment) => string;
}) {
  const t = useT();
  const [previewing, setPreviewing] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape" && !previewing) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, previewing]);

  if (!open) return null;
  return createPortal(
    <div className="media-panel-backdrop" onClick={onClose}>
      <aside className="media-panel" onClick={(event) => event.stopPropagation()}>
        <header>
          <strong>{t("chat.sharedContent")}</strong>
          <button type="button" onClick={onClose} aria-label={t("chat.closePreview")}><X size={18} /></button>
        </header>
        <SharedContentList messages={messages} urlFor={urlFor} onPreviewChange={setPreviewing} />
      </aside>
    </div>,
    document.body,
  );
}
