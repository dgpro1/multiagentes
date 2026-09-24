"use client";

import { ChevronLeft, ChevronRight, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { useT } from "@/lib/i18n";

export type SectionTab<T extends string> = { id: T; label: string; icon?: LucideIcon; badge?: ReactNode;
  /** When set the tab is a real link (middle-click, Ctrl+click and "open in new tab" work), and the address is what says which tab is open. */
  href?: string;
};

// Below this much pointer travel a press is a click on a tab, not a drag.
const DRAG_THRESHOLD = 5;

// One tab state, two controls. The tab strip is what a desktop shows. On a
// phone four or more tabs do not fit the strip, so there it gives way to a
// pager: one section at a time, an arrow back and forward, and a position
// count. Each arrow is labelled with the section it leads to. The stylesheet
// decides which of the two is on screen (.section-tabs / .section-pager); the
// parent only owns the value, exactly as it did with a hand-written strip.
//
// A tab with an href is a <Link> instead of a button, and the pager arrows push
// the neighbouring tab's href; onChange is then optional, since the address
// already carries the value.
//
// A strip wider than its column slides on its own: edge arrows appear on the
// side that still has tabs, the wheel and a mouse drag move it, and the active
// tab is kept in view. A strip that fits shows none of that.
export function SectionTabs<T extends string>({ tabs, value, onChange, className }: { tabs: SectionTab<T>[]; value: T; onChange?: (id: T) => void; className?: string }) {
  const t = useT();
  const router = useRouter();
  const navRef = useRef<HTMLElement>(null);
  const drag = useRef({ active: false, moved: false, startX: 0, startLeft: 0 });
  const [edges, setEdges] = useState({ left: false, right: false });
  const index = Math.max(0, tabs.findIndex((tab) => tab.id === value));
  const current = tabs[index];
  const previous = index > 0 ? tabs[index - 1] : undefined;
  const next = index < tabs.length - 1 ? tabs[index + 1] : undefined;

  const measure = useCallback(() => {
    const nav = navRef.current;
    if (!nav) return;
    const left = nav.scrollLeft > 1;
    const right = nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 1;
    setEdges((state) => (state.left === left && state.right === right ? state : { left, right }));
  }, []);

  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(nav);
    // The wheel slides the strip sideways while it has room to move that way;
    // at either end it lets the page scroll as usual.
    const onWheel = (event: WheelEvent) => {
      if (nav.scrollWidth <= nav.clientWidth || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
      const atStart = nav.scrollLeft <= 0 && event.deltaY < 0;
      const atEnd = nav.scrollLeft + nav.clientWidth >= nav.scrollWidth - 1 && event.deltaY > 0;
      if (atStart || atEnd) return;
      event.preventDefault();
      nav.scrollLeft += event.deltaY;
    };
    nav.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      observer.disconnect();
      nav.removeEventListener("wheel", onWheel);
    };
  }, [measure, tabs.length]);

  useEffect(() => {
    const nav = navRef.current;
    const active = nav?.querySelector<HTMLElement>(".active");
    if (!nav || !active) return;
    nav.scrollTo({ left: Math.max(0, active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2) });
  }, [value]);

  const go = (tab?: SectionTab<T>) => {
    if (!tab) return;
    if (tab.href) router.push(tab.href);
    else onChange?.(tab.id);
  };

  const slide = (direction: 1 | -1) => {
    const nav = navRef.current;
    if (!nav) return;
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    nav.scrollBy({ left: direction * nav.clientWidth * 0.7, behavior: smooth ? "smooth" : "auto" });
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLElement>) => {
    const nav = navRef.current;
    if (!nav || event.pointerType !== "mouse" || event.button !== 0) return;
    drag.current = { active: true, moved: false, startX: event.clientX, startLeft: nav.scrollLeft };
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    const nav = navRef.current;
    const state = drag.current;
    if (!nav || !state.active) return;
    const delta = event.clientX - state.startX;
    if (!state.moved) {
      if (Math.abs(delta) < DRAG_THRESHOLD) return;
      // Capture only once it is a drag, so a plain press still clicks its tab.
      state.moved = true;
      nav.setPointerCapture(event.pointerId);
      nav.classList.add("is-dragging");
    }
    nav.scrollLeft = state.startLeft - delta;
  };
  const endDrag = () => {
    drag.current.active = false;
    navRef.current?.classList.remove("is-dragging");
  };
  // The release of a drag lands on a tab; it must not select it.
  const onClickCapture = (event: ReactMouseEvent<HTMLElement>) => {
    if (!drag.current.moved) return;
    drag.current.moved = false;
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <>
      <div className="section-tabs-wrap">
        {edges.left && <button type="button" className="section-tabs-arrow left" aria-label={t("shell.scrollTabsLeft")} onClick={() => slide(-1)}><ChevronLeft size={16} /></button>}
        <nav
          ref={navRef}
          className={`tabs section-tabs${className ? ` ${className}` : ""}`}
          onScroll={measure}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onClickCapture={onClickCapture}
        >
          {tabs.map((tab) => {
            const active = tab.id === value;
            const content = <>{tab.icon && <tab.icon size={17} />} {tab.label}{tab.badge !== undefined && <> <span>{tab.badge}</span></>}</>;
            return tab.href
              ? <Link key={tab.id} href={tab.href} className={active ? "active" : ""} aria-current={active ? "page" : undefined} draggable={false}>{content}</Link>
              : <button key={tab.id} type="button" className={active ? "active" : ""} onClick={() => go(tab)}>{content}</button>;
          })}
        </nav>
        {edges.right && <button type="button" className="section-tabs-arrow right" aria-label={t("shell.scrollTabsRight")} onClick={() => slide(1)}><ChevronRight size={16} /></button>}
      </div>
      <div className="section-pager">
        <button type="button" className="button ghost" disabled={!previous} aria-label={previous?.label} onClick={() => go(previous)}><ChevronLeft size={18} /></button>
        <div className="section-pager-current">
          {current.icon && <current.icon size={17} />}<strong>{current.label}</strong>{current.badge !== undefined && <span>{current.badge}</span>}<small>{index + 1} / {tabs.length}</small>
        </div>
        <button type="button" className="button ghost" disabled={!next} aria-label={next?.label} onClick={() => go(next)}><ChevronRight size={18} /></button>
      </div>
    </>
  );
}
