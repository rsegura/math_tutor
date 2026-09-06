const statusNode = document.querySelector("#status");
const learnersNode = document.querySelector("#learners");
const sessionsNode = document.querySelector("#sessions");
let credential = "";
let current = null;
const playbackUrls = new Map();

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

function evidenceReference(evidenceId) {
  const link=node("a",evidenceId); link.href=`#evidence-${encodeURIComponent(evidenceId)}`;
  link.addEventListener("click",()=>{ const target=document.getElementById(`evidence-${evidenceId}`); if (target) target.focus(); });
  return link;
}

function renderClaim(target, item) {
  const block=document.createElement("article");
  block.append(node("strong",item.status === "hypothesis" ? "Hipótesis" : "Hecho"),node("p",item.text),node("span","Evidencias: "));
  item.evidence_ids.forEach((id,index)=>{ if(index) block.append(node("span",", ")); block.append(evidenceReference(id)); });
  target.append(block);
}

function revokePlayback(clipId) {
  const prior=playbackUrls.get(clipId); if (prior) { URL.revokeObjectURL(prior); playbackUrls.delete(clipId); }
}

async function showSession(learnerId, sessionId) {
  const detail = await request(`/api/review/learners/${encodeURIComponent(learnerId)}/sessions/${encodeURIComponent(sessionId)}`);
  current=detail;
  playbackUrls.forEach(value=>URL.revokeObjectURL(value)); playbackUrls.clear();
  const progress=document.querySelector("#progress"), assistance=document.querySelector("#assistance"), evidence=document.querySelector("#evidence");
  const claims=document.querySelector("#claims"), hypotheses=document.querySelector("#hypotheses"), profileProposals=document.querySelector("#profile-proposals"), nextObjectives=document.querySelector("#next-objectives"), history=document.querySelector("#history");
  [progress,assistance,evidence,claims,hypotheses,profileProposals,nextObjectives,history].forEach(clear);
  detail.objective_estimates.forEach(item => {
    const confidence=item.support_confidence.minimum === null ? "sin soporte retenido" : `confianza STT ${item.support_confidence.minimum}–${item.support_confidence.maximum}`;
    const block=node("article",`${item.objective_id}: ${item.state} · ${item.status} · versión ${item.estimate_version} · ${confidence}`);
    item.supporting_evidence_ids.forEach(id=>block.append(evidenceReference(id)));
    const select=document.createElement("select");
    ["not-observed","exploring","with-intensive-help","with-light-help","independent","generalized","needs-review"].forEach(value=>{ const option=node("option",value); option.value=value; select.append(option); });
    const button=node("button","Corregir estimación"); button.type="button";
    button.addEventListener("click",()=>command({kind:"correct-skill-estimate",command_id:crypto.randomUUID(),review_id:`estimate-${item.objective_id}`,objective_id:item.objective_id,corrected_state:select.value,expected_review_version:detail.history.filter(entry=>entry.review_id===`estimate-${item.objective_id}`).length}));
    block.append(select,button); progress.append(block);
  });
  detail.assistance_observations.forEach(item=>assistance.append(node("p",`${item.objective_id}: nivel ${item.assistance_level} · evidencia ${item.evidence_id}`)));
  detail.evidence.forEach(item => {
    const block=node("article", `${item.objective_id}: ${item.response_excerpt} · ayuda ${item.assistance_level}`);
    block.id=`evidence-${item.evidence_id}`; block.tabIndex=-1;
    if (item.clip) {
      const listen=node("button",`Escuchar clip (${item.clip.duration_seconds}s)`); listen.type="button";
      const remove=node("button","Eliminar clip"); remove.type="button";
      listen.addEventListener("click",async()=>{ const response=await fetch(`/api/review/learners/${encodeURIComponent(learnerId)}/sessions/${encodeURIComponent(sessionId)}/clips/${encodeURIComponent(item.clip.clip_id)}`,{headers:{Authorization:`Bearer ${credential}`},cache:"no-store"}); if (!response.ok) { statusNode.textContent="El clip ya no está disponible."; return; } revokePlayback(item.clip.clip_id); const url=URL.createObjectURL(await response.blob()); playbackUrls.set(item.clip.clip_id,url); const audio=document.createElement("audio"); audio.controls=true; audio.src=url; block.append(audio); listen.disabled=true; });
      remove.addEventListener("click",async()=>{ if (!window.confirm("¿Eliminar permanentemente este clip de evidencia?")) return; const result=await request(`/api/review/learners/${encodeURIComponent(learnerId)}/sessions/${encodeURIComponent(sessionId)}/clips/${encodeURIComponent(item.clip.clip_id)}`,{method:"DELETE"}); revokePlayback(item.clip.clip_id); block.querySelectorAll("audio").forEach(audio=>audio.remove()); listen.remove(); remove.disabled=true; remove.textContent=result.status === "deleted" ? "Clip eliminado" : "Clip ya eliminado"; statusNode.textContent="El clip ya no puede reproducirse."; });
      block.append(listen,remove);
    }
    const discard=node("button","Descartar evidencia"); discard.type="button";
    discard.addEventListener("click",()=>command({kind:"discard-evidence",command_id:crypto.randomUUID(),review_id:`evidence-${item.evidence_id}`,evidence_id:item.evidence_id,expected_review_version:detail.history.filter(entry=>entry.review_id===`evidence-${item.evidence_id}`).length}));
    block.append(discard);
    evidence.append(block);
  });
  detail.authoritative_claims.forEach(item=>renderClaim(claims,item));
  detail.hypotheses.forEach(item=>renderClaim(hypotheses,item));
  detail.profile_change_proposals.forEach(item=>renderClaim(profileProposals,item));
  if (!detail.next_objective_proposals.length) nextObjectives.append(node("p","No hay propuestas de siguiente objetivo registradas."));
  detail.next_objective_proposals.forEach(item=>renderClaim(nextObjectives,item));
  detail.history.forEach(item => { const action=item.action; const target=action.evidence_id || action.objective_id || "acción"; const original=action.original_proposed_state || action.original_outcome || "sin valor propuesto"; const corrected=action.corrected_state || (action.evidence_id ? "descartada" : "sin cambio"); history.append(node("p",`${item.created_at || "fecha no disponible"} · revisión ${item.version} · ${target} · original: ${original} · resultado: ${corrected} · motivo: ${action.reason}`)); });
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
