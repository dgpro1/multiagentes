import type { TimeRange, WeekDay, WeeklyHours } from "@/types";
import type { TranslateFn } from "@/lib/i18n";

export const DAYS: readonly WeekDay[] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
export const WEEKDAYS: readonly WeekDay[] = ["mon", "tue", "wed", "thu", "fri"];
export const MAX_RANGES = 4;
export const DEFAULT_RANGE: TimeRange = ["09:00", "17:00"];

export const emptyHours = (): WeeklyHours => ({ mon: [], tue: [], wed: [], thu: [], fri: [], sat: [], sun: [] });

export const cloneRanges = (ranges: TimeRange[]): TimeRange[] => ranges.map(([start, end]) => [start, end]);

export const cloneHours = (hours?: WeeklyHours | null): WeeklyHours => {
  const copy = emptyHours();
  if (!hours) return copy;
  for (const day of DAYS) copy[day] = cloneRanges(hours[day] ?? []);
  return copy;
};

/** "Mon–Fri 09:00–13:00, 14:00–18:00 · Sat 09:00–12:00": consecutive days with the same hours are grouped. */
export function summarizeHours(hours: WeeklyHours, t: TranslateFn): string {
  const groups: { from: WeekDay; to: WeekDay; ranges: string }[] = [];
  for (const day of DAYS) {
    const ranges = (hours[day] ?? []).map(([start, end]) => `${start}–${end}`).join(", ");
    if (!ranges) continue;
    const last = groups[groups.length - 1];
    if (last && last.ranges === ranges && DAYS.indexOf(last.to) === DAYS.indexOf(day) - 1) last.to = day;
    else groups.push({ from: day, to: day, ranges });
  }
  if (!groups.length) return t("professionals.noHours");
  const label = (from: WeekDay, to: WeekDay) => (from === to ? t(`professionals.daysShort.${from}`) : `${t(`professionals.daysShort.${from}`)}–${t(`professionals.daysShort.${to}`)}`);
  return groups.map((group) => `${label(group.from, group.to)} ${group.ranges}`).join(" · ");
}

/** The first rule a schedule breaks, worded for the person, or null when it is valid. Mirrors the server's checks. */
export function scheduleError(hours: WeeklyHours, t: TranslateFn): string | null {
  for (const day of DAYS) {
    const dayName = t(`professionals.daysLong.${day}`);
    const ranges = hours[day] ?? [];
    if (ranges.some(([start, end]) => !start || !end)) return t("professionals.errors.incomplete", { day: dayName });
    if (ranges.some(([start, end]) => start >= end)) return t("professionals.errors.endBeforeStart", { day: dayName });
    const sorted = [...ranges].sort((a, b) => a[0].localeCompare(b[0]));
    if (sorted.some((range, index) => index > 0 && range[0] < sorted[index - 1][1])) return t("professionals.errors.overlap", { day: dayName });
  }
  return null;
}
