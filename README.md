# Jasman Mander

First-year Financial Mathematics and BBA double degree student at Wilfrid Laurier University, building at the intersection of quantitative finance and applied AI.

## Projects

### [ARIA: Insurance Underwriting Copilot](aria-insurance-copilot/)

An AI underwriting assistant that turns a pasted applicant file into a structured risk assessment: a recommended tier, the drivers behind it, the contradictions worth challenging, and a pre-bind verification checklist.

The engineering sits around the model rather than in the chat box. A layered prompt architecture defines the underwriting role and a separate integration contract defines a strict JSON output schema, which the client parses, clamps, and type-checks before anything reaches the interface. Submissions are treated as data rather than instruction, and a prompt-injection case runs on every test pass to prove it. Identifiers are detected and redacted client-side before any message is sent.

Built as one self-contained **90 KB** HTML file with no framework and no bundler, covered by **23** Playwright assertions.

[![ARIA](aria-insurance-copilot/docs/screenshots/assessment.png)](aria-insurance-copilot/)

**Stack:** Vanilla JavaScript, CSS, Anthropic Messages API, Playwright

---

More projects in progress.
