import { createContext, useContext } from 'react';

// Styled replacements for window.confirm / window.prompt, plus toasts.
// The provider lives in Feedback.tsx; components only use these hooks.

export interface ConfirmOptions {
  title: string;
  body?: string;
  confirmLabel: string;
  cancelLabel?: string;
  /** Red confirm button for destructive actions. */
  tone?: 'default' | 'danger';
  /** Ask for a short free-text reason (e.g. archive); resolves with it. */
  reason?: { label: string; placeholder?: string };
}

/** Resolves to null when cancelled, otherwise to the reason ('' if none). */
export type Confirm = (options: ConfirmOptions) => Promise<string | null>;
export type Toast = (message: string, tone?: 'success' | 'error') => void;

export const FeedbackContext = createContext<{ confirm: Confirm; toast: Toast } | null>(null);

function useFeedback() {
  const value = useContext(FeedbackContext);
  if (!value) throw new Error('FeedbackProvider is missing');
  return value;
}

export const useConfirm = (): Confirm => useFeedback().confirm;
export const useToast = (): Toast => useFeedback().toast;
