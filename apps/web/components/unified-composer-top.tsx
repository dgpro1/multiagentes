"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Clock, CalendarCheck, FileText, Lock, MessageSquareText, Zap } from "lucide-react";
import { ChannelIcon, channelLabel, threadName } from "@/lib/channels";
import { useT } from "@/lib/i18n";
import type { LinkedThread } from "@/types";

export type ComposerMode = "chat" | "note";

interface UnifiedComposerTopProps {
  mode: ComposerMode;
  onModeChange: (mode: ComposerMode) => void;
  threads?: LinkedThread[];
  channel?: string;
  via?: string;
  onViaChange?: (conversationId: string) => void;
  onOpenVariables: () => void;
}

export function UnifiedComposerTop({
  mode,
  onModeChange,
  threads = [],
  channel = "whatsapp",
  via,
  onViaChange,
  onOpenVariables,
}: UnifiedComposerTopProps) {
  const t = useT();
  const [menuOpen, setMenuOpen] = useState<"action" | "channel" | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const currentThread = threads.find((th) => th.conversation_id === via) ?? threads[0];
  const activeChannel = currentThread?.channel ?? channel;

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setMenuOpen(null);
      }
    }
    if (menuOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [menuOpen]);

  return (
    <div ref={containerRef} className="composer-top">
      {/* Mode picker: Chat ⌵ / Nota interna ⌵ */}
      <div className="composer-menu">
        <button
          type="button"
          className={`composer-pill action${mode === "note" ? " note" : ""}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen === "action"}
          onClick={() => setMenuOpen(menuOpen === "action" ? null : "action")}
        >
          {mode === "note" ? (
            <>
              <FileText size={14} className="text-amber-600" />
              <span>{t("inbox.composerInternalNote") || "Nota interna"}</span>
            </>
          ) : (
            <>
              <MessageSquareText size={14} />
              <span>{t("inbox.composerChat") || "Chat"}</span>
            </>
          )}
          <ChevronDown size={14} />
        </button>

        {menuOpen === "action" && (
          <div className="composer-menu-list" role="menu">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                onModeChange("chat");
                setMenuOpen(null);
              }}
            >
              <MessageSquareText size={15} />
              <span>{t("inbox.composerChat") || "Chat"}</span>
              {mode === "chat" && <Check size={15} />}
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                onModeChange("note");
                setMenuOpen(null);
              }}
            >
              <FileText size={15} color="#d97706" />
              <span>{t("inbox.composerInternalNote") || "Nota interna"}</span>
              {mode === "note" && <Check size={15} />}
            </button>
            <button
              type="button"
              role="menuitem"
              disabled
              title="Disponible en Sub-Fase 4"
              style={{ opacity: 0.5, cursor: "not-allowed" }}
            >
              <Clock size={15} />
              <span>{t("inbox.composerScheduleMessage") || "Programar mensaje"}</span>
              <small style={{ fontSize: 10, color: "var(--muted)" }}>Fase 4</small>
            </button>
            <button
              type="button"
              role="menuitem"
              disabled
              title="Disponible en Sub-Fase 5"
              style={{ opacity: 0.5, cursor: "not-allowed" }}
            >
              <CalendarCheck size={15} />
              <span>{t("inbox.composerBookAppointment") || "Agendar cita"}</span>
              <small style={{ fontSize: 10, color: "var(--muted)" }}>Fase 5</small>
            </button>
          </div>
        )}
      </div>

      {/* When in chat mode: via channel selector */}
      {mode === "chat" && (
        <>
          <span className="composer-via">{t("portal.inbox.composer.via") || "vía"}</span>
          <div className="composer-menu">
            <button
              type="button"
              className="composer-pill channel"
              title={t("portal.inbox.composer.sendThrough") || "Enviar por"}
              aria-haspopup="menu"
              aria-expanded={menuOpen === "channel"}
              onClick={() => setMenuOpen(menuOpen === "channel" ? null : "channel")}
            >
              <span className={`channel-dot ${activeChannel}`}>
                <ChannelIcon channel={activeChannel} />
              </span>
              <span>
                {currentThread ? threadName(currentThread, t) : channelLabel(activeChannel, t)}
              </span>
              {threads.length > 1 && <ChevronDown size={14} />}
            </button>

            {menuOpen === "channel" && (
              <div className="composer-menu-list" role="menu">
                {threads.length > 1 ? (
                  threads.map((thread) => (
                    <button
                      key={thread.conversation_id}
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        onViaChange?.(thread.conversation_id);
                        setMenuOpen(null);
                      }}
                    >
                      <span className={`channel-dot ${thread.channel}`}>
                        <ChannelIcon channel={thread.channel} />
                      </span>
                      <span>{threadName(thread, t)}</span>
                      {thread.conversation_id === via && <Check size={15} />}
                    </button>
                  ))
                ) : (
                  <button type="button" role="menuitem" onClick={() => setMenuOpen(null)}>
                    <span className={`channel-dot ${activeChannel}`}>
                      <ChannelIcon channel={activeChannel} />
                    </span>
                    <span>{channelLabel(activeChannel, t)}</span>
                    <Check size={15} />
                  </button>
                )}
              </div>
            )}
          </div>
        </>
      )}

      {/* When in note mode: internal team badge */}
      {mode === "note" && (
        <span className="composer-note-badge">
          <Lock size={12} />
          {t("inbox.internalNoteBadge") || "Nota interna (solo equipo)"}
        </span>
      )}

      {/* Quick variables button (Rayito ⚡) */}
      <button
        type="button"
        className="composer-icon bolt"
        title={t("portal.inbox.composer.quick") || "Variables del lead ([)"}
        aria-label={t("portal.inbox.composer.quick") || "Variables del lead"}
        onClick={onOpenVariables}
      >
        <Zap size={16} />
      </button>
    </div>
  );
}
