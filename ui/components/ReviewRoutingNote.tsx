import type { ReviewRouting } from "@/lib/types";

/**
 * What happened to the hand-off to the review console, in plain words (rule R8).
 *
 * The API reports it on every response whose service hands something to the router. A result
 * that required review but is not queued must say so, rather than read as if a checker has it.
 */
const REVIEW_ROUTING_TEXT: Record<Exclude<ReviewRouting, "not_required">, string> = {
  routed: "Sent to the review console.",
  failed: "Could not reach the review console; this item is not queued for review.",
  off: "Review routing is off in this deployment; this item is not queued for review.",
};

export function ReviewRoutingNote({ routing }: { routing?: ReviewRouting }) {
  if (!routing || routing === "not_required") return null;
  return (
    <p
      data-review-routing={routing}
      className={`mt-1 ${routing === "routed" ? "text-emerald-800" : "text-rose-800"}`}
    >
      {REVIEW_ROUTING_TEXT[routing]}
    </p>
  );
}
