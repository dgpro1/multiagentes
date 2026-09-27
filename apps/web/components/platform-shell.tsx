"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { Building2, LayoutDashboard, LogOut, ScrollText } from "lucide-react";
import { api } from "@/lib/api";
import { useT, type I18nKey } from "@/lib/i18n";
import type { PlatformAdmin } from "@/types";

const navigation: { href: string; labelKey: I18nKey; icon: typeof LayoutDashboard }[] = [
  { href: "/superadmin", labelKey: "platform.nav.overview", icon: LayoutDashboard },
  { href: "/superadmin/agencies", labelKey: "platform.nav.agencies", icon: Building2 },
  { href: "/superadmin/audit", labelKey: "platform.nav.audit", icon: ScrollText },
];

/** The platform's own shell: its own session, never the agency's. */
export function PlatformShell({ children }: { children: ReactNode }) {
  const t = useT();
  const pathname = usePathname();
  const router = useRouter();
  const [admin, setAdmin] = useState<PlatformAdmin | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api<PlatformAdmin>("/platform/auth/me")
      .then((current) => { if (!cancelled) setAdmin(current); })
      .catch(() => { if (!cancelled) router.replace("/superadmin/login"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [router]);

  async function logout() {
    await api("/platform/auth/logout", { method: "POST" });
    setAdmin(null);
    router.push("/superadmin/login");
    router.refresh();
  }

  if (loading || !admin) {
    return <div className="app-loader"><span className="hunterai-icon"><img src="/brand/hunterai-icon.png" alt="" /></span><span>{t("platform.nav.loading")}</span></div>;
  }

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="brand-row">
          <Link href="/superadmin" className="brand"><span className="hunterai-icon"><img src="/brand/hunterai-icon.png" alt="" /></span><span>HunterAI</span></Link>
        </div>
        <div className="sidebar-workspace" title={t("platform.nav.section")}><Building2 size={14} /><span>{t("platform.nav.section")}</span></div>
        <nav>
          <span className="nav-label">{t("platform.nav.section")}</span>
          {navigation.map((item) => {
            const active = item.href === "/superadmin" ? pathname === "/superadmin" : pathname.startsWith(item.href);
            return <Link key={item.href} href={item.href} className={active ? "active" : ""}><item.icon size={18} /><span>{t(item.labelKey)}</span></Link>;
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-foot">
            <div className="user-avatar">{admin.name.slice(0, 1).toUpperCase()}</div>
            <div className="user-meta"><strong>{admin.name}</strong><span>{admin.email}</span></div>
            <button className="icon-button inverse" onClick={logout} title={t("platform.nav.logout")} aria-label={t("platform.nav.logout")}><LogOut size={17} /></button>
          </div>
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
