/* Consultas locais autenticadas; resumos recentes somente na memória do navegador. Sem inferência. */
(()=>{
const el=id=>document.getElementById(id);
let token='',current=null,busy=false,refreshing=false,recipes=[];
let requestKey=sessionStorage.getItem('flow-request-key')||crypto.randomUUID();
sessionStorage.setItem('flow-request-key',requestKey);
let submitted=sessionStorage.getItem('flow-request-submitted')===requestKey;
const names={registered:'Pedido registrado',waiting:'Aguardando disponibilidade',dispatching:'Encaminhando',awaiting_ack:'Aguardando aceite',uncertain:'Entrega incerta — conferir',accepted:'Recebimento confirmado',paused:'Pausada',resumed:'Retomada registrada',needs_review:'Conferência necessária',recorded:'Registrado',queued:'Na fila',checkpoint:'Registrado no checkpoint',forwarded:'Encaminhado; confirmação pendente',premium_active:'Executando no principal',reserve_active:'Executando na reserva',handoff_preparing:'Preparando passagem para a reserva',review:'Em validação',completed:'Concluída',blocked:'Interrompida',running:'Em execução',done:'Etapa registrada',awaiting_operations:'Aguardando operações',awaiting_delegations:'Aguardando especialistas',waiting_portal:'Aguardando portal',sending:'Enviando',confirming:'Conferindo envio',confirmed:'Confirmado',unknown:'Resultado incerto — conferir',failed_before_send:'Falha antes de enviar',expired_before_send:'Prazo encerrado antes de enviar',requeued:'Retomada colocada na fila',quota_unknown:'Cota não confirmada',quota_exhausted:'Cota insuficiente'};
const text=v=>typeof v==='string'?v:JSON.stringify(v);
const lines=v=>Array.isArray(v)?v.map(text).join('\n'):text(v||'');
const msg=t=>{el('central-message').textContent=t};
function saveDraft(){
 sessionStorage.setItem('flow-request-draft',JSON.stringify({request:el('central-request').value,acceptance:el('central-acceptance').value,mode:el('central-mode').value,workflow:el('central-recipe').value}));
 el('draft-status').textContent='Rascunho salvo nesta sessão do navegador.';
}
window.addEventListener('flow-view-change',event=>{if(event.detail==='tasks')refreshTitles()});
let draft;try{draft=JSON.parse(sessionStorage.getItem('flow-request-draft')||'null')}catch{}
if(draft){el('central-request').value=draft.request||'';el('central-acceptance').value=draft.acceptance||'';el('central-mode').value=['premium','reserve','auto'].includes(draft.mode)?draft.mode:'auto';el('draft-status').textContent='Rascunho recuperado desta sessão.';}
el('central-submit').disabled=submitted;
['central-request','central-acceptance','central-mode'].forEach(id=>el(id).addEventListener('input',saveDraft));
async function call(path,data){
 const r=await fetch(path,{method:data?'POST':'GET',headers:{'X-Central-Token':token,...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined,cache:'no-store'});
 const value=await r.json();if(!r.ok)throw Error(value.error||'Não foi possível confirmar a operação.');return value;
}
let seen=new Map(),baseline=false;
const titleLoads=new Set();
async function refreshTitles(){
 if(!window.flowTaskTitles||!latest?.management)return;
 const ids=[...latest.management.tasks].sort((a,b)=>b.updated-a.updated).slice(0,document.querySelector('[data-screen=tasks]')?.hidden===false?50:12).map(t=>t.id).filter(id=>!window.flowTaskTitles.has(id)&&!titleLoads.has(id)).slice(0,8);
 await Promise.all(ids.map(async id=>{titleLoads.add(id);try{const d=await call('/api/task?id='+encodeURIComponent(id));window.flowTaskTitles.set(id,d.request||'Tarefa registrada')}catch{}finally{titleLoads.delete(id)}}));
 if(ids.length)renderTasks();
}

async function refreshCentral(){
 if(refreshing)return;refreshing=true;
 try{
  const d=await call('/api/central');token=d.token;
  if(!recipes.length&&d.recipes){recipes=d.recipes;el('central-recipe').innerHTML='<option value="">Pedido livre</option>'+recipes.map(r=>'<option value="'+esc(r.id)+'">'+esc(r.name)+'</option>').join('');if(draft?.workflow)el('central-recipe').value=draft.workflow;}
  const completed=d.entries.filter(r=>baseline&&seen.has(r.id)&&seen.get(r.id)!=='completed'&&r.reason_code==='completed');
  if(completed.length){msg(completed.length+' tarefa(s) concluída(s) pelo executor. Abra os detalhes para conferir evidências.');document.title='✓ Entrega atualizada · Central FLOW';}
  seen=new Map(d.entries.map(r=>[r.id,r.reason_code]));baseline=true;
  el('central-entries').innerHTML=d.entries.length?'<table><thead><tr><th>Pedido</th><th>Responsável atual</th><th>Execução</th><th>Recebimento / orientação</th></tr></thead><tbody>'+d.entries.map(r=>'<tr><td><button type="button" data-task="'+esc(r.id)+'">'+esc(window.flowVisual?.title(window.flowTaskTitles?.get(r.id))||r.id)+'</button><br><code>'+esc(r.id)+'</code></td><td>'+esc(label(r.owner||r.team)||'A definir')+'</td><td>'+esc(r.execution_state||names[r.state]||r.state)+(r.confirmed?'<br>'+r.confirmed+' ações confirmadas':'')+(r.uncertain?'<br>'+r.uncertain+' a conferir':'')+'</td><td>'+esc(names[r.state]||r.state)+'<br>'+esc(r.reason)+'</td></tr>').join('')+'</tbody></table>':'<p class="muted">Seus novos pedidos aparecerão aqui.</p>';
  if(current&&!busy)await openTask(current.task_id,false);
  await refreshTitles();
 }catch(e){msg(e.message)}finally{refreshing=false}
}
function newRequest(){if(busy)return;requestKey=crypto.randomUUID();sessionStorage.setItem('flow-request-key',requestKey);sessionStorage.removeItem('flow-request-submitted');submitted=false;el('central-form').reset();el('central-submit').disabled=false;saveDraft();msg('Novo pedido pronto para preencher.');}
el('central-new').onclick=newRequest;
el('central-recipe').onchange=()=>{
 if(busy)return;
 const recipe=recipes.find(r=>r.id===el('central-recipe').value);if(!recipe){saveDraft();return;}
 if(submitted){newRequest();el('central-recipe').value=recipe.id;}
 else if(el('central-request').value.trim()&&!confirm('Substituir o rascunho pelo procedimento selecionado?')){el('central-recipe').value='';return;}
 el('central-request').value=recipe.request;el('central-acceptance').value=recipe.acceptance;saveDraft();el('central-request').focus();
};
el('central-form').onsubmit=async event=>{
 event.preventDefault();if(busy||submitted)return;
 if(el('central-recipe').value&&/\[[^\]\n]{1,100}\]/.test(el('central-request').value)){msg('Complete os campos entre colchetes antes de enviar.');return;}
 busy=true;el('central-submit').disabled=true;saveDraft();
 try{const d=await call('/api/requests',{key:requestKey,request:el('central-request').value,acceptance:el('central-acceptance').value,mode:el('central-mode').value,workflow:el('central-recipe').value});
 submitted=true;sessionStorage.setItem('flow-request-submitted',requestKey);
 msg('Pedido registrado: '+d.task_id+'. Para outra tarefa, clique em Novo pedido.');await refreshCentral();await openTask(d.task_id);
 }catch(e){msg(e.message);el('central-submit').disabled=false}finally{busy=false}
};
async function openTask(id,scroll=true){
 try{
  const result=await call('/api/task?id='+encodeURIComponent(id));
  if(!scroll&&current?.task_id!==id)return;
  const changed=current?.task_id!==id;
  current=result;const t=current.task||{},e=current.evidence;
  if(changed)el('detail-complement').value=sessionStorage.getItem('flow-complement-'+id)||'';
  el('task-detail').hidden=false;el('task-detail').dataset.task=id;
  el('detail-title').textContent=window.flowVisual?.title(current.request)||'Tarefa '+id;
  if(el('detail-id'))el('detail-id').textContent=id;
  window.flowTaskTitles?.set(id,current.request||'Tarefa registrada');
  el('detail-request').textContent=current.request;
  el('detail-complement').disabled=t.phase==='completed';
  el('task-commands').querySelectorAll('button').forEach(button=>button.disabled=busy||t.phase==='completed');
  el('detail-state').textContent=(t.owner?label(t.owner)+' · ':'')+(t.phase?(names[t.phase]||t.phase):names[current.entry?.state]||'Aguardando encaminhamento')+(t.phase==='completed'&&!e.independently_recorded?' · confira as evidências de conclusão':'');
  el('detail-plan').textContent=lines(t.plan)||'Coordenador ainda não registrou o plano.';
  const roles=Object.entries(t.role_results||{});
  const completedSteps=[lines(t.completed),...roles.map(([role,r])=>r.completed?.length?role+':\n'+lines(r.completed):'')].filter(Boolean);
  el('detail-completed').textContent=completedSteps.join('\n\n')||'Nenhuma etapa relatada como concluída.';
  const roleNext=roles.filter(([,r])=>r.next_step).map(([role,r])=>role+': '+r.next_step);
  el('detail-next').textContent=t.phase==='completed'?'Tarefa concluída. Confira as evidências abaixo; não é necessário repetir a execução.':t.next_step||(roleNext.length?roleNext.join('\n'):current.entry?.reason||'Aguardando atualização');
  el('detail-acceptance').textContent=lines(t.acceptance)||'Será definido pelo coordenador.';
  const blockers=[...(t.blockers||[]),...Object.values(t.job_blockers||{})];
  el('detail-blockers').textContent=blockers.length?lines(blockers):'Nenhum bloqueio informado no checkpoint.';
  const recovery=current.recovery||[];
  el('detail-recovery').textContent=recovery.length?recovery.map(r=>(names[r.code]||r.code)+' · '+(names[r.state]||r.state)+' · '+r.attempts+'/3 retomadas'+(r.state==='waiting'?' · próxima avaliação a partir de '+when(r.next_retry):'')).join('\n'):
   t.automatic_recovery?'Recuperação automática de falhas de cota anteriores à inferência habilitada. Efeitos incertos e pausas humanas exigem conferência.':'Resultados preservados. Retomada depende de conferência; tarefas antigas não são reiniciadas automaticamente.';
  el('detail-progress').textContent=(current.progress||[]).map(p=>p.actor+' · '+p.operation+' · '+(names[p.state]||p.state)+' · '+when(p.updated)).join('\n')||'Nenhuma atividade recente registrada pelas ferramentas.';
  el('detail-outbox').textContent=(current.outbox||[]).map(q=>q.requester_actor+' · '+q.action_id+' · '+(names[q.state]||q.state)+' · tentativas: '+q.attempts+(q.state==='waiting_portal'?' · nova tentativa: '+when(q.next_retry):'')+(q.reason?' · '+q.reason:'')).join('\n')||'Nenhuma operação WhatsApp registrada nesta tarefa.';
  el('detail-evidence').textContent=(e.independently_recorded?'Validação independente registrada por job do validador. ':e.label+' ')+'\n\nRelatos do agente:\n'+JSON.stringify(e.reported,null,2)+'\n\nConfirmações das ferramentas:\n'+JSON.stringify(e.confirmed,null,2)+'\n\nValidação registrada:\n'+JSON.stringify(e.validation,null,2);
  el('detail-jobs').textContent=current.jobs.map(j=>j.role+' · '+(names[j.state]||j.state)+' · '+when(j.updated)).join('\n')||'Nenhuma delegação registrada.';
  el('detail-history').textContent=current.events.map(x=>when(x.created)+' · '+x.kind).join('\n');
  el('detail-commands').textContent=current.commands.map(c=>when(c.created)+' · '+c.kind+' · '+(names[c.state]||c.state)+(c.text?'\n'+c.text:'')+(c.result?'\n'+c.result:'')).join('\n\n')||'Nenhuma solicitação de controle.';
  if(scroll){el('detail-title').focus({preventScroll:true});}
 }catch(e){msg(e.message)}
}
document.addEventListener('click',event=>{const button=event.target.closest('button[data-task]');if(button)openTask(button.dataset.task)});
el('detail-refresh').onclick=()=>current&&openTask(current.task_id,false);
el('detail-close').onclick=()=>{el('task-detail').hidden=true;current=null};
el('detail-complement').addEventListener('input',()=>{if(current)sessionStorage.setItem('flow-complement-'+current.task_id,el('detail-complement').value)});
el('task-commands').addEventListener('click',async event=>{
 const button=event.target.closest('[data-command]');if(!button||!current||busy)return;
 const taskId=current.task_id,revision=current.task.revision??current.entry.revision;
 const kind=button.dataset.command,text=kind==='complement'?el('detail-complement').value:'';
 if(kind==='complement'&&!text.trim()){msg('Escreva o complemento antes de enviar.');return;}
 busy=true;button.disabled=true;
 const fingerprint=taskId+'|'+kind+'|'+text;
 let pending;try{pending=JSON.parse(sessionStorage.getItem('flow-command')||'null')}catch{}
 if(!pending||pending.fingerprint!==fingerprint){pending={fingerprint,key:crypto.randomUUID()};sessionStorage.setItem('flow-command',JSON.stringify(pending));}
 try{const d=await call('/api/commands',{task_id:taskId,key:pending.key,kind,text,revision});
  sessionStorage.removeItem('flow-command');msg(d.message||'Solicitação registrada.');if(kind==='complement'){sessionStorage.removeItem('flow-complement-'+taskId);el('detail-complement').value='';}await openTask(taskId,false);await refreshCentral();
 }catch(e){msg(e.message)}finally{busy=false;button.disabled=false;}
});
refreshCentral();setInterval(refreshCentral,5000);
})();
