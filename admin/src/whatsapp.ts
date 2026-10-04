// P-026 G3: opens a WhatsApp chat with the message ready. With a number the
// chat goes straight to that person; without one, WhatsApp asks whom to send
// it to.
export function whatsappUrl(number: string | null | undefined, text: string): string {
  const digits = (number ?? '').replace(/\D/g, '');
  return `https://wa.me/${digits}?text=${encodeURIComponent(text)}`;
}
