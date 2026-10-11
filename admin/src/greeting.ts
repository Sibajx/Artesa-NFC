// The welcome line of Inicio: a greeting from a catalog, by the time of day in
// Mexico, with one of the nicknames the person uses. Only decoration: nothing
// here decides access.

// How each teammate likes to be called (the owner approves this list). An
// account that is not here, or a shared one, is greeted as "equipo".
const NAMES: Record<string, string[]> = {
  'armzsibaja@gmail.com': ['Alexis', 'Alex'],
  'diego.sanchez.030604@gmail.com': ['Diego'],
  'mariourieljuarezrosales@gmail.com': ['Uri', 'Uriel', 'Mario'],
  'nicolashariel2103@gmail.com': ['Hariel'],
  'solcruzmartinez19@gmail.com': ['Sol', 'Soledad'],
};
const FALLBACK_NAME = 'equipo';

type Slot = 'madrugada' | 'manana' | 'tarde' | 'noche';

// `{n}` is replaced by the name.
const PHRASES: Record<Slot, string[]> = {
  madrugada: ['A trasnochar, {n}', 'Madrugando con ganas, {n}', 'Qué desvelo, {n}', 'La noche es joven, {n}'],
  manana: ['Buenos días, {n}', 'Buen día, {n}', 'A darle, {n}', 'Arrancamos el día, {n}'],
  tarde: ['Buenas tardes, {n}', 'Buenas, {n}', 'Qué gusto verte, {n}', 'Seguimos, {n}'],
  noche: ['Buenas noches, {n}', 'Buenas, {n}', 'Última vuelta del día, {n}', 'Hola, {n}'],
};

// Day-specific lines (month-day in Mexico). They win over the time of day.
const DATES: Record<string, string[]> = {
  '01-01': ['Feliz año nuevo, {n}'],
  '09-15': ['Viva México, {n}'],
  '09-16': ['Viva México, {n}'],
  '10-31': ['Feliz Día de Muertos, {n}'],
  '11-01': ['Feliz Día de Muertos, {n}'],
  '11-02': ['Feliz Día de Muertos, {n}'],
  '12-24': ['Feliz Nochebuena, {n}'],
  '12-25': ['Feliz Navidad, {n}'],
};

const WEEKDAYS: Record<string, string[]> = {
  Mon: ['Nueva semana, {n}'],
  Fri: ['Ya es viernes, {n}'],
};

const LAST_KEY = 'artesa.greeting.last';
const SESSION_KEY = 'artesa.greeting.session';

function mexicoParts(now: Date): { hour: number; monthDay: string; weekday: string } {
  const get = (options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat('en-US', { timeZone: 'America/Mexico_City', ...options }).format(now);
  const [month, day] = get({ month: '2-digit', day: '2-digit' }).split('/');
  return {
    hour: Number(get({ hour: '2-digit', hourCycle: 'h23' })),
    monthDay: `${month}-${day}`,
    weekday: get({ weekday: 'short' }),
  };
}

export function slotOf(hour: number): Slot {
  if (hour < 5) return 'madrugada';
  if (hour < 12) return 'manana';
  if (hour < 19) return 'tarde';
  return 'noche';
}

export const namesOf = (email: string): string[] => NAMES[email.trim().toLowerCase()] ?? [FALLBACK_NAME];

function read(storage: 'session' | 'local', key: string): string | null {
  try {
    return (storage === 'session' ? sessionStorage : localStorage).getItem(key);
  } catch {
    return null;
  }
}

function write(storage: 'session' | 'local', key: string, value: string) {
  try {
    (storage === 'session' ? sessionStorage : localStorage).setItem(key, value);
  } catch {
    /* private window or blocked storage: the greeting just is not remembered */
  }
}

/** Every greeting this person could get right now (before choosing the name). */
export function candidates(now: Date): string[] {
  const { hour, monthDay, weekday } = mexicoParts(now);
  return [...(DATES[monthDay] ?? []), ...(WEEKDAYS[weekday] ?? []), ...PHRASES[slotOf(hour)]];
}

/**
 * One greeting per visit: it stays the same while the tab is open and avoids
 * repeating the last one this browser showed. `random` is injectable for tests.
 */
export function greetingFor(email: string, now: Date = new Date(), random: () => number = Math.random): string {
  const kept = read('session', SESSION_KEY);
  if (kept) return kept;
  const names = namesOf(email);
  const last = read('local', LAST_KEY);
  const pool = candidates(now).map((phrase) => phrase.replace('{n}', names[Math.floor(random() * names.length)]));
  const fresh = pool.filter((text) => text !== last);
  const options = fresh.length > 0 ? fresh : pool;
  const chosen = options[Math.floor(random() * options.length)];
  write('session', SESSION_KEY, chosen);
  write('local', LAST_KEY, chosen);
  return chosen;
}
