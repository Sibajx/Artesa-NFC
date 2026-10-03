// Thin promise layer over Web NFC (Chrome for Android only). The certificate
// URL passes through here in memory and is never logged or rendered.

interface NdefRecord {
  recordType: string;
  data?: DataView;
}
interface NdefReadingEvent extends Event {
  serialNumber: string;
  message: { records: NdefRecord[] };
}
interface NdefReaderLike {
  scan(options?: { signal?: AbortSignal }): Promise<void>;
  write(message: { records: { recordType: string; data: string }[] }, options?: { signal?: AbortSignal; overwrite?: boolean }): Promise<void>;
  makeReadOnly(options?: { signal?: AbortSignal }): Promise<void>;
  onreading: ((event: NdefReadingEvent) => void) | null;
  onreadingerror: ((event: Event) => void) | null;
}

type Ctor = new () => NdefReaderLike;

export function nfcSupported(): boolean {
  return typeof window !== 'undefined' && 'NDEFReader' in window;
}

function reader(): NdefReaderLike {
  return new (window as unknown as { NDEFReader: Ctor }).NDEFReader();
}

export type NfcErrorCode = 'unsupported' | 'permission' | 'timeout' | 'read' | 'write' | 'lock';

export class NfcError extends Error {
  readonly code: NfcErrorCode;

  constructor(code: NfcErrorCode, message: string) {
    super(message);
    this.code = code;
  }
}

const TIMEOUT_MS = 30_000;

function withTimeout<T>(run: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  return run(controller.signal).finally(() => {
    window.clearTimeout(timer);
    controller.abort();
  });
}

function mapError(error: unknown, fallback: NfcError['code']): NfcError {
  if (error instanceof NfcError) return error;
  const name = error instanceof DOMException ? error.name : '';
  if (name === 'NotAllowedError') return new NfcError('permission', 'Permite el acceso a NFC en el navegador.');
  if (name === 'AbortError') return new NfcError('timeout', 'No se detectó el chip a tiempo. Vuelve a intentarlo.');
  if (name === 'NotSupportedError') return new NfcError('unsupported', 'Este dispositivo no tiene NFC o está desactivado.');
  const text = { read: 'No se pudo leer el chip.', write: 'No se pudo grabar el chip.', lock: 'No se pudo bloquear el chip.' } as Record<string, string>;
  return new NfcError(fallback, text[fallback] ?? 'Error de NFC.');
}

export interface TagRead {
  uid: string;
  url: string | null;
}

/** Waits for one tag and returns its serial number and first URL record. */
export function readTag(): Promise<TagRead> {
  if (!nfcSupported()) return Promise.reject(new NfcError('unsupported', 'Abre Gestión en Chrome para Android.'));
  return withTimeout(
    (signal) =>
      new Promise<TagRead>((resolve, reject) => {
        const r = reader();
        r.onreading = (event) => {
          const record = event.message.records.find((rec) => rec.recordType === 'url');
          const url = record?.data ? new TextDecoder().decode(record.data) : null;
          resolve({ uid: event.serialNumber, url });
        };
        r.onreadingerror = () => reject(new NfcError('read', 'No se pudo leer el chip. Sepáralo y acércalo de nuevo.'));
        signal.addEventListener('abort', () => reject(new NfcError('timeout', 'No se detectó el chip a tiempo. Vuelve a intentarlo.')));
        r.scan({ signal }).catch((e: unknown) => reject(mapError(e, 'read')));
      }),
  );
}

/** Writes a single NDEF URI record (overwrites what the tag had). */
export function writeUrl(url: string): Promise<void> {
  if (!nfcSupported()) return Promise.reject(new NfcError('unsupported', 'Abre Gestión en Chrome para Android.'));
  return withTimeout((signal) =>
    reader().write({ records: [{ recordType: 'url', data: url }] }, { signal, overwrite: true }).catch((e: unknown) => {
      throw mapError(e, 'write');
    }),
  );
}

/** Makes the tag permanently read-only. Irreversible. */
export function lockTag(): Promise<void> {
  if (!nfcSupported()) return Promise.reject(new NfcError('unsupported', 'Abre Gestión en Chrome para Android.'));
  return withTimeout((signal) =>
    reader().makeReadOnly({ signal }).catch((e: unknown) => {
      throw mapError(e, 'lock');
    }),
  );
}
