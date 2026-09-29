import type { ReactNode } from 'react';

const INPUT =
  'w-full px-3 py-2 border border-botanica-gris/30 rounded-md text-sm text-botanica-negro bg-white focus:outline-none focus:border-botanica-jade disabled:bg-botanica-hueso disabled:text-botanica-gris';

interface FieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  error?: string;
  required?: boolean;
  disabled?: boolean;
  placeholder?: string;
}

function Wrapper({ id, label, hint, error, required, children }: Omit<FieldProps, 'value' | 'onChange'> & { children: ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-botanica-grafito mb-1">
        {label}
        {required && <span aria-hidden="true" className="text-botanica-jade"> *</span>}
      </label>
      {children}
      {hint && !error && <p id={`${id}-hint`} className="mt-1 text-xs text-botanica-gris">{hint}</p>}
      {error && <p id={`${id}-error`} className="mt-1 text-xs text-red-700">{error}</p>}
    </div>
  );
}

export function TextInput(props: FieldProps & { type?: 'text' | 'number' }) {
  const { id, value, onChange, error, hint, required, disabled, placeholder, type = 'text' } = props;
  return (
    <Wrapper {...props}>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        disabled={disabled}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={INPUT}
      />
    </Wrapper>
  );
}

export function TextArea(props: FieldProps) {
  const { id, value, onChange, error, hint, disabled, placeholder } = props;
  return (
    <Wrapper {...props}>
      <textarea
        id={id}
        rows={4}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={INPUT}
      />
    </Wrapper>
  );
}

export function Select(props: FieldProps & { options: { value: string; label: string }[] }) {
  const { id, value, onChange, error, disabled, options } = props;
  return (
    <Wrapper {...props}>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}
        aria-invalid={error ? true : undefined} className={INPUT}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </Wrapper>
  );
}

export function Checkbox({ id, label, checked, onChange }: { id: string; label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm text-botanica-grafito">
      <input id={id} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="accent-botanica-jade" />
      {label}
    </label>
  );
}

export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-md text-sm">
      {message}
    </div>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="flex flex-col gap-4 border-t border-botanica-gris/15 pt-6 first:border-0 first:pt-0">
      <legend className="text-lg font-serif text-botanica-negro mb-2">{title}</legend>
      {children}
    </fieldset>
  );
}
