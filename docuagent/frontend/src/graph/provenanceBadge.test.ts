import { describe, expect, it } from "vitest";
import { provenanceBadges } from "./provenanceBadge";
import type { ProvenanceClaim } from "../api";

const claims: ProvenanceClaim[] = [
  { id: "c1", text: "事实一", source: "confirmed", module_id: "core" },
  { id: "c2", text: "事实二", source: "confirmed", module_id: "core" },
  { id: "c3", text: "推测一", source: "inferred", module_id: "core" },
  { id: "c4", text: "建议", source: "recommended", module_id: "auth" },
  { id: "c5", text: "已拒绝", source: "rejected", module_id: "auth" },
  { id: "c6", text: "无锚点全局事实", source: "confirmed" },
];

describe("provenanceBadges", () => {
  it("counts confirmed and pending per anchored module", () => {
    const badges = provenanceBadges(claims);
    expect(badges.core).toEqual({ confirmed: 2, pending: 1 });
    expect(badges.auth).toEqual({ confirmed: 0, pending: 1 });
  });

  it("ignores rejected claims and unanchored ones", () => {
    const badges = provenanceBadges(claims);
    // auth shows only the pending recommendation; the rejection is a decision,
    // not an open question. c6 has no module_id and never becomes a badge.
    expect(Object.keys(badges).sort()).toEqual(["auth", "core"]);
  });

  it("returns an empty map for missing or empty claims", () => {
    expect(provenanceBadges(undefined)).toEqual({});
    expect(provenanceBadges([])).toEqual({});
  });
});
