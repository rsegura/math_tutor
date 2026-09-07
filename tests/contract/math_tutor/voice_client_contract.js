"use strict";

const assert = require("node:assert/strict");

const events = [];
let submit;
let latestRoom;
let blockAudio = false;

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

global.document = {
  querySelector(selector) {
    if (selector === "#join") {
      return {addEventListener: (_event, handler) => { submit = handler; }};
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
      setMicrophoneEnabled: async () => { events.push("microphone"); },
    };
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
  }

  disconnect() {
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

global.fetch = async () => {
  events.push("fetch");
  return {
    ok: true,
    json: async () => ({server_url: "ws://livekit", participant_token: "token"}),
  };
};

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
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
