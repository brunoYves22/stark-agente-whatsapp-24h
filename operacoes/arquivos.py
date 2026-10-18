"""Arquivos de trabalho e downloads, sem shell ou execução de arquivos."""
import hashlib,ipaddress,json,os,re,shutil,socket,time,urllib.parse,urllib.request
from pathlib import Path

PRIVATE={'.codex','.git','.ssh','.aws','.azure','network','cookies','vendor','backups'}

def resolve_work_path(context,value,write):
    p=Path(value)
    if not p.is_absolute():p=Path(context['project_root'])/p
    p=p.resolve()
    roots=[Path(r).resolve() for r in context['policy'].get('file_roots',[])]
    if not write:roots += [Path(r).resolve() for r in context['policy'].get('read_roots',[])]
    if not any(p.is_relative_to(r) for r in roots):raise ValueError('path_out_of_scope')
    parts={x.casefold() for x in p.parts};name=p.name.casefold()
    if parts & PRIVATE or '.flow-sync' in parts or ('.maestri' in parts and 'outputs' not in parts):
        raise ValueError('private_path_denied')
    if name.startswith('.env') or any(s in name for s in ('auth.json','credential','token','estado.sqlite','operations.sqlite','policy.json','registry.json','consent')):
        raise ValueError('private_file_denied')
    if write and str(p).lower().endswith(('.exe','.dll','.ps1','.vbs','.bat','.cmd','.lnk')):
        raise ValueError('executable_write_requires_explicit_operator')
    return p

def backup_file(context,path):
    if not path.exists():return None
    target=Path(context['runtime'])/'backups'/'operations'/str(time.time_ns())/path.name
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target);return str(target)

def file_operation(context,operation,arguments):
    p=resolve_work_path(context,arguments['path'],operation in ('write','mkdir','move'))
    if operation=='list':return {'entries':[{'name':x.name,'directory':x.is_dir()} for x in list(p.iterdir())[:500] if x.name.casefold() not in PRIVATE and not x.name.startswith('.')]}
    if operation=='read':
        if p.suffix.casefold() in ('.pdf','.docx'):
            if p.stat().st_size>30*1024*1024:raise ValueError('document_too_large: limite 30 MiB')
            if p.suffix.casefold()=='.pdf':
                from pypdf import PdfReader
                document=PdfReader(str(p));pages=len(document.pages)
                if pages>200:raise ValueError('document_too_many_pages')
                text='\n\n'.join('Página '+str(i+1)+'\n'+(page.extract_text() or '') for i,page in enumerate(document.pages))
                note='Texto extraído do PDF. Se o conteúdo for imagem, conferir visualmente; não inferir cláusulas ausentes.'
            else:
                import zipfile
                from xml.etree import ElementTree as ET
                with zipfile.ZipFile(p) as z:
                    info=z.getinfo('word/document.xml')
                    if info.file_size>30*1024*1024:raise ValueError('document_xml_too_large')
                    tree=ET.fromstring(z.read(info));ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                    text='\n'.join(''.join(n.itertext()) for n in tree.findall('.//w:t',ns));pages=None
                note='Texto extraído do DOCX; formatação e imagens exigem conferência visual.'
            return {'path':str(p),'text':text[:1000000],'pages':pages,'truncated':len(text)>1000000,'note':note}
        if p.stat().st_size>500000:raise ValueError('file_too_large: limite textual 500 KB')
        return {'path':str(p),'text':p.read_text(encoding='utf-8-sig')}
    if operation=='mkdir':p.mkdir(parents=True,exist_ok=True);return {'path':str(p)}
    if operation=='write':
        text=arguments['text']
        if not isinstance(text,str) or len(text.encode('utf-8'))>2000000:raise ValueError('invalid_text_size')
        backup=backup_file(context,p);p.parent.mkdir(parents=True,exist_ok=True)
        temp=p.with_name(p.name+'.'+str(time.time_ns())+'.tmp');temp.write_text(text,encoding='utf-8');os.replace(temp,p)
        return {'path':str(p),'backup':backup,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    if operation=='move':
        dest=resolve_work_path(context,arguments['destination'],True)
        if not p.is_file() or dest.exists():raise ValueError('move_file_only_destination_must_not_exist')
        backup=backup_file(context,p);dest.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(p),str(dest))
        return {'path':str(dest),'backup':backup}
    raise ValueError('file_operation_invalid')

def validate_download_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('download_url_invalid')
    for result in socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(result[4][0]).is_global:raise ValueError('download_private_address_denied')
    return url

class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_download_url(newurl);return super().redirect_request(req,fp,code,msg,headers,newurl)

def download_file(context,url,destination):
    validate_download_url(url);p=resolve_work_path(context,destination,True)
    if p.exists():raise ValueError('download_destination_exists')
    p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_name(p.name+'.'+str(time.time_ns())+'.part');size=0;digest=hashlib.sha256()
    try:
        with urllib.request.build_opener(CheckedRedirect()).open(urllib.request.Request(url,headers={'User-Agent':'FLOW-Operations/1.0'}),timeout=30) as response,temp.open('xb') as out:
            while chunk:=response.read(65536):
                size+=len(chunk)
                if size>50*1024*1024:raise ValueError('download_too_large: limite 50 MiB')
                digest.update(chunk);out.write(chunk)
        # Publicação sem substituição de arquivo criado por outro processo.
        with p.open('xb') as out,temp.open('rb') as inp:shutil.copyfileobj(inp,out)
        return {'path':str(p),'bytes':size,'sha256':digest.hexdigest(),'source':urllib.parse.urlunsplit((*urllib.parse.urlsplit(url)[:3],'',''))}
    finally:
        if temp.exists():temp.unlink()
