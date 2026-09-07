# Browser Audio Playback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make published LiveKit agent audio audible and lifecycle-safe in the learner browser.

**Architecture:** Keep media playback at the web/LiveKit boundary. Register handlers before room connection, attach only subscribed remote audio tracks, resume playback from the join gesture, and remove media nodes when tracks leave.

**Tech Stack:** LiveKit Client JS 2.15.7, browser DOM APIs, pytest documentation/static-client contracts, Docker.

---

### Task 1: Subscribe and play remote audio

**Files:**
- Modify: `web/static/app.js`
- Modify: `web/static/index.html` if a managed media container is needed
- Test: create or modify the appropriate web client contract test under `tests/contract/math_tutor/`

**Steps:**

1. Write a failing contract test proving the current client never attaches a
   subscribed remote audio track.
2. Register `TrackSubscribed`, `TrackUnsubscribed`, and disconnect handlers
   before `room.connect()`.
3. Attach only remote audio tracks, avoid duplicate nodes, and clean them up on
   unsubscription/disconnect.
4. Call `room.startAudio()` from the join flow and surface a concise status if
   browser playback cannot start.
5. Run focused tests, `make test`, and `make eval-math` in Docker.
6. Commit with `fix: play remote tutor audio in learner client`.
