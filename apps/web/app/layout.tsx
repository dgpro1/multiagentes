import type { Metadata } from "next";
import { Inter, Manrope, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/app-shell";
import { ToastProvider } from "@/components/toast";
import { LanguageProvider } from "@/lib/i18n";
import { ThemeProvider } from "@/lib/theme";
import { THEME_INIT_SCRIPT } from "@/lib/theme-script";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const manrope = Manrope({ subsets: ["latin"], variable: "--font-manrope", display: "swap" });
const plusJakarta = Plus_Jakarta_Sans({ subsets: ["latin"], variable: "--font-plus-jakarta", display: "swap" });

export const metadata: Metadata = {
  title: "HunterAI — AI agents for your agency",
  description: "Platform to build and manage AI agents for your agency.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // The theme bootstrap is a plain inline script, first thing in <body>, so it
  // runs during HTML parsing before any content paints (next/script's
  // beforeInteractive lands after the first markup in the app router, which is
  // late enough to flash). suppressHydrationWarning: the data-theme attribute
  // it stamps on <html> is meant to differ from the server markup.
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${plusJakarta.variable} ${inter.variable} ${manrope.variable}`}>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        <ThemeProvider>
          <LanguageProvider>
            <ToastProvider>
              <AppShell>{children}</AppShell>
            </ToastProvider>
          </LanguageProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
