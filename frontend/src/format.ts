/** Today's date in the user's own time zone, as `YYYY-MM-DD`. */
export function todayIso(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * A date without a time (a birthday), shown as that calendar date everywhere. It is built and
 * formatted in UTC so the user's time zone cannot move it by a day.
 */
export function formatDate(value: string, locale?: string): string {
  const match = DATE_ONLY.exec(value);
  if (!match) return value;
  const [, year, month, day] = match;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  // `Date.UTC` rolls impossible dates over (month 13, day 45), which would show a different day.
  const isRealDate =
    date.getUTCFullYear() === Number(year) && date.getUTCMonth() === Number(month) - 1 && date.getUTCDate() === Number(day);
  if (!isRealDate) return value;
  return new Intl.DateTimeFormat(locale, { dateStyle: "long", timeZone: "UTC" }).format(date);
}

/** A moment in time, shown in the user's own time zone. */
export function formatTimestamp(value: string, locale?: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date);
}
