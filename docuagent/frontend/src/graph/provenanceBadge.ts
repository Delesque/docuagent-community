/** Per-module provenance badge for the architecture graph (I2 closing piece).
 *
 *  Provenance claims anchored to a module (see `module_id` whitelisting) surface
 *  on that module's node: confirmed facts as a green count, pending ones
 *  (inferred / recommended / unknown) as an amber count that nudges the user
 *  toward the 来源确认 ledger. Rejected claims are not counted — they are
 *  decisions, not open questions. Unanchored claims are architecture-wide and
 *  stay in the ledger; they never appear as node badges.
 */

import type { ProvenanceClaim } from "../api";

export interface ProvenanceBadge {
  confirmed: number;
  pending: number;
}

const PENDING_SOURCES: ProvenanceClaim["source"][] = ["inferred", "recommended", "unknown"];

export function provenanceBadges(claims: ProvenanceClaim[] | undefined | null): Record<string, ProvenanceBadge> {
  const badges: Record<string, ProvenanceBadge> = {};
  for (const claim of claims ?? []) {
    if (!claim.module_id) continue;
    if (claim.source === "rejected") continue;
    const badge = badges[claim.module_id] ?? { confirmed: 0, pending: 0 };
    if (claim.source === "confirmed") badge.confirmed += 1;
    else if (PENDING_SOURCES.includes(claim.source)) badge.pending += 1;
    badges[claim.module_id] = badge;
  }
  return badges;
}
