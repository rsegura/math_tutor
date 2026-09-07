const form = document.querySelector("#join");
const joinButton = form.querySelector("button");
const statusNode = document.querySelector("#status");
const audioContainer = document.querySelector("#remote-audio");

// Keep the connected room reachable for the lifetime of the page.
let activeRoom = null;
let pendingRoom = null;
let pendingTokenRequest = null;
let joinGeneration = 0;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const generation = ++joinGeneration;
  const isCurrentJoin = () => generation === joinGeneration;
  joinButton.disabled = true;
  statusNode.textContent = "Conectando…";

  if (pendingTokenRequest) pendingTokenRequest.abort();
  pendingTokenRequest = null;
  if (pendingRoom) pendingRoom.disconnect();
  pendingRoom = null;
  if (activeRoom) {
    activeRoom.disconnect();
    activeRoom = null;
  }

  const room = new LivekitClient.Room();
  pendingRoom = room;
  const attachedAudio = new Map();
  let intentionalDisconnect = false;
  let disconnected = false;
  let tokenRequest = null;

  const removeAudioTrack = (track) => {
    const attachment = attachedAudio.get(track.sid);
    if (!attachment) return;

    const {element} = attachment;
    try {
      track.detach(element);
    } finally {
      element.remove();
      attachedAudio.delete(track.sid);
    }
  };

  const removeAllAudioTracks = () => {
    for (const {track} of attachedAudio.values()) {
      removeAudioTrack(track);
    }
    attachedAudio.clear();
  };

  // Subscription events represent remote tracks. The participant check keeps
  // that boundary explicit if the client API ever broadens the event contract.
  room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, _publication, participant) => {
    if (participant === room.localParticipant) return;
    if (track.kind !== LivekitClient.Track.Kind.Audio) return;
    if (attachedAudio.has(track.sid)) return;

    const element = track.attach();
    element.autoplay = true;
    element.playsInline = true;
    audioContainer.appendChild(element);
    attachedAudio.set(track.sid, {track, element});
  });
  room.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track) => {
    removeAudioTrack(track);
  });
  room.on(LivekitClient.RoomEvent.Disconnected, () => {
    disconnected = true;
    if (tokenRequest && !tokenRequest.signal.aborted) tokenRequest.abort();
    if (pendingTokenRequest === tokenRequest) pendingTokenRequest = null;
    removeAllAudioTracks();
    if (activeRoom === room) activeRoom = null;
    if (pendingRoom === room) pendingRoom = null;
    if (!intentionalDisconnect && isCurrentJoin()) {
      statusNode.textContent = "La sesión se ha desconectado.";
      joinButton.disabled = false;
    }
  });

  // Run as the first async operation so browsers treat it as part of the join gesture.
  const audioStartPromise = room.startAudio()
    .then(() => null)
    .catch(() => "Sesión conectada, pero el navegador bloqueó el audio.");

  try {
    tokenRequest = new AbortController();
    pendingTokenRequest = tokenRequest;
    const response = await fetch("/api/token", {
      method: "POST", headers: {"content-type": "application/json"},
      signal: tokenRequest.signal,
      body: JSON.stringify({tutoring_session_id: document.querySelector("#session").value, join_code: document.querySelector("#code").value})
    });
    if (!isCurrentJoin()) {
      intentionalDisconnect = true;
      room.disconnect();
      return;
    }
    pendingTokenRequest = null;
    if (!response.ok) {
      intentionalDisconnect = true;
      room.disconnect();
      statusNode.textContent = "No se pudo abrir la sesión.";
      return;
    }
    const issued = await response.json();
    if (!isCurrentJoin() || disconnected) {
      intentionalDisconnect = true;
      room.disconnect();
      return;
    }
    await room.connect(issued.server_url, issued.participant_token);
    if (!isCurrentJoin() || disconnected) {
      intentionalDisconnect = true;
      room.disconnect();
      return;
    }
    pendingRoom = null;
    activeRoom = room;
    await room.localParticipant.setMicrophoneEnabled(true);
    if (!isCurrentJoin() || disconnected) {
      await room.localParticipant.setMicrophoneEnabled(false);
      intentionalDisconnect = true;
      room.disconnect();
      return;
    }
    const audioError = await audioStartPromise;
    if (isCurrentJoin()) statusNode.textContent = audioError || "Sesión conectada";
  } catch (_error) {
    intentionalDisconnect = true;
    room.disconnect();
    if (activeRoom === room) activeRoom = null;
    if (pendingRoom === room) pendingRoom = null;
    if (isCurrentJoin()) statusNode.textContent = "No se pudo conectar la sesión.";
  } finally {
    if (isCurrentJoin()) {
      pendingTokenRequest = null;
      joinButton.disabled = false;
    }
  }
});
