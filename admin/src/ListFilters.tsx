import type { useListFilters } from './hooks';

export function ListFilters({
  status,
  q,
  setStatus,
  setQ,
  searchLabel,
}: ReturnType<typeof useListFilters> & { searchLabel: string }) {
  return (
    <div className="p-4 border-b border-botanica-gris/15 bg-[#FCFBF9] flex flex-col sm:flex-row sm:items-center gap-3">
      <label className="flex items-center gap-2 text-sm font-medium text-botanica-grafito">
        Estado
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="text-sm border border-botanica-gris/30 rounded-md px-3 py-1.5 focus:outline-none focus:border-botanica-jade text-botanica-negro bg-white"
        >
          <option value="">Todos</option>
          <option value="draft">Borrador</option>
          <option value="published">Publicado</option>
          <option value="archived">Archivado</option>
        </select>
      </label>
      <label className="flex items-center gap-2 text-sm font-medium text-botanica-grafito sm:ml-auto">
        <span className="sr-only">{searchLabel}</span>
        <input
          type="search"
          placeholder={searchLabel}
          defaultValue={q}
          maxLength={100}
          onKeyDown={(e) => {
            if (e.key === 'Enter') setQ(e.currentTarget.value.trim());
          }}
          onBlur={(e) => setQ(e.currentTarget.value.trim())}
          className="input-base sm:w-64"
        />
      </label>
    </div>
  );
}
