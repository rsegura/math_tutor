"use strict";

const assert = require("node:assert/strict");

const events = [];
let submit;
let latestRoom;
let blockAudio = false;
let fetchImpl;
let nextConnectPromise = null;

const statusNode = {textContent: ""};
const audioContainer = {
  children: [],
  appendChild(element) {
    this.children.push(element);
    element.parent = this;
  },
};
const fields = {
  "#session": {value: "session-1"},
  "#code": {value: "join-code"},
};
const joinButton = {disabled: false};
const form = {
  addEventListener: (_event, handler) => { submit = handler; },
  querySelector: (selector) => selector === "button" ? joinButton : null,
};

global.document = {
  querySelector(selector) {
    if (selector === "#join") {
      return form;
    }
    if (selector === "#status") return statusNode;
    if (selector === "#remote-audio") return audioContainer;
    return fields[selector];
  },
};

class FakeRoom {
  constructor() {
    this.handlers = new Map();
    this.localParticipant = {
      identity: "learner",
      setMicrophoneEnabled: async (enabled) => { this.microphoneEnabled = enabled; events.push("microphone"); },
    };
    this.disconnected = false;
    latestRoom = this;
  }

  on(event, handler) {
    this.handlers.set(event, handler);
  }

  startAudio() {
    events.push("startAudio");
    return blockAudio ? Promise.reject(new Error("blocked")) : Promise.resolve();
  }

  async connect() {
    assert.ok(this.handlers.has("trackSubscribed"));
    assert.ok(this.handlers.has("trackUnsubscribed"));
    assert.ok(this.handlers.has("disconnected"));
    events.push("connect");
    const barrier = nextConnectPromise;
    if (barrier) await barrier;
  }

  disconnect() {
    this.disconnected = true;
    events.push("disconnect");
    this.handlers.get("disconnected")?.();
  }

  emit(event, ...args) {
    this.handlers.get(event)(...args);
  }
}

global.LivekitClient = {
  Room: FakeRoom,
  RoomEvent: {
    TrackSubscribed: "trackSubscribed",
    TrackUnsubscribed: "trackUnsubscribed",
    Disconnected: "disconnected",
  },
  Track: {Kind: {Audio: "audio", Video: "video"}},
};

const successfulTokenResponse = () => ({
  ok: true,
  json: async () => ({server_url: "ws://livekit", participant_token: "token"}),
});

fetchImpl = async () => {
  events.push("fetch");
  return successfulTokenResponse();
};
global.fetch = (...args) => fetchImpl(...args);

function deferred() {
  let resolve;
  const promise = new Promise((fulfil) => { resolve = fulfil; });
  return {promise, resolve};
}

function mediaElement() {
  return {
    removed: false,
    remove() {
      this.removed = true;
      if (this.parent) {
        this.parent.children = this.parent.children.filter((child) => child !== this);
      }
    },
  };
}

function remoteTrack(sid, kind = "audio") {
  const element = mediaElement();
  return {
    sid,
    kind,
    element,
    attachCalls: 0,
    detachCalls: 0,
    attach() { this.attachCalls += 1; return element; },
    detach(attached) { assert.equal(attached, element); this.detachCalls += 1; },
  };
}

async function join() {
  await submit({preventDefault() {}});
  return latestRoom;
}

async function run() {
  require("../../../web/static/app.js");

  const room = await join();
  assert.ok(events.indexOf("startAudio") < events.indexOf("fetch"));
  assert.equal(statusNode.textContent, "Sesión conectada");

  const tutorAudio = remoteTrack("audio-1");
  room.emit("trackSubscribed", tutorAudio, {}, {identity: "tutor"});
  room.emit("trackSubscribed", tutorAudio, {}, {identity: "tutor"});
  assert.equal(tutorAudio.attachCalls, 1, "duplicate events must not duplicate audio");
  assert.equal(audioContainer.children.length, 1);
  assert.equal(tutorAudio.element.autoplay, true);
  assert.equal(tutorAudio.element.playsInline, true);

  const tutorVideo = remoteTrack("video-1", "video");
  room.emit("trackSubscribed", tutorVideo, {}, {identity: "tutor"});
  assert.equal(tutorVideo.attachCalls, 0, "video must not create an audio element");

  const localAudio = remoteTrack("local-audio");
  room.emit("trackSubscribed", localAudio, {}, room.localParticipant);
  assert.equal(localAudio.attachCalls, 0, "local audio must not be attached as remote audio");

  room.emit("trackUnsubscribed", tutorAudio);
  assert.equal(tutorAudio.detachCalls, 1);
  assert.equal(tutorAudio.element.removed, true);
  assert.equal(audioContainer.children.length, 0);

  const replacement = remoteTrack("audio-2");
  room.emit("trackSubscribed", replacement, {}, {identity: "tutor"});
  blockAudio = true;
  const nextRoom = await join();
  assert.equal(replacement.detachCalls, 1, "joining again must clean up the old room");
  assert.equal(audioContainer.children.length, 0);
  assert.equal(statusNode.textContent, "Sesión conectada, pero el navegador bloqueó el audio.");

  const finalTrack = remoteTrack("audio-3");
  nextRoom.emit("trackSubscribed", finalTrack, {}, {identity: "tutor"});
  nextRoom.disconnect();
  assert.equal(finalTrack.detachCalls, 1);
  assert.equal(audioContainer.children.length, 0);
  assert.equal(statusNode.textContent, "La sesión se ha desconectado.");

  blockAudio = false;
  const delayedToken = deferred();
  let firstSignal;
  fetchImpl = (_url, options) => {
    firstSignal = options.signal;
    return delayedToken.promise;
  };
  const staleJoin = submit({preventDefault() {}});
  const staleRoom = latestRoom;
  assert.equal(joinButton.disabled, true);

  fetchImpl = async () => successfulTokenResponse();
  const currentJoin = submit({preventDefault() {}});
  const currentRoom = latestRoom;
  await currentJoin;
  delayedToken.resolve(successfulTokenResponse());
  await staleJoin;

  assert.equal(firstSignal.aborted, true, "a newer join must abort the stale token request");
  assert.equal(staleRoom.disconnected, true);
  assert.notEqual(currentRoom, staleRoom);
  assert.equal(currentRoom.disconnected, false);
  assert.equal(staleRoom.microphoneEnabled, undefined);
  assert.equal(currentRoom.microphoneEnabled, true);
  assert.equal(statusNode.textContent, "Sesión conectada");
  assert.equal(joinButton.disabled, false);

  const delayedConnect = deferred();
  nextConnectPromise = delayedConnect.promise;
  const staleConnectJoin = submit({preventDefault() {}});
  const staleConnectingRoom = latestRoom;
  await Promise.resolve();
  await Promise.resolve();

  nextConnectPromise = null;
  const finalJoin = submit({preventDefault() {}});
  const finalRoom = latestRoom;
  await finalJoin;
  delayedConnect.resolve();
  await staleConnectJoin;

  assert.equal(staleConnectingRoom.disconnected, true);
  assert.equal(staleConnectingRoom.microphoneEnabled, undefined);
  assert.equal(finalRoom.disconnected, false);
  assert.equal(finalRoom.microphoneEnabled, true);
  assert.equal(statusNode.textContent, "Sesión conectada");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
