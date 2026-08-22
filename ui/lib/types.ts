/**
 * Domain shapes mirrored from the D6 FastAPI backend (the JSON shape of a cited
 * Review produced by `to_jsonable`).
 */

export type Market = "JP" | "AU" | "SG";
export type Vertical = "banking" | "online_retail";
export type AssetType = "campaign" | "creative" | "offer";

export interface Citation {
  source_id: string;
  source_type: string;
  title: string;
  url?: string;
  page?: number | null;
  published_date?: string | null;
  snippet?: string;
  score?: number | null;
}

export interface ClaimFinding {
  rule_id: string;
  rule_kind: string;
  status: "pass" | "fail";
  severity: string;
  message: string;
  evidence?: string;
  remediation?: string;
  citations: Citation[];
}

export interface ConsentCheck {
  purpose: string;
  required: boolean;
  granted: boolean;
  rule_id?: string;
  citations: Citation[];
}

export interface ApprovalRecord {
  review_id: string;
  decision: "pending" | "approved" | "rejected";
  checker?: string;
  rationale?: string;
  decided_at?: string | null;
}

export interface Review {
  id: string;
  asset_id: string;
  asset_type: AssetType;
  market: Market;
  vertical: Vertical;
  outcome: "compliant" | "non_compliant";
  findings: ClaimFinding[];
  consent_checks: ConsentCheck[];
  summary: string;
  citations: Citation[];
  approval: ApprovalRecord | null;
  requires_human_review: boolean;
}

export interface Health {
  status: string;
  profile: string;
  market: string;
  vertical: string;
}

// A seeded dev persona for the local-profile identity picker (GET /v1/personas).
export interface Persona {
  id: string;
  subject: string;
  tenant: string;
  principals: string;
}

// ---------------------------------------------------------------------------
// Green claims and substantiation (the anti-greenwashing gate).
// The verdict and the coverage number are decided by the backend's deterministic
// coverage engine; this console only renders them.
// ---------------------------------------------------------------------------
export type SubstantiationVerdict =
  | "not_applicable"
  | "substantiated"
  | "partially_substantiated"
  | "unsubstantiated";

export interface GreenClaim {
  category: string;
  phrase: string;
  location: string;
}

export interface ClaimCoverage {
  claim: GreenClaim;
  verdict: SubstantiationVerdict;
  coverage: number;
  required_kinds: string[];
  satisfied_kinds: string[];
  missing_kinds: string[];
  evidence_ids: string[];
  expired_evidence_ids: string[];
  gaps: string[];
  remediation?: string;
  citations: Citation[];
}

export interface SubstantiationAssessment {
  id: string;
  asset_id: string;
  tenant: string;
  market: Market;
  vertical: Vertical;
  as_of: string;
  verdict: SubstantiationVerdict;
  coverage: number;
  claims: ClaimCoverage[];
  findings: ClaimFinding[];
  narrative: string;
  citations: Citation[];
  requires_human_review: boolean;
}

// One substantiation record held by the caller's OWN tenant (GET /v1/evidence).
export interface SubstantiationEvidence {
  id: string;
  tenant: string;
  asset_id: string;
  kind: string;
  title: string;
  categories: string[];
  issued_date?: string;
  valid_until?: string;
  issuer?: string;
  independently_verified: boolean;
  reference?: string;
}
