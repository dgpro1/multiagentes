"use client";

import { Moon, Sun } from "lucide-react";
import { useT } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";

// A one-tap switch between light and dark. It writes the same stored choice as
// the Appearance setting in Preferences, which stays the place for "System".
export function ThemeQuickToggle({ className = "" }: { className?: string }) {
  const t = useT();
  const { resolved, setTheme } = useTheme();
  const dark = resolved === "dark";
  const label = t(dark ? "shell.switchToLight" : "shell.switchToDark");
  return (
    <button type="button" className={`icon-button inverse theme-quick-toggle ${className}`.trim()} onClick={() => setTheme(dark ? "light" : "dark")} title={label} aria-label={label}>
      {dark ? <Sun size={17} /> : <Moon size={17} />}
    </button>
  );
}
