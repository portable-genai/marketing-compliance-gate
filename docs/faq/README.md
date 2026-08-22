# FAQ index

Answers to the questions different teams ask when evaluating, adopting, or reviewing this
repository as a common base. Each file is written for a specific audience; skim the one that
matches your role.

| FAQ | For | Answers |
|---|---|---|
| [security-faq.md](security-faq.md) | AppSec / security review | authn/authz, tenancy, secrets, supply chain, the audit chain, what is in vs out of scope |
| [portability-faq.md](portability-faq.md) | Architecture / cloud / exit planning | no-lock-in, the four profiles, on-prem / sovereign exit, data export |
| [features-faq.md](features-faq.md) | Product / compliance / delivery | what the agent does, what is deterministic vs LLM, and the boundary with sibling platform systems |
| [adoption-faq.md](adoption-faq.md) | Engineering leads forking the repo | rename, upstream fixes, extension points, versioning |
| [compliance-faq.md](compliance-faq.md) | Compliance / brand / model risk | regulatory posture, consent, maker-checker, residency, model-risk evidence |

These FAQs deliberately do **not** re-document capabilities owned by sibling systems in the
[catalog](https://github.com/portable-genai). Where a concern belongs to another
repo (the guardrail gateway, the governed rule / knowledge base, the eval platform, the
human-review console, ...), the FAQ points at it and explains the boundary rather than
duplicating it. See [features-faq.md](features-faq.md) for the full "what this repo owns vs
what it integrates" map.
