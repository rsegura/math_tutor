const statusNode = document.querySelector("#status");
const learnersNode = document.querySelector("#learners");
const sessionsNode = document.querySelector("#sessions");
let credential = "";
let current = null;

function node(tag, text) {
  const value = document.createElement(tag);
  value.textContent = text;
  return value;
}

async function request(path, options = {}) {
  const response = await fetch(path, {...options, headers: {...options.headers, Authorization: `Bearer ${credential}`}, cache: "no-store"});
  if (!response.ok) throw new Error(`request-${response.status}`);
  return response.json();
}

async function command(payload) {
  if (!current) return;
  const reason=document.querySelector("#reason").value.trim();
  if (!reason) { statusNode.textContent="Escribe un motivo para la corrección."; return; }
  try {
    await request(`/api/review/learners/${encodeURIComponent(current.learner.learner_id)}/sessions/${encodeURIComponent(current.session_id)}/corrections`, {method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({...payload,reason,expected_profile_version:current.profile_version})});
    await showSession(current.learner.learner_id,current.session_id);
    statusNode.textContent="Corrección registrada; el historial original se conserva.";
  } catch (_) { statusNode.textContent="La revisión cambió. Recarga la sesión antes de corregir."; }
}

function clear(target) { while (target.firstChild) target.removeChild(target.firstChild); }

async function showSession(learnerId, sessionId) {
  const detail = await request(`/api/review/learners/${encodeURIComponent(learnerId)}/sessions/${encodeURIComponent(sessionId)}`);
  current=detail;
  const progress = document.querySelector("#progress"); const evidence = document.querySelector("#evidence");
  const hypotheses = document.querySelector("#hypotheses"); const history = document.querySelector("#history");
  [progress,evidence,hypotheses,history].forEach(clear);
  detail.objectives.forEach(item => {
    const block=node("article",`${item.objective_id}: ${item.state} (${item.status})`);
    const select=document.createElement("select");
    ["not-observed","exploring","with-intensive-help","with-light-help","independent","generalized","needs-review"].forEach(value=>{ const option=node("option",value); option.value=value; select.append(option); });
    const button=node("button","Corregir estimación"); button.type="button";
    button.addEventListener("click",()=>command({kind:"correct-skill-estimate",command_id:crypto.randomUUID(),review_id:`estimate-${item.objective_id}`,objective_id:item.objective_id,corrected_state:select.value,expected_review_version:detail.history.filter(entry=>entry.review_id===`estimate-${item.objective_id}`).length}));
    block.append(select,button); progress.append(block);
  });
  detail.evidence.forEach(item => {
    const block=node("article", `${item.objective_id}: ${item.response_excerpt} · ayuda ${item.assistance_level}`);
    if (item.clip) { const button=node("button",`Escuchar clip (${item.clip.duration_seconds}s)`); button.type="button"; button.addEventListener("click",async()=>{ const response=await fetch(`/api/review/learners/${encodeURIComponent(learnerId)}/sessions/${encodeURIComponent(sessionId)}/clips/${encodeURIComponent(item.clip.clip_id)}`,{headers:{Authorization:`Bearer ${credential}`},cache:"no-store"}); if (!response.ok) { statusNode.textContent="El clip ya no está disponible."; return; } const audio=document.createElement("audio"); audio.controls=true; audio.src=URL.createObjectURL(await response.blob()); block.append(audio); button.remove(); }); block.append(button); }
    const discard=node("button","Descartar evidencia"); discard.type="button";
    discard.addEventListener("click",()=>command({kind:"discard-evidence",command_id:crypto.randomUUID(),review_id:`evidence-${item.evidence_id}`,evidence_id:item.evidence_id,expected_review_version:detail.history.filter(entry=>entry.review_id===`evidence-${item.evidence_id}`).length}));
    block.append(discard);
    evidence.append(block);
  });
  detail.summary.filter(item => item.status === "hypothesis").forEach(item => hypotheses.append(node("p", `Hipótesis: ${item.text}`)));
  detail.history.forEach(item => history.append(node("p", `${item.review_id} · revisión ${item.version}`)));
  statusNode.textContent=`${detail.learner.pseudonym} · sesión ${detail.session_id}`;
}

async function showSessions(learnerId) {
  const data=await request(`/api/review/learners/${encodeURIComponent(learnerId)}/sessions`); clear(sessionsNode);
  data.sessions.forEach(item => { const button=node("button", item.session_id); button.type="button"; button.addEventListener("click",()=>showSession(learnerId,item.session_id)); const li=node("li",""); li.append(button); sessionsNode.append(li); });
}

document.querySelector("#login").addEventListener("submit", async event => {
  event.preventDefault(); credential=document.querySelector("#credential").value; document.querySelector("#credential").value="";
  try { const data=await request("/api/review/learners"); clear(learnersNode); data.learners.forEach(item => { const button=node("button",item.pseudonym); button.type="button"; button.addEventListener("click",()=>showSessions(item.learner_id)); const li=node("li",""); li.append(button); learnersNode.append(li); }); statusNode.textContent="Selecciona un alumno."; }
  catch (_) { credential=""; statusNode.textContent="No se pudo autorizar la revisión."; }
});
