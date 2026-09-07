const form = document.querySelector("#join");
const statusNode = document.querySelector("#status");
const audioContainer = document.querySelector("#remote-audio");

// Keep the connected room reachable for the lifetime of the page.
let activeRoom = null;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusNode.textContent = "Conectando…";

  if (activeRoom) {
    activeRoom.disconnect();
    activeRoom = null;
  }

  const room = new LivekitClient.Room();
  const attachedAudio = new Map();

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
    removeAllAudioTracks();
    if (activeRoom === room) activeRoom = null;
  });

  // Run as the first async operation so browsers treat it as part of the join gesture.
  const audioStartPromise = room.startAudio()
    .then(() => null)
    .catch(() => "Sesión conectada, pero el navegador bloqueó el audio.");

  try {
    const response = await fetch("/api/token", {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({tutoring_session_id: document.querySelector("#session").value, join_code: document.querySelector("#code").value})
    });
    if (!response.ok) {
      room.disconnect();
      statusNode.textContent = "No se pudo abrir la sesión.";
      return;
    }
    const issued = await response.json();
    await room.connect(issued.server_url, issued.participant_token);
    activeRoom = room;
    await room.localParticipant.setMicrophoneEnabled(true);
    const audioError = await audioStartPromise;
    statusNode.textContent = audioError || "Sesión conectada";
  } catch (_error) {
    room.disconnect();
    if (activeRoom === room) activeRoom = null;
    statusNode.textContent = "No se pudo conectar la sesión.";
  }
});
