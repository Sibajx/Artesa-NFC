import { useEffect, useRef, useState } from 'react';
import type { DragEvent } from 'react';
import { ApiError, adminApi, mediaUrl } from '../api';
import type { HeroCampaign, HeroState } from '../api';
import { writeErrorMessage } from '../format';
import { useConfirm, useToast } from '../feedback-context';
import { useLoad } from '../hooks';
import { Badge, ErrorState, Loading, PageHeader } from '../ui';

// P-028: the home hero by season. One card per season: drop a video on it,
// the server crops, trims and compresses it, then Publicar puts it on the
// site on its dates. "Forzar" shows a season for everyone right now.

const MONTHS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
const MAX_VIDEO_BYTES = 200 * 1024 * 1024;
const MAX_SECONDS = 20;
const MAX_START_SECONDS = 3600;
const POLL_MS = 3000;

const STATUS: Record<HeroCampaign['status'], { label: string; tone: 'jade' | 'neutral' | 'lavanda' }> = {
  no_video: { label: 'Sin video', tone: 'neutral' },
  processing: { label: 'Procesando…', tone: 'lavanda' },
  error: { label: 'Error', tone: 'neutral' },
  draft: { label: 'Borrador', tone: 'neutral' },
  live: { label: 'En vivo', tone: 'jade' },
  scheduled: { label: 'Programado', tone: 'lavanda' },
};

const REASON: Record<NonNullable<HeroState['live_reason']>, string> = {
  forced: 'forzado',
  date: 'por fecha',
  default: 'es el hero normal',
};

function rangeLabel(c: HeroCampaign): string {
  if (c.start_month === null || c.start_day === null || c.end_month === null || c.end_day === null) {
    return 'Todo el año, cuando no hay otra temporada';
  }
  return `${c.start_day} ${MONTHS[c.start_month - 1]} – ${c.end_day} ${MONTHS[c.end_month - 1]} (cada año)`;
}

function message(e: unknown): string {
  return writeErrorMessage(e instanceof ApiError ? e : new ApiError('network', 0));
}

const dayValue = (month: number | null, day: number | null) =>
  month && day ? `2024-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}` : '';

function splitDay(value: string): [number, number] | null {
  const m = /^\d{4}-(\d{2})-(\d{2})$/.exec(value);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

function DropZone({ campaign, busy, onFile }: { campaign: HeroCampaign; busy: boolean; onFile: (file: File, start: number) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [startText, setStartText] = useState('');
  const processing = campaign.status === 'processing';
  const disabled = busy || processing;

  // Empty or invalid means "from the beginning".
  const start = (() => {
    const n = Number(startText.replace(',', '.'));
    return startText.trim() !== '' && Number.isFinite(n) ? Math.min(Math.max(n, 0), MAX_START_SECONDS) : 0;
  })();

  function drop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setOver(false);
    const file = e.dataTransfer.files[0];
    if (file && !disabled) onFile(file, start);
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
      className={`rounded-xl border-2 border-dashed p-4 text-center text-sm transition-colors ${
        over ? 'border-botanica-jade bg-botanica-jade/5' : 'border-botanica-gris/30'
      } ${disabled ? 'opacity-60' : ''}`}
    >
      <p className="text-botanica-grafito">
        {processing ? 'Procesando el video…' : campaign.video_mp4 ? 'Arrastra otro video aquí para reemplazarlo' : 'Arrastra el video aquí'}
      </p>
      <button type="button" className="btn-secondary mt-2" disabled={disabled} onClick={() => input.current?.click()}>
        {busy ? 'Subiendo…' : 'Elegir video'}
      </button>
      <input
        ref={input}
        type="file"
        accept="video/mp4,video/webm,video/quicktime"
        className="sr-only"
        aria-label={`Video de ${campaign.name}`}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) onFile(file, start);
        }}
      />
      <label className="mt-3 flex items-center justify-center gap-2 text-xs text-botanica-grafito">
        Empezar en el segundo
        <input
          type="number"
          inputMode="decimal"
          min={0}
          max={MAX_START_SECONDS}
          step={0.5}
          placeholder="0"
          value={startText}
          disabled={disabled}
          onChange={(e) => setStartText(e.target.value)}
          className="w-20 rounded-lg border border-botanica-gris/30 px-2 py-1 text-center text-sm"
        />
      </label>
      <p className="mt-2 text-xs text-botanica-gris">
        MP4, MOV o WebM, hasta 200 MB. El sistema lo recorta a 16:9, deja {MAX_SECONDS} s desde ese segundo, quita el audio y lo comprime.
      </p>
    </div>
  );
}

function CampaignCard({
  campaign, state, busy, run,
}: {
  campaign: HeroCampaign;
  state: HeroState;
  busy: string | null;
  run: (id: string, call: () => Promise<HeroState>, done?: string) => Promise<boolean>;
}) {
  const confirm = useConfirm();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(campaign.name);
  const [from, setFrom] = useState(dayValue(campaign.start_month, campaign.start_day));
  const [to, setTo] = useState(dayValue(campaign.end_month, campaign.end_day));
  const [until, setUntil] = useState('');
  const mine = busy === campaign.id;
  const status = STATUS[campaign.status];
  const ready = !!campaign.video_mp4 && campaign.status !== 'processing';

  async function upload(file: File, start: number) {
    if (!file.type.startsWith('video/')) {
      toast('Ese archivo no es un video.', 'error');
      return;
    }
    if (file.size > MAX_VIDEO_BYTES) {
      toast('El video pesa más de 200 MB.', 'error');
      return;
    }
    await run(campaign.id, () => adminApi.uploadHeroVideo(campaign.id, file, file.type, start), 'Video recibido; se está procesando');
  }

  async function saveEdit() {
    const a = splitDay(from);
    const b = splitDay(to);
    const body: Parameters<typeof adminApi.updateHeroCampaign>[1] = { name: name.trim() };
    if (!campaign.is_default) {
      if (!a || !b) {
        toast('Elige la fecha de inicio y de fin.', 'error');
        return;
      }
      Object.assign(body, { start_month: a[0], start_day: a[1], end_month: b[0], end_day: b[1] });
    }
    if (await run(campaign.id, () => adminApi.updateHeroCampaign(campaign.id, body), 'Temporada actualizada')) setEditing(false);
  }

  async function remove() {
    const answer = await confirm({
      title: `¿Borrar la temporada «${campaign.name}»?`,
      body: 'Se borra también su video. El hero vuelve a su comportamiento normal en esas fechas.',
      confirmLabel: 'Borrar',
      tone: 'danger',
    });
    if (answer !== null) await run(campaign.id, () => adminApi.deleteHeroCampaign(campaign.id), 'Temporada borrada');
  }

  async function force() {
    const answer = await confirm({
      title: `¿Mostrar «${campaign.name}» ahora para todos?`,
      body: until
        ? `Se verá en el sitio hasta el ${until}, aunque no sea su fecha.`
        : 'Se verá en el sitio hasta que quites el forzado, aunque no sea su fecha.',
      confirmLabel: 'Forzar',
    });
    if (answer !== null) await run(campaign.id, () => adminApi.forceHeroCampaign(campaign.id, until || null), 'Forzado en el sitio');
  }

  return (
    <article className="card-elevated p-5 flex flex-col gap-4" aria-label={campaign.name}>
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {editing ? (
            <input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} aria-label="Nombre de la temporada"
              className="w-full px-3 py-1.5 border border-botanica-gris/30 rounded-md text-base bg-white" />
          ) : (
            <h2 className="text-xl font-serif text-botanica-negro">{campaign.name}</h2>
          )}
          {editing && !campaign.is_default ? (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-botanica-grafito">
              <label>Desde <input type="date" value={from} onChange={(e) => setFrom(e.target.value)}
                className="ml-1 px-2 py-1 border border-botanica-gris/30 rounded-md bg-white" /></label>
              <label>Hasta <input type="date" value={to} onChange={(e) => setTo(e.target.value)}
                className="ml-1 px-2 py-1 border border-botanica-gris/30 rounded-md bg-white" /></label>
              <span className="text-botanica-gris">(el año no importa; se repite cada año)</span>
            </div>
          ) : (
            <p className="text-sm text-botanica-gris">{rangeLabel(campaign)}</p>
          )}
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <Badge tone={status.tone}>{status.label}</Badge>
          {campaign.forced && <Badge tone="lavanda">Forzado{campaign.forced_until ? ` hasta ${campaign.forced_until}` : ''}</Badge>}
        </div>
      </header>

      {campaign.video_mp4 && campaign.poster ? (
        <video
          key={campaign.video_mp4}
          className="w-full aspect-video rounded-xl bg-botanica-negro object-cover"
          controls muted loop playsInline preload="none"
          poster={mediaUrl(`/media/${campaign.poster}`)}
          aria-label={`Vista previa de ${campaign.name}`}
        >
          <source src={mediaUrl(`/media/${campaign.video_mp4}`)} type="video/mp4" />
          {campaign.video_webm && <source src={mediaUrl(`/media/${campaign.video_webm}`)} type="video/webm" />}
        </video>
      ) : (
        <div className="w-full aspect-video rounded-xl bg-botanica-hueso border border-botanica-gris/15 flex items-center justify-center text-sm text-botanica-gris">
          Todavía no hay video
        </div>
      )}

      {campaign.error && (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{campaign.error}</p>
      )}

      {state.ffmpeg_available && state.media_enabled && (
        <DropZone campaign={campaign} busy={mine} onFile={(f, start) => void upload(f, start)} />
      )}

      <div className="flex flex-wrap items-center gap-2">
        {editing ? (
          <>
            <button type="button" className="btn-primary" disabled={mine} onClick={() => void saveEdit()}>Guardar</button>
            <button type="button" className="btn-secondary" onClick={() => setEditing(false)}>Cancelar</button>
          </>
        ) : (
          <>
            {campaign.published ? (
              <button type="button" className="btn-secondary" disabled={mine}
                onClick={() => void run(campaign.id, () => adminApi.publishHeroCampaign(campaign.id, false), 'Despublicada')}>Despublicar</button>
            ) : (
              <button type="button" className="btn-primary" disabled={mine || !ready}
                title={ready ? undefined : 'Primero sube un video'}
                onClick={() => void run(campaign.id, () => adminApi.publishHeroCampaign(campaign.id, true), 'Publicada')}>Publicar</button>
            )}
            {campaign.forced ? (
              <button type="button" className="btn-secondary" disabled={mine}
                onClick={() => void run(campaign.id, () => adminApi.unforceHero(), 'Volvió a mostrarse por fecha')}>Quitar forzado</button>
            ) : (
              <span className="inline-flex items-center gap-2">
                <button type="button" className="btn-secondary" disabled={mine || !ready}
                  onClick={() => void force()}>Forzar ahora</button>
                <label className="text-xs text-botanica-gris">hasta
                  <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} aria-label="Forzar hasta (opcional)"
                    className="ml-1 px-2 py-1 border border-botanica-gris/30 rounded-md bg-white" />
                </label>
              </span>
            )}
            <button type="button" className="btn-secondary ml-auto" disabled={mine} onClick={() => setEditing(true)}>Editar</button>
            {!campaign.is_default && (
              <button type="button" className="btn-secondary text-red-700 border-red-200" disabled={mine || campaign.forced}
                onClick={() => void remove()}>Borrar</button>
            )}
          </>
        )}
      </div>
      {campaign.updated_by && <p className="text-xs text-botanica-gris">Último cambio: {campaign.updated_by}</p>}
    </article>
  );
}

function NewCampaign({ busy, onCreate }: { busy: boolean; onCreate: (name: string, from: [number, number], to: [number, number]) => Promise<boolean> }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const a = splitDay(from);
  const b = splitDay(to);

  if (!open) return <button type="button" className="btn-secondary self-start" onClick={() => setOpen(true)}>+ Nueva temporada</button>;
  return (
    <form
      className="card-elevated p-5 grid gap-3 md:grid-cols-[2fr_1fr_1fr_auto] items-end"
      onSubmit={(e) => {
        e.preventDefault();
        if (!a || !b) return;
        void onCreate(name, a, b).then((ok) => { if (ok) { setOpen(false); setName(''); setFrom(''); setTo(''); } });
      }}
    >
      <label className="text-xs font-medium text-botanica-grafito">Nombre
        <input required maxLength={60} value={name} onChange={(e) => setName(e.target.value)} placeholder="Ej. Guelaguetza"
          className="mt-1 w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white" />
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Desde
        <input required type="date" value={from} onChange={(e) => setFrom(e.target.value)}
          className="mt-1 w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white" />
      </label>
      <label className="text-xs font-medium text-botanica-grafito">Hasta
        <input required type="date" value={to} onChange={(e) => setTo(e.target.value)}
          className="mt-1 w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm bg-white" />
      </label>
      <div className="flex gap-2">
        <button type="submit" className="btn-primary" disabled={busy || !a || !b}>Crear</button>
        <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>Cancelar</button>
      </div>
      <p className="md:col-span-4 text-xs text-botanica-gris">El año no importa: la temporada se repite cada año en esas fechas.</p>
    </form>
  );
}

export default function Hero() {
  const loaded = useLoad('hero', (signal) => adminApi.hero(signal));
  // The latest answer (from a change or a poll) wins over the first load.
  const [changed, setState] = useState<HeroState | null>(null);
  const state = changed ?? (loaded.status === 'ready' ? loaded.data : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  // While a video is being processed, look again every few seconds.
  const processing = state?.campaigns.some((c) => c.status === 'processing') ?? false;
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => {
      adminApi.hero().then(setState, () => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [processing]);

  async function run(id: string, call: () => Promise<HeroState>, done?: string): Promise<boolean> {
    setBusy(id);
    setError(null);
    try {
      setState(await call());
      if (done) toast(done);
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(null);
    }
  }

  if (loaded.status === 'loading') return <Loading label="Cargando el hero..." />;
  if (loaded.status === 'error') return <div className="card-elevated"><ErrorState error={loaded.error} /></div>;
  if (!state) return null;

  const live = state.campaigns.find((c) => c.id === state.live_id);
  const forced = state.campaigns.find((c) => c.forced);

  return (
    <div className="max-w-6xl mx-auto pb-12 flex flex-col gap-6">
      <PageHeader title="Hero" subtitle="El video de la portada del sitio, por temporada. Solo lo ven el dueño y quien tenga el rol Hero." />

      {forced && (
        <p role="status" className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-botanica-negro">
          Forzado: «{forced.name}»{forced.forced_until ? `, hasta el ${forced.forced_until}` : ', sin fecha de fin'}. Mientras tanto el sitio no cambia por fecha.
        </p>
      )}
      <p role="status" className="rounded-xl border border-botanica-jade/30 bg-botanica-jade/5 p-3 text-sm text-botanica-negro">
        {live && state.live_reason
          ? <>En el sitio ahora: <strong>{live.name}</strong> ({REASON[state.live_reason]}). Los cambios tardan hasta 5 minutos en verse.</>
          : 'En el sitio ahora: el hero incluido en la página (no hay ninguna temporada publicada para hoy).'}
      </p>
      {!state.media_enabled && (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          El servidor no tiene configurada la carpeta de medios, así que no se pueden subir videos.
        </p>
      )}
      {state.media_enabled && !state.ffmpeg_available && (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          El servidor no tiene ffmpeg instalado, así que no puede procesar videos.
        </p>
      )}
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      <NewCampaign busy={busy !== null}
        onCreate={(name, from, to) => run('new', () => adminApi.createHeroCampaign({
          name, start_month: from[0], start_day: from[1], end_month: to[0], end_day: to[1],
        }), 'Temporada creada')} />

      <div className="grid gap-6 lg:grid-cols-2">
        {state.campaigns.map((c) => (
          <CampaignCard key={c.id} campaign={c} state={state} busy={busy} run={run} />
        ))}
      </div>
    </div>
  );
}
