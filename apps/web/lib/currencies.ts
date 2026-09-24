export const CURRENCIES = ["USD", "EUR", "MXN", "COP", "CLP", "ARS", "PEN", "BRL", "UYU", "BOB", "PYG", "DOP", "CRC", "GTQ", "PAB"] as const;
export type Currency = (typeof CURRENCIES)[number];

/** An amount in the client's currency, written the way the app's language writes it. */
export function formatMoney(value: number, currency: string | null | undefined, locale?: string): string {
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency: currency || "USD", maximumFractionDigits: 2 }).format(value);
  } catch {
    return value.toLocaleString(locale, { maximumFractionDigits: 2 });
  }
}
