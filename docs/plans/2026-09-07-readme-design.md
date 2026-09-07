# README Design

## Purpose

Create an English developer-facing entry point that explains what the Math
Tutor Voice PoC does, how its safety architecture works, how to configure and
run it, and which validation gates remain open.

## Structure

The README will cover the product summary, architecture, technology stack,
repository layout, prerequisites, environment configuration, Docker workflow,
tests and offline evals, voice providers, privacy and therapist review, and
known limitations. It will remain concise and link to `docs/DOCKER.md` and
`docs/math-tutor-poc-verification.md` for details that change independently.

## Accuracy constraints

- Docker is the only supported Python/uv/pytest environment.
- `THERAPIST_API_TOKEN` is mandatory for web startup and must satisfy the
  implemented strength policy.
- Offline eval fixtures must not be described as therapist evidence.
- Professional tabletop review and supervised voice validation remain pending.
- The repository is not approved for a trial involving children.

## Verification

Check every documented command and local URL against the Makefile, Compose
configuration, `.env.example`, and existing operational documentation. Verify
relative links and run `git diff --check`.
