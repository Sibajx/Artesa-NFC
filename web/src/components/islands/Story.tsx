// A long text for reading: its first sentence as a pull-quote (display
// face) and the rest in readable body columns. Used for histories and for
// the full biography / description when the hero only shows an excerpt.
import { splitLead } from "@/lib/format";

interface Props {
  id: string;
  title: string;
  text: string;
  /** Pull the first sentence out as a quote (histories) or not (descriptions). */
  quote?: boolean;
}

export function Story({ id, title, text, quote = true }: Props) {
  const { lead, rest } = quote ? splitLead(text) : { lead: "", rest: text };
  const paragraphs = rest.split(/\n{2,}/).filter((p) => p.trim() !== "");
  return (
    <section id={id} className="section quote-section" aria-labelledby={`${id}-title`}>
      <div className="container story">
        <h2 id={`${id}-title`} className="hud-label light-hud">
          {title}
        </h2>
        {lead && (
          <blockquote className="quote">
            <p>{lead}</p>
          </blockquote>
        )}
        {paragraphs.length > 0 && (
          <div className="story__body">
            {paragraphs.map((p, i) => (
              <p key={i}>{p}</p>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
