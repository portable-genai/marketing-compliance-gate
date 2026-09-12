// The console cannot state a consent, and it says where the consent it shows came from.
//
// Until 2026-09-12 the review form carried a "Granted consents (comma-separated)" input whose
// contents went out as the asset's `granted_consents`. Whatever a reviewer typed there WAS the
// consent the deterministic gate applied, so the gate could be told any permission at all, and
// the seeded regional consent and preference store the same service maintains was never read on
// the review path. Every existing check was green throughout: the form rendered, the request
// validated, the review came back with its consent checks populated and cited.
//
// Two halves here, because the defect had two:
//
// 1. The field is GONE, from the form and from the request shape. This half is a source scan,
//    and it is deliberately over the real files rather than a list of expected strings: a
//    re-spelling (`grantedConsents`, `consents`) would reintroduce the hole under a new name and
//    a literal-string assertion would not see it.
// 2. The console SAYS whose stored records decided the checks. A granted check and an asserted
//    one look identical on screen, which is why the screen could not be used to audit the
//    verdict. `consentProvenance` is executed here over all four states.
//
// The browser-level half of this lives in `tests/browser/test_served_demo_ui.py`, which reads the
// rendered provenance out of the live DOM.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { consentProvenance } from "../lib/consent.mjs";

const UI_ROOT = new URL("..", import.meta.url);

/** The console files a review request is assembled from, read as text. */
const REQUEST_SOURCES = ["app/page.tsx", "lib/api.ts", "lib/types.ts", "components/ReviewView.tsx"];

function source(relative) {
  return readFileSync(new URL(relative, UI_ROOT), "utf8");
}

// Any identifier by which the console could hand the backend a consent of its own. The plural
// forms are what the old field was called; `grantedConsent`/`consentPurpose` catch a rename.
const ASSERTED_CONSENT_SPELLINGS = [
  "granted_consents",
  "grantedConsents",
  "granted_consent",
  "grantedConsent",
  "consent_purposes",
  "consentPurposes",
];

test("no console source can hand the backend a consent of its own", () => {
  for (const relative of REQUEST_SOURCES) {
    const text = source(relative);
    for (const spelling of ASSERTED_CONSENT_SPELLINGS) {
      // A comment may name the retired field to explain why it is gone; code may not use it.
      const code = text
        .split("\n")
        .filter((line) => !line.trimStart().startsWith("//"))
        .join("\n");
      assert.ok(
        !code.includes(spelling),
        `${relative} still carries ${spelling}; consent is read from the store under the ` +
          "verified tenant, and a console that can state one can state any",
      );
    }
  }
});

test("the review form names a subject instead, and says the store answers", () => {
  const page = source("app/page.tsx");
  assert.match(page, /audience_subject_id/, "the form sends no audience subject id");
  assert.match(page, /data-testid="audience-subject-id"/, "the subject input has no stable hook");
  assert.match(
    page,
    /stored consent records/,
    "the form does not tell the reviewer that the store, not the form, decides consent",
  );
  assert.ok(
    !/Granted consents/i.test(page),
    "the 'Granted consents' input is still in the form",
  );
});

test("the request type carries the subject and no consent array", () => {
  const api = source("lib/api.ts");
  assert.match(api, /audience_subject_id\?: string;/, "AssetBody does not carry the subject id");
  assert.ok(!/string\[\];\s*\/\/ consent/i.test(api), "a consent array survives in AssetBody");
});

// --------------------------------------------------------------------------- //
// The rendered provenance: all four states, executed
// --------------------------------------------------------------------------- //
test("a review with records read names the subject, the count and the purposes", () => {
  const text = consentProvenance({
    subject_id: "subj-000101",
    records_read: 2,
    granted_purposes: ["marketing", "profiling"],
    reason: "",
  });
  assert.match(text, /subj-000101/);
  assert.match(text, /2 stored record\(s\)/);
  assert.match(text, /granting marketing, profiling/);
});

test("a subject with no record on file is a refusal the sentence states", () => {
  const text = consentProvenance({
    subject_id: "subj-000199",
    records_read: 0,
    granted_purposes: [],
    reason: "the consent store holds no record for this subject",
  });
  assert.match(text, /holds no record/);
  assert.match(text, /silence is not consent/i);
  assert.ok(
    !/granting/.test(text),
    "a subject with no record must not be described as granting anything",
  );
});

test("records that grant nothing are not rendered as granting something", () => {
  // A withdrawn record IS on file, so the store was read: the count is real and the purpose
  // list is empty. This is the state a typed field could never distinguish from a grant.
  const text = consentProvenance({
    subject_id: "subj-000102",
    records_read: 1,
    granted_purposes: [],
    reason: "",
  });
  assert.match(text, /1 stored record\(s\)/);
  assert.match(text, /granting no purpose/);
});

test("no subject named says so rather than implying a clean read", () => {
  const unasked = { subject_id: "", records_read: 0, granted_purposes: [], reason: "" };
  for (const absent of [undefined, null, unasked]) {
    const text = consentProvenance(absent);
    assert.match(text, /No audience subject named/);
    assert.ok(!/granting/.test(text), "an unasked review must not be described as granting");
  }
});
