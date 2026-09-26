"use client";

import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { BarChart3, Bot, Building2, CreditCard, Inbox, LayoutDashboard, LogOut, Menu, MessageSquareText, PanelLeftClose, PanelLeftOpen, Radio, Settings, Sparkles, Wallet, X } from "lucide-react";
import { api } from "@/lib/api";
import { useT, type I18nKey } from "@/lib/i18n";
import { NAV_COLLAPSED_CLASS, useCollapsibleNav } from "@/lib/sidebar";
import type { User } from "@/types";

const navigation: { href: string; labelKey: I18nKey; icon: typeof LayoutDashboard }[] = [
  { href: "/", labelKey: "nav.home", icon: LayoutDashboard },
  { href: "/clients", labelKey: "nav.clients", icon: Building2 },
  { href: "/agents", labelKey: "nav.agents", icon: Bot },
  { href: "/inbox", labelKey: "nav.inbox", icon: Inbox },
  { href: "/playground", labelKey: "nav.playground", icon: MessageSquareText },
  { href: "/channels", labelKey: "nav.channels", icon: Radio },
  { href: "/reports", labelKey: "nav.reports", icon: BarChart3 },
  { href: "/settings", labelKey: "nav.settings", icon: Settings },
];

// Extra path prefixes served without a session (comma-separated, baked at
// build). Lets a deployment add public pages without patching the shell.
const EXTRA_PUBLIC_PATHS = (process.env.NEXT_PUBLIC_PUBLIC_PATHS || "")
  .split(",")
  .map((path) => path.trim())
  .filter(Boolean);

const EXTRA_NAV_ICONS: Record<string, typeof LayoutDashboard> = {
  wallet: Wallet,
  "credit-card": CreditCard,
  billing: Wallet,
  sparkles: Sparkles,
};

// Extra sidebar links (comma-separated `label|href|icon`, baked at build). Lets a
// deployment add nav entries without patching the shell; icon falls back to Wallet.
const EXTRA_NAV = (process.env.NEXT_PUBLIC_EXTRA_NAV || "")
  .split(",")
  .map((entry) => entry.trim())
  .filter(Boolean)
  .map((entry) => {
    const [label, href, icon] = entry.split("|").map((part) => (part || "").trim());
    return { label, href, icon: EXTRA_NAV_ICONS[icon] || Wallet };
  })
  .filter((item) => item.label && item.href);

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const t = useT();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(pathname !== "/login");
  const [mobileOpen, setMobileOpen] = useState(false);
  // The rail is a desktop gesture only; below 901px the stylesheet hands the
  // navigation back to the phone drawer. See lib/sidebar.ts.
  const { collapsed, toggle } = useCollapsibleNav("agency");
  const isLogin = pathname === "/login";
  // On a client's own domain the browser path has no /portal prefix, but the matched route still has its slug.
  const routeParams = useParams<{ slug?: string }>();
  const isPortal = pathname.startsWith("/portal/") || routeParams.slug !== undefined;
  const isWidget = pathname.startsWith("/widget/");
  // The Google Calendar connection link: opened by a team member who has no
  // OpenLivery account at all, straight from the link the agency or the
  // client portal shared with them.
  const isConnect = pathname.startsWith("/connect/");
  const isExtraPublic = EXTRA_PUBLIC_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );
  const isBare = isLogin || isPortal || isWidget || isConnect || isExtraPublic;

  // Check the session on entry and revalidate it on every navigation, without
  // taking the shell off screen to do it: `loading` starts true and is only ever
  // cleared, so the full-screen loader covers the first render and nothing more.
  // Once a user is known, navigating keeps the sidebar and the page mounted while
  // the check runs in the background. Losing the session clears the user, which
  // puts the loader back up until the redirect lands.
  useEffect(() => {
    if (isBare) { setLoading(false); setUser(null); return; }
    let cancelled = false;
    api<User>("/auth/me")
      .then((current) => { if (!cancelled) setUser(current); })
      .catch(() => { if (!cancelled) { setUser(null); router.replace("/login"); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [isBare, pathname, router]);

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    // The shell lives in the root layout and survives client-side navigation, so
    // the signed-out identity has to be dropped explicitly.
    setUser(null);
    router.push("/login");
    router.refresh();
  }

  if (isBare) return <>{children}</>;
  if (loading || !user) return <div className="app-loader"><span className="hunterai-icon"><img src="/brand/hunterai-icon.png" alt="" /></span><span>{t("shell.loading")}</span></div>;

  const mobileNav = [
    { href: "/", labelKey: "nav.home" as const, icon: LayoutDashboard },
    { href: "/clients", labelKey: "nav.clients" as const, icon: Building2 },
    { href: "/agents", labelKey: "nav.agents" as const, icon: Bot },
    { href: "/inbox", labelKey: "nav.inbox" as const, icon: Inbox },
    { href: "/settings", labelKey: "nav.settings" as const, icon: Settings },
  ];

  return (
    <div className={`app-layout ${collapsed ? NAV_COLLAPSED_CLASS : ""}`}>
      {/* Stitch Mobile TopAppBar */}
      <header className="stitch-mobile-topbar">
        <Link href="/" className="stitch-mobile-topbar-brand">
          <span className="hunterai-icon">
            <img src="/brand/hunterai-icon.png" alt="HunterAI" />
          </span>
          <div className="stitch-mobile-topbar-meta">
            <span className="stitch-mobile-topbar-name">HunterAI</span>
            <span className="stitch-mobile-topbar-agency">
              {user.agency.name}
              <span className="stitch-online-dot" />
            </span>
          </div>
        </Link>
        <button
          type="button"
          className="stitch-mobile-menu-btn"
          onClick={() => setMobileOpen(true)}
          aria-label={t("shell.openMenu")}
        >
          <Menu size={20} />
        </button>
      </header>

      {mobileOpen && <div className="sidebar-overlay" onClick={() => setMobileOpen(false)} />}
      <aside id="app-sidebar" className={`sidebar ${mobileOpen ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <Link href="/" className="brand">
            <span className="hunterai-icon">
              <img src="/brand/hunterai-icon.png" alt="HunterAI" />
            </span>
            <span>HunterAI</span>
          </Link>
          <button className="sidebar-close" onClick={() => setMobileOpen(false)} aria-label={t("shell.closeMenu")}><X /></button>
          {/* Folds the column into the icon rail. Hidden below 901px, where the
              drawer and its own close button take over. */}
          <button type="button" className="icon-button inverse sidebar-toggle" onClick={toggle} aria-expanded={!collapsed} aria-controls="app-sidebar" title={t(collapsed ? "shell.expandSidebar" : "shell.collapseSidebar")} aria-label={t(collapsed ? "shell.expandSidebar" : "shell.collapseSidebar")}>{collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}</button>
        </div>
        <div className="sidebar-workspace" title={collapsed ? user.agency.name : undefined}>
          <div className="sidebar-workspace-avatar">{user.agency.name.slice(0, 2).toUpperCase()}</div>
          {!collapsed && (
            <div className="sidebar-workspace-meta">
              <p className="sidebar-workspace-name">{user.agency.name}</p>
              <p className="sidebar-workspace-plan">Plan Agencia Pro</p>
            </div>
          )}
        </div>
        <nav>
          <span className="nav-label">{t("nav.section")}</span>
          {navigation.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            const label = t(item.labelKey);
            // Folded, the label is hidden by the stylesheet, so the icon carries
            // it as both the accessible name and the tooltip.
            return (
              <Link
                key={item.href}
                href={item.href}
                className={active ? "active" : ""}
                title={collapsed ? label : undefined}
                aria-label={collapsed ? label : undefined}
                onClick={() => setMobileOpen(false)}
              >
                <item.icon size={18} />
                <span>{label}</span>
                {active && <span className="sidebar-active-dot" />}
              </Link>
            );
          })}
          {EXTRA_NAV.map((item) => {
            const Icon = item.icon;
            const active = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={active ? "active" : ""}
                title={collapsed ? item.label : undefined}
                aria-label={collapsed ? item.label : undefined}
                onClick={() => setMobileOpen(false)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          {!collapsed && (
            <div className="sidebar-assist-card">
              <div className="sidebar-assist-head">
                <Sparkles size={14} className="text-teal-600" />
                <span>Agente Asistente</span>
              </div>
              <p className="sidebar-assist-desc">
                Agentes en producción y operando al 100%.
              </p>
            </div>
          )}
          <div className="sidebar-foot">
            <div className="user-avatar">{user.name.slice(0, 1).toUpperCase()}</div>
            <div className="user-meta"><strong>{user.name}</strong><span>{user.email}</span></div>
            <button className="icon-button inverse" onClick={logout} title={t("shell.logout")} aria-label={t("shell.logout")}><LogOut size={17} /></button>
          </div>
        </div>
      </aside>
      <main className="main-content">{children}</main>

      {/* Stitch Mobile BottomNavBar */}
      <nav className="stitch-mobile-bottombar" aria-label="Mobile Navigation">
        {mobileNav.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          const label = t(item.labelKey);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`stitch-bottombar-tab ${active ? "active" : ""}`}
              aria-current={active ? "page" : undefined}
            >
              <item.icon size={18} />
              <span>{label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
