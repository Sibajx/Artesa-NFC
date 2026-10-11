import { mexicoParts } from './greeting';

// The seasons of the year with their own touch in Inicio. Same dates as the
// seasons of the public hero (Hero), so the console and the site agree.
export type Season = 'muertos' | 'navidad';

const SEASONS: { season: Season; from: string; to: string }[] = [
  { season: 'muertos', from: '10-20', to: '11-02' },
  { season: 'navidad', from: '12-16', to: '12-25' },
];

/** The season of `now` (Mexico date, month-day compared as text), or null. */
export function seasonOf(now: Date = new Date()): Season | null {
  const { monthDay } = mexicoParts(now);
  return SEASONS.find((s) => monthDay >= s.from && monthDay <= s.to)?.season ?? null;
}
