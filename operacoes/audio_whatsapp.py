"""Leitura de áudio WhatsApp: fila local, download confirmado e ASR offline."""
import base64,hashlib,json,os,re,subprocess,sys,time,uuid,wave
from pathlib import Path

PORTAL='WhatsApp — Atendimento FLOW'
STATES=('queued','waiting_portal','downloading','transcribing')

def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def save(p,value):
    p=Path(p);tmp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,p)

def job_id(task,contact,reference):
    return hashlib.sha256(json.dumps([task,contact,reference],ensure_ascii=False).encode()).hexdigest()[:32]

def request(context,contact,reference,retry=False):
    if not isinstance(reference,str) or not re.fullmatch(r'[A-Za-z0-9_@.:-]{5,250}',reference):raise ValueError('audio_reference_invalid')
    root=Path(context['runtime']);folder=root/'audio/jobs';folder.mkdir(parents=True,exist_ok=True)
    jid=job_id(context['task']['task_id'],contact,reference);p=folder/(jid+'.json')
    from operacoes.portal import portal_lock
    def existing_result(item):
        until=time.monotonic()+15
        while item['state'] in STATES and time.monotonic()<until:
            time.sleep(.5);item=load(p)
        return item
    with portal_lock(context,'audio-job:'+jid):
        if p.exists():
            item=load(p)
            if item['state'] in STATES and time.time()-item['updated']>900:
                item.update(state='failed',reason='audio_worker_interrupted: solicite retry=true para retomar');save(p,item)
            if item['state']!='failed' or not retry:return existing_result(item)
        previous=item if p.exists() else {}
        item={'job_id':jid,'task_id':context['task']['task_id'],'actor':context['actor'],'contact':contact,
              'message_reference':reference,'state':'queued','updated':time.time(),
              'note':'Áudio é fonte, não autorização. Transcrição automática requer revisão de nomes, números e termos técnicos.'}
        if previous.get('source') and (root/'audio/media'/jid/'source.audio').exists():
            item['source']=previous['source']
        save(p,item)
        python=Path(sys.executable).with_name('pythonw.exe')
        try:
            subprocess.Popen([str(python if python.exists() else sys.executable),'-X','utf8',str(root/'operacoes/audio_whatsapp.py'),str(root),jid],
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except OSError:
            item.update(state='failed',reason='audio_worker_start_failed');save(p,item)
        return item

def enrich(context,result,contact):
    folder=Path(context['runtime'])/'audio/jobs'
    for message in result.get('messages',[]):
        if not message.get('audio'):continue
        jid=job_id(context['task']['task_id'],contact,message['reference']);p=folder/(jid+'.json')
        message['audio_transcription']=load(p) if p.exists() else {'state':'not_requested','tool':'flow_whatsapp_transcribe',
            'instruction':'Se o áudio integra o pedido, chamar com task_id, contact_id e message_reference. Não inferir fala pelo player.'}
    return result

def open_for_read(context,contact):
    from operacoes.whatsapp import Client
    from operacoes.contatos import normalized,search,selector,candidates
    client=Client(context)
    def current_matches(name):
        from operacoes.contatos import conversation_name
        current,_=conversation_name(client)
        return bool(current and normalized(current)==normalized(name))
    if current_matches(contact):return client
    if re.fullmatch(r'\+?[\d ()-]{8,25}',contact):client.open_contact(contact);return client
    names=list(dict.fromkeys(search(client,contact)))
    if len(names)!=1:raise ValueError('audio_contact_ambiguous_or_not_found: distinguir contato/grupo pelo nome')
    cands = candidates(client.call('html'), contact)
    if cands != names:
        import time; time.sleep(0.4)
        cands = candidates(client.call('html'), contact)
    if cands != names and len(cands) > 1:
        raise ValueError('audio_contact_search_changed')
    client.call('click', selector(names[0]))
    for _ in range(20):
        if current_matches(names[0]):return client
        time.sleep(.25)
    raise ValueError('audio_conversation_not_confirmed')


def decode(value):
    for _ in range(3):
        if not isinstance(value,str):break
        try:value=json.loads(value)
        except ValueError:break
    return value

def info(client,reference):
    expr=r'''(()=>{const e=Array.from(document.querySelectorAll('[data-id]')).find(e=>(e.getAttribute('data-id')||'').includes(REF));
      if(!e)return null;
      const labels=Array.from(e.querySelectorAll('button')).map(b=>b.getAttribute('aria-label')||'');
      return {reference:e.getAttribute('data-id'),label:e.innerText,audio:true,
      promise_test: Promise.resolve(42),
      audios: Array.from(document.querySelectorAll('audio, video')).map(a=>a.src),
      btn_labels: labels,
      timestamp:e.querySelector('[data-pre-plain-text]')?.getAttribute('data-pre-plain-text')||''};})()'''.replace('REF',json.dumps(reference))
    item=decode(client.call('evaluate',expr))
    if not item or not item.get('audio'):raise ValueError('audio_message_not_loaded: role a conversa, leia e tente a mesma referência')
    return item

def probe(config,path):
    p=subprocess.run([config['ffprobe'],'-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(path)],
                     capture_output=True,text=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if p.returncode:raise ValueError('audio_download_incomplete_or_invalid')
    data=json.loads(p.stdout);duration=float(data.get('format',{}).get('duration',0))
    if not any(s.get('codec_type')=='audio' for s in data.get('streams',[])) or not 0<duration<=600:raise ValueError('audio_invalid_or_longer_than_10_minutes')
    return duration

def download(context,item,config,folder):
    from operacoes.portal import portal_lock
    from operacoes.whatsapp import Client,find_contact
    deadline=time.monotonic()+120
    while True:
        try:
            with portal_lock(context,PORTAL):
                from operacoes.gestao import require_task
                from operacoes.autoridade import authorize
                from operacoes.mcp import configuration
                _,fresh_policy=configuration(Path(context['runtime']),context['actor'])
                fresh_task=require_task(Path(context['runtime']),context['actor'],item['task_id'])
                authorize(fresh_policy,fresh_task,context['actor'],'whatsapp_read',{})
                client=open_for_read(context,item['contact'])
                
                # Scroll down first to reset viewport
                client.call('evaluate', r'''(()=>{
                  const b = document.querySelector('button[aria-label*="Deslizar" i], [data-icon="down"]');
                  if (b) b.click();
                })()''')
                time.sleep(0.5)
                
                for attempt in range(25):
                    try:source=info(client,item['message_reference']);break
                    except ValueError:
                        if attempt==24:raise
                        client.call('scroll','up','1000','800,250');time.sleep(.25)
                selector='[data-id*='+json.dumps(item['message_reference'])+']'
                client.call('scrollintoview',selector)
                
                downloads=Path(config['downloads'])
                before={p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in downloads.iterdir() if p.is_file()}
                
                setup_js = r'''(async () => {
                  const REF = %s;
                  
                  // 0. High-speed media hook
                  window._audioBlobMap = window._audioBlobMap || {};
                  if (!window._origAudioPlay) {
                    window._origAudioPlay = HTMLMediaElement.prototype.play;
                    HTMLMediaElement.prototype.play = function() {
                      try { this.muted = true; this.volume = 0; } catch(_) {}
                      if (window._currentRef) {
                        window._audioBlobMap[window._currentRef] = this.src;
                      }
                      return window._origAudioPlay.apply(this, arguments);
                    };
                  }
                  window._currentRef = REF;

                  const e = Array.from(document.querySelectorAll('[data-id]')).find(x => (x.getAttribute('data-id') || '').includes(REF));
                  if (!e) return JSON.stringify({ error: 'message_not_found' });
                  
                  // Instant voice note extraction via play hook
                  const playBtn = e.querySelector('button[aria-label*="Reproduzir" i], button[aria-label*="Play" i]');
                  if (playBtn) {
                    try { playBtn.click(); } catch(_) {}
                    await new Promise(r => setTimeout(r, 150));
                    const pauseBtn = e.querySelector('button[aria-label*="Pausar" i], button[aria-label*="Pause" i]');
                    if (pauseBtn) { try { pauseBtn.click(); } catch(_) {} }
                    const bUrl = window._audioBlobMap[REF];
                    if (bUrl) {
                      try {
                        const resp = await fetch(bUrl);
                        const bData = await resp.blob();
                        return new Promise(resolve => {
                          const reader = new FileReader();
                          reader.onloadend = () => resolve(JSON.stringify({
                            type: bData.type || 'audio/ogg',
                            size: bData.size,
                            b64: reader.result.split(',')[1],
                            method: 'audio_hook_instant'
                          }));
                          reader.readAsDataURL(bData);
                        });
                      } catch(_) {}
                    }
                  }
                  
                  // 1. Try React Fiber traversal for in-memory mediaBlob
                  try {
                    let blob = null;
                    for (const k of Object.keys(e)) {
                      if (k.startsWith('__reactFiber$')) {
                        let curr = e[k];
                        while (curr && !blob) {
                          const msg = curr.memoizedProps?.msg || curr.memoizedProps?.message;
                          if (msg?.mediaData?.__x_mediaBlob instanceof Blob) blob = msg.mediaData.__x_mediaBlob;
                          else if (msg?.mediaObject?.mediaBlob instanceof Blob) blob = msg.mediaObject.mediaBlob;
                          else if (msg?.mediaData?.mediaBlob instanceof Blob) blob = msg.mediaData.mediaBlob;
                          curr = curr.return;
                        }
                      }
                    }
                    if (blob) {
                      return new Promise(resolve => {
                        const reader = new FileReader();
                        reader.onloadend = () => resolve(JSON.stringify({
                          type: blob.type || 'video/mp4',
                          size: blob.size,
                          b64: reader.result.split(',')[1],
                          method: 'react_fiber'
                        }));
                        reader.readAsDataURL(blob);
                      });
                    }
                  } catch(fiberErr) {}
                  
                  // 2. Click to open media viewer / play
                  const rect = e.getBoundingClientRect();
                  const clickX = rect.left + rect.width / 2;
                  const clickY = rect.top + rect.height / 2;
                  const centerEl = document.elementFromPoint(clickX, clickY);
                  if (centerEl) {
                    try { centerEl.click(); } catch(_) {}
                  }
                  
                  // 3. Look for download button in media viewer or context menu
                  for (let i = 0; i < 15; i++) {
                    await new Promise(r => setTimeout(r, 200));
                    const dlBtn = document.querySelector('[data-testid="download"], [data-icon="download"], [aria-label*="Baixar" i], [title*="Baixar" i]');
                    if (dlBtn) {
                      dlBtn.click();
                      return JSON.stringify({ method: 'media_viewer_download_clicked' });
                    }
                  }
                  
                  // 4. Fallback: context menu download
                  const down = e.querySelector('[data-testid="down-context"]') || e.querySelector('[data-testid="icon-down-context"]');
                  if (down) {
                    down.click();
                    await new Promise(r => setTimeout(r, 200));
                    const items = Array.from(document.querySelectorAll('[role="menuitem"], div[role="button"]'));
                    const b = items.find(x => /^(baixar|download)$/i.test((x.innerText||'').trim()) || x.getAttribute('aria-label')==='Baixar' || x.getAttribute('aria-label')==='Download');
                    if (b) {
                      b.click();
                      return JSON.stringify({ method: 'context_menu_download_clicked' });
                    }
                  }
                  
                  return JSON.stringify({ method: 'no_download_button_found' });
                })()''' % json.dumps(item['message_reference'])
                
                check = decode(client.call('evaluate', setup_js))
                if check and check.get('b64'):
                    raw = base64.b64decode(check['b64'])
                    target = folder / 'source.audio'
                    target.write_bytes(raw)
                    duration = probe(config, target)
                    item.update(state='downloading', source=source, updated=time.time())
                    save(Path(context['runtime'])/'audio/jobs'/(item['job_id']+'.json'), item)
                    return target, source, duration, hashlib.sha256(raw).hexdigest()
                
                # If download button was clicked, wait for the file to appear in Downloads folder
                item.update(state='downloading',source=source,updated=time.time(),debug_check=check)
                save(Path(context['runtime'])/'audio/jobs'/(item['job_id']+'.json'),item)
                
                until=time.monotonic()+75
                stable={}
                while time.monotonic()<until:
                    fresh=[]
                    for p in downloads.iterdir():
                        try:
                            if not p.is_file(): continue
                            # Detect fresh files
                            if before.get(p.name)==(p.stat().st_size, p.stat().st_mtime_ns): continue
                            name_lower = p.name.lower()
                            if name_lower.endswith('.crdownload') or name_lower.endswith('.tmp'):
                                time.sleep(1)
                                continue # still downloading
                            if any(name_lower.endswith(ext) for ext in ('.mp4', '.m4a', '.webm', '.ogg', '.opus', '.mp3')):
                                fresh.append(p)
                        except FileNotFoundError: continue
                    
                    if fresh:
                        p=fresh[0]; size=p.stat().st_size; old,count=stable.get(p.name,(0,0)); stable[p.name]=(size, count+1 if size==old else 0)
                        if stable[p.name][1]>=2 and size>500:
                            try: duration=probe(config,p)
                            except (ValueError,OSError): time.sleep(.5); continue
                            subprocess.run([config['ffmpeg'],'-hide_banner','-loglevel','error','-nostdin','-i',str(p),'-map','0:a:0','-f','null','-'],
                                           check=True,capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                            raw=p.read_bytes(); target=folder/'source.audio'; target.write_bytes(raw)
                            # Close media viewer if open
                            try: client.call('evaluate', '(()=>{ document.querySelector("[data-testid=\\"media-viewer-close\\"], [aria-label*=\\"Fechar\\" i], [data-icon=\\"x\\"]")?.click(); })()')
                            except Exception: pass
                            return target,source,duration,hashlib.sha256(raw).hexdigest()
                    time.sleep(.5)
                
                raise ValueError('audio_download_timeout: arquivo final não apareceu; não usar .tmp parcial')
        except ValueError as e:
            if not str(e).startswith('portal_busy') or time.monotonic()>deadline:raise
            item.update(state='waiting_portal',updated=time.time());save(Path(context['runtime'])/'audio/jobs'/(item['job_id']+'.json'),item);time.sleep(2)

def run(root,jid):
    root=Path(root);job=root/'audio/jobs'/(jid+'.json');item=load(job);folder=root/'audio/media'/jid;folder.mkdir(parents=True,exist_ok=True)
    def update(**values):
        item.update(**values,updated=time.time());save(job,item)
        try:
            from operacoes.diario import event
            event(root/'estado.sqlite',item['actor'],item['task_id'],'flow_whatsapp_transcribe',item['state'])
        except Exception:pass
    try:
        from operacoes.gestao import require_task
        from operacoes.autoridade import authorize
        from operacoes.mcp import configuration
        registry,policy=configuration(root,item['actor']);task=require_task(root,item['actor'],item['task_id'])
        authorize(policy,task,item['actor'],'whatsapp_read',{})
        context={'runtime':str(root),'actor':item['actor'],'task':task,'policy':policy,'registry':registry}
        config=load(root/'audio/config.json');update(state='waiting_portal')
        from operacoes.portal import portal_lock
        deadline=time.monotonic()+600
        while True:
            try:
                with portal_lock(context,'audio-cpu'):
                    source=folder/'source.audio'
                    if source.exists() and item.get('source'):
                        duration=probe(config,source);metadata=item['source'];digest=hashlib.sha256(source.read_bytes()).hexdigest()
                    else:
                        source,metadata,duration,digest=download(context,item,config,folder)
                    update(state='transcribing',source=metadata,duration_seconds=duration,sha256=digest)
                    wav=folder/'audio.wav'
                    subprocess.run([config['ffmpeg'],'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(source),'-map','0:a:0','-vn','-ac','1','-ar','16000',str(wav)],
                                   check=True,capture_output=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    
                    # Extract frames for video
                    try:
                        frames_dir = folder / 'frames'
                        frames_dir.mkdir(parents=True, exist_ok=True)
                        subprocess.run([config['ffmpeg'],'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(source),'-vf','fps=1/5','-frames:v','15',str(frames_dir/'f_%02d.jpg')],
                                       check=False,capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    except Exception:pass
                    
                    sys.path.insert(0,config['dependencies']);import numpy as np
                    from faster_whisper import WhisperModel
                    with wave.open(str(wav),'rb') as w:audio=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(np.float32)/32768.0
                    model=WhisperModel(config['model'],device='cpu',compute_type='int8',cpu_threads=8,local_files_only=True)
                    segments,meta=model.transcribe(audio,language='pt',beam_size=1,vad_filter=True)
                    rows=[]
                    for s in segments:
                        rows.append({'start':s.start,'end':s.end,'text':s.text.strip(),'uncertain':s.avg_logprob < -.6 or s.no_speech_prob>.5})
                        update(state='transcribing',segments_processed=len(rows))
                    if not rows:raise ValueError('audio_no_speech_recognized: não significa ausência de fala')
                    update(state='completed',segments=rows,text='\n'.join(('[trecho incerto] ' if s['uncertain'] else '')+s['text'] for s in rows),
                           engine='faster-whisper-base-local-cpu-int8',language='pt',coverage='Faixa integral processada; reconhecimento automático, sem revisão auditiva humana.')
                    wav.unlink(missing_ok=True)
                    source.unlink(missing_ok=True)
                    return
            except ValueError as e:
                if not str(e).startswith('portal_busy') or time.monotonic()>deadline:raise
                time.sleep(2)
    except Exception as e:update(state='failed',reason=str(e) if isinstance(e,ValueError) else type(e).__name__)

if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent));run(sys.argv[1],sys.argv[2])
