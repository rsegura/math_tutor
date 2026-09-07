# Browser Audio Playback Design

## Problem

The LiveKit agent publishes a valid remote audio track, but the learner client
never subscribes that track to an HTML media element. The room therefore works
while the browser remains silent.

## Design

Register room event handlers before connecting. On a subscribed remote audio
track, attach it to a managed autoplay audio element. Resume LiveKit audio from
the learner's join interaction to satisfy browser autoplay policies. On track
unsubscription or room disconnect, detach and remove owned media elements.
Video and local tracks must not create audio elements, and repeated events must
not leak or duplicate media nodes.

## Verification

Add a browser-client contract test that exercises subscription, playback
resume, unsubscription cleanup, and error/status handling using a fake LiveKit
client. Run the full offline suite and the math eval gate.
