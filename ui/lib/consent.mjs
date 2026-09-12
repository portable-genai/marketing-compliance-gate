// Where the consent a review applied came from, rendered as one sentence.
//
// Plain `.mjs` for the same reason `csp.mjs` is: the console's test runner is `node --test` over
// `tests/*.test.mjs`, with no TypeScript transform, so anything a test must EXECUTE rather than
// read as text lives here. A string this function returns is the sentence the served page shows.
//
// Why the console says this at all: until 2026-09-12 the review form had a "Granted consents"
// box. Whatever a reviewer typed there became the consent the gate applied, and the regional
// consent and preference store this product maintains was never consulted on the review path. A
// granted consent check and an asserted one rendered identically, so the screen could not be used
// to audit the verdict. The form now names a SUBJECT and the backend reads that subject's stored
// records, and this sentence says which of the four things happened.

/**
 * @typedef {object} ConsentSource
 * @property {string} subject_id
 * @property {number} records_read
 * @property {string[]} granted_purposes
 * @property {string} reason
 */

/**
 * One sentence naming whose stored consent records decided a review's consent checks.
 *
 * Three of the four branches grant nothing, and each says which it was: a reader who cannot tell
 * "the store holds no record for this subject" from "nobody was asked" cannot audit the outcome.
 *
 * @param {ConsentSource | undefined | null} source
 * @returns {string}
 */
export function consentProvenance(source) {
  if (!source || !source.subject_id) {
    return (
      "No audience subject named, so no consent records were read. " +
      "Name one to read the stored consent."
    );
  }
  if (source.reason) {
    return `Subject ${source.subject_id}: ${source.reason}. Nothing is granted; silence is not consent.`;
  }
  const purposes = source.granted_purposes ?? [];
  const granted = purposes.length > 0 ? purposes.join(", ") : "no purpose";
  const count = source.records_read ?? 0;
  return (
    `Subject ${source.subject_id}: ${count} stored record(s) read from the ` +
    `consent and preference store, granting ${granted}.`
  );
}
