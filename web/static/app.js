const form = document.querySelector("#join");
const statusNode = document.querySelector("#status");
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusNode.textContent = "Conectando…";
  const response = await fetch("/api/token", {
    method: "POST", headers: {"content-type": "application/json"},
    body: JSON.stringify({tutoring_session_id: document.querySelector("#session").value, join_code: document.querySelector("#code").value})
  });
  if (!response.ok) { statusNode.textContent = "No se pudo abrir la sesión."; return; }
  const issued = await response.json();
  const room = new LivekitClient.Room();
  await room.connect(issued.server_url, issued.participant_token);
  await room.localParticipant.setMicrophoneEnabled(true);
  statusNode.textContent = "Sesión conectada";
});
