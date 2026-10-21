let v2UploadAbort=false,v2LastFileJob='';
let v2Ready=false,v2Leaving=false,v2Socket=null,v2OwnedRun='',v2FileBusy=false,v2Transfer='',v2Clipboard='',v2Selected=null,v2Path='/',v2FolderOffset=0;
const v2Client=Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');
const v2Style=document.createElement('style');v2Style.textContent='[hidden]{display:none!important}.v2nav{max-width:1400px;margin:16px auto 0;display:flex;gap:10px;padding:0 32px;flex-wrap:wrap}.v2nav button[aria-pressed=true]{background:#245dd6;color:white}.v2panel{max-width:1050px;margin:auto}.v2grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.v2panel label{display:block;margin:14px 0 7px}.v2panel input:not([type=checkbox]){box-sizing:border-box;width:100%;padding:11px;border:1px solid #95a4b5;border-radius:5px;font:inherit}.v2toolbar{display:flex;gap:9px;flex-wrap:wrap;margin:16px 0}.v2panel table{width:100%;border-collapse:collapse}.v2panel td,.v2panel th{padding:12px 8px;text-align:left;border-bottom:1px solid #e1e7ed;overflow-wrap:anywhere}.v2panel tr.selected{background:#eaf2ff}.v2panel tr{cursor:pointer}.v2notice{padding:12px;background:#eff5ff;color:#264873;border-radius:6px;margin:12px 0;white-space:pre-wrap}.v2panel dialog,dialog.v2dialog{border:1px solid #cbd5e1;border-radius:12px;padding:26px;max-width:440px;color:#17243b}dialog::backdrop{background:#17243b66}.v2panel .v2result{white-space:pre-wrap;font-size:13px;line-height:1.7}.v2panel a{color:#194fb9}@media(max-width:760px){.v2grid{grid-template-columns:1fr}.v2nav{padding:0 16px}}';
document.head.append(v2Style);
const v2Main=document.querySelector('main'),v2Monitor=document.createElement('div');v2Monitor.id='monitorPanel';v2Monitor.append(...v2Main.childNodes);v2Main.append(v2Monitor);
const v2RecordFile=document.createElement('p');v2RecordFile.id='recordFile';v2RecordFile.style.overflowWrap='anywhere';v2Monitor.querySelector('.aside-note').append(v2RecordFile);
const v2Nav=document.createElement('nav');v2Nav.className='v2nav';v2Nav.setAttribute('aria-label','主控功能');
v2Nav.innerHTML='<button data-panel="monitor" aria-pressed="true">检测页面</button><button data-panel="network" aria-pressed="false">主控网络设置</button><button data-panel="files" aria-pressed="false">TF 文件</button>';
v2Main.before(v2Nav);
const v2Network=document.createElement('section');v2Network.id='networkPanel';v2Network.className='v2panel';v2Network.hidden=true;
v2Network.innerHTML='<h1>主控网络设置</h1><p>通过 USB、已有 Wi-Fi 或主控热点配置；采集和文件任务期间不能修改网络。</p><div class="v2notice" id="networkMessage" role="status">正在读取设备网络状态</div><div class="v2grid"><section class="card"><h2>连接 Wi-Fi</h2><label><input type="checkbox" id="staEnabled"> 启用 Wi-Fi 连接</label><label for="wifiSsid">Wi-Fi 名称（2.4 GHz）</label><input id="wifiSsid" maxlength="32"><div class="v2toolbar"><button id="scanWifi">扫描附近网络</button></div><select id="wifiChoices" aria-label="扫描到的网络"><option>扫描后选择网络</option></select><label for="wifiPassword">Wi-Fi 密码</label><input id="wifiPassword" type="password" maxlength="63" autocomplete="new-password"><p class="meta">原网络不变时，密码留空表示保留；不会回显已保存的 Wi-Fi 密码。</p></section><section class="card"><h2>主控自身热点</h2><label><input type="checkbox" id="apEnabled"> 开启设备热点</label><label for="apSsid">热点名称</label><input id="apSsid" maxlength="32"><label for="apPassword">热点密码</label><input id="apPassword" type="password" minlength="8" maxlength="63"><label><input type="checkbox" id="showApPassword"> 显示热点密码</label><p class="meta">连接这个热点的设备可访问检测页；热点不转发互联网。</p></section></div><div class="v2toolbar"><button id="saveNetwork" class="primary">保存并应用到主控</button><button id="reloadNetwork">重新读取</button></div><section class="card"><h2>检测页面网址</h2><div id="deviceUrls" class="v2result"></div><p class="meta">USB 本机网址仅这台电脑可用；其他设备请使用 Wi-Fi 或热点网址。配置变化可能导致当前页面断开，USB 可用于恢复。</p></section>';
v2Main.append(v2Network);
const v2Files=document.createElement('section');v2Files.id='filesPanel';v2Files.className='v2panel';v2Files.hidden=true;
v2Files.innerHTML='<h1>TF 文件</h1><p>文件操作只在采集、保存和交接结束后执行；永久删除无法通过本软件恢复。</p><div id="fileMessage" class="v2notice" role="status">点击刷新读取 TF 目录</div><section class="card"><label for="filePath">当前目录</label><input id="filePath" value="/"><div class="v2toolbar"><button id="refreshFiles">刷新</button><button id="parentFolder">上一级</button><button id="newFolder">新建文件夹</button><button id="uploadFiles">上传文件</button><button id="uploadFolder">上传文件夹</button><input id="uploadPicker" type="file" multiple hidden><input id="folderPicker" type="file" webkitdirectory multiple hidden></div><div class="v2toolbar"><button id="renameFile">重命名</button><button id="copyFile">复制</button><button id="pasteFile">粘贴到当前目录</button><button id="downloadFile">下载文件</button><button id="deleteFile">永久删除</button><button id="cancelFile">取消文件任务</button></div><div style="overflow-x:auto"><table aria-label="TF目录"><thead><tr><th>名称（双击进入文件夹）</th><th>类型</th><th>大小</th></tr></thead><tbody id="fileRows"></tbody></table></div><button id="nextFiles" hidden>下一页</button><p class="meta">可在本页粘贴浏览器提供的电脑文件；也可用上传按钮选择。复制按钮用于 TF 卡内复制，不会剪切源文件。</p></section>';
v2Main.append(v2Files);
const v2Dialog=document.createElement('dialog');v2Dialog.className='v2dialog';v2Dialog.innerHTML='<h2>已有页面正在采集</h2><p id="takeoverText" style="line-height:1.8"></p><div class="v2toolbar"><button id="takeoverContinue" class="primary">继续</button><button id="takeoverCancel">取消</button></div>';document.body.append(v2Dialog);
const v2Id=id=>document.getElementById(id);
function v2Message(id,message){v2Id(id).textContent=message;}
function v2ChoosePanel(panel){for(const p of ['monitor','network','files'])v2Id(p+'Panel').hidden=p!==panel;v2Nav.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.panel===panel)));if(panel==='network'&&v2Ready)v2ReadNetwork();if(panel==='files'&&v2Ready)v2RefreshFiles();}
v2Nav.querySelectorAll('button').forEach(b=>b.onclick=()=>v2ChoosePanel(b.dataset.panel));
async function v2Api(path,body){const post=body!==undefined,r=await fetch('/api/v2/'+path,{method:post?'POST':'GET',headers:post?{'Content-Type':'application/json','X-Record-Key':sessionKey}:{},body:post?JSON.stringify({...body,boot:sessionKey}):undefined,cache:'no-store',signal:AbortSignal.timeout(8000)});let data;try{data=await r.json();}catch{throw Error('设备未返回有效响应');}if(!r.ok)throw Error(data.error||'设备拒绝请求（HTTP '+r.status+'）');return data;}
function v2ConnectLive(){
  if(v2Leaving)return;
  v2Socket=new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/api/v2/live?client='+v2Client+'&boot='+sessionKey);
  v2Socket.onopen=()=>{v2Ready=true;buttons();const panel=v2Nav.querySelector('[aria-pressed=true]').dataset.panel;if(panel==='network')v2ReadNetwork();if(panel==='files')v2RefreshFiles();};
  v2Socket.onclose=()=>{v2Ready=false;buttons();if(!v2Leaving)setTimeout(v2ConnectLive,1500);};
  v2Socket.onerror=()=>{v2Ready=false;buttons();};
}
async function v2Bootstrap(){
  try{const c=await v2Api('capabilities');if(c.version!==2)throw Error('固件不支持新版控制');await v2Api('session/open',{client:v2Client});v2ConnectLive();}
  catch(e){text('message',e.message+'；新版控制需要升级主控固件。');}
  const panel=new URLSearchParams(location.search).get('panel');if(['network','files'].includes(panel))v2ChoosePanel(panel);poll();
}
function v2Render(s){
  v2RecordFile.textContent=s.record_path?'信号文件：'+s.record_path:'新记录按开始时电脑或手机的本地时间命名；请先确认控制端时间正确。';
  if(v2OwnedRun&&s.owner&&s.owner!==v2Client&&s.run!==v2OwnedRun){text('message','本页采集已被其他页面接管，当前仅查看新的采集。');v2OwnedRun='';}
  if(s.pending_owner&&s.owner===v2Client)text('message','其他页面请求接管，正在停止、保存和校验本页记录。');
  v2Files.querySelectorAll('button').forEach(b=>{if(b.id!=='cancelFile')b.disabled=!v2Ready||active.has(s.phase)||v2FileBusy;});
  if(v2Id('networkPanel').hidden===false){v2Id('saveNetwork').disabled=!v2Ready||active.has(s.phase)||s.files_busy;v2Id('scanWifi').disabled=v2Id('saveNetwork').disabled;}
}
function v2ConfirmTakeover(s,seconds){return new Promise(resolve=>{
  v2Id('takeoverText').textContent='页面 '+(s.owner||'未知').slice(0,6)+' 正在采集。继续将停止并保存当前记录，再按本页设置开始一份新的 '+seconds+' 秒记录。取消则不改变当前采集。';
  let finished=false;const finish=value=>{if(finished)return;finished=true;v2Dialog.close();resolve(value);};
  v2Id('takeoverContinue').onclick=()=>finish(true);v2Id('takeoverCancel').onclick=()=>finish(false);
  v2Dialog.oncancel=e=>{e.preventDefault();finish(false);};v2Dialog.showModal();
});}
async function v2Start(){
  if($('start').disabled)return;const seconds=Number($('seconds').value);
  if(!Number.isInteger(seconds)||seconds<1||seconds>300){text('message','请输入1–300的整数秒数');return;}
  const observed={...snapshot},busy=active.has(observed.phase);
  pending=true;buttons();
  try{
    if(busy&&!await v2ConfirmTakeover(observed,seconds)){text('message','已取消；原页面继续采集。');return;}
    const run=crypto.getRandomValues(new Uint32Array(1))[0].toString(16).toUpperCase().padStart(8,'0');
    await v2Api('session/'+(busy?'takeover':'start'),{client:v2Client,run,seconds,local_time:v2LocalTime(new Date()),card_serial:observed.card_serial,card_bytes:observed.card_bytes,...(busy?{expected_run:observed.run}:{})});
    v2OwnedRun=run;text('message',busy?'已预约接管：先保存并校验原记录，再开始本页采集。':'开始请求已接收，以设备状态为准。');
  }catch(e){text('message',e.message+'；不会自动重发，请查看当前状态。');}finally{pending=false;buttons();}
}
function v2LocalTime(date){
  const year=date.getFullYear();if(!Number.isFinite(date.getTime())||year<2000||year>2099)throw Error('电脑或手机日期需在2000–2099年之间，请校准后再开始');
  const two=n=>String(n).padStart(2,'0');
  return two(year%100)+'-'+two(date.getMonth()+1)+'-'+two(date.getDate())+'-'+two(date.getHours())+'.'+two(date.getMinutes())+'.'+two(date.getSeconds());
}
async function v2Stop(){try{await v2Api('session/stop',{client:v2Client});text('message','已请求安全停止，请等待文件保存和校验完成。');}catch(e){text('message',e.message);}}
window.addEventListener('pagehide',()=>{v2Leaving=true;v2Ready=false;navigator.sendBeacon('/api/v2/session/release',JSON.stringify({boot:sessionKey,client:v2Client}));if(v2Socket)v2Socket.close();if(v2Transfer)navigator.sendBeacon('/api/v2/files/cancel',JSON.stringify({boot:sessionKey,job:v2Uuid(),expected_job:v2LastFileJob,transfer:v2Transfer}));});
function v2Uuid(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');}
async function v2ReadNetwork(){
  try{const n=await v2Api('network/state');v2Id('staEnabled').checked=n.sta_enabled;v2Id('apEnabled').checked=n.ap_enabled;v2Id('wifiSsid').value=n.ssid;v2Id('wifiPassword').value='';v2Id('apSsid').value=n.ap_ssid;v2Id('apPassword').value=n.ap_password;v2Message('networkMessage',n.apply_ok===false?'网络启动失败，请通过 USB 检查或重新保存配置':n.applying?'配置正在应用，可能短暂断开':n.connected?'主控 Wi-Fi 已连接':'主控 Wi-Fi 尚未连接；USB仍可配置');v2Message('deviceUrls',(location.hostname==='127.0.0.1'?'本机 USB / 转接入口：'+location.origin+'/\n':'')+(n.sta_ip?'Wi-Fi 检测页：http://'+n.sta_ip+'/\n':'')+(n.ap_enabled?'热点检测页：http://'+n.ap_ip+'/\n':'')+'可选中并复制网址。');}
  catch(e){v2Message('networkMessage',e.message);}
}
v2Id('showApPassword').onchange=()=>v2Id('apPassword').type=v2Id('showApPassword').checked?'text':'password';
v2Id('reloadNetwork').onclick=v2ReadNetwork;
v2Id('scanWifi').onclick=async()=>{try{v2Message('networkMessage','正在扫描附近的 2.4 GHz 网络…');const list=await v2Api('network/scan',{});v2Id('wifiChoices').replaceChildren(...list.map(p=>{const o=document.createElement('option');o.value=p.ssid;o.textContent=p.ssid+'  '+p.rssi+' dBm';return o;}));v2Message('networkMessage','扫描完成，请选择网络并输入密码。');}catch(e){v2Message('networkMessage',e.message);}};
v2Id('wifiChoices').onchange=()=>v2Id('wifiSsid').value=v2Id('wifiChoices').value;
v2Id('saveNetwork').onclick=async()=>{try{await v2Api('network/save',{sta_enabled:v2Id('staEnabled').checked,ap_enabled:v2Id('apEnabled').checked,ssid:v2Id('wifiSsid').value,password:v2Id('wifiPassword').value,ap_ssid:v2Id('apSsid').value,ap_password:v2Id('apPassword').value});v2Message('networkMessage','已保存，正在应用。若断线，请使用 USB 或重新连接热点后打开页面。');}catch(e){v2Message('networkMessage',e.message);}};
async function v2FileOp(action,args={}){
  const job=v2Uuid();v2LastFileJob=job;await v2Api('files/'+action,{...args,job});
  const start=Date.now();
  const blockTransfer=action==='chunk'||action==='read';
  if(!blockTransfer)await new Promise(r=>setTimeout(r,180));
  for(;;){const s=await v2Api('files/state');if(s.job!==job)throw Error('文件任务状态已变化，结果未确认，请刷新，勿重复删除或重命名');v2Message('fileMessage',(s.running?'正在处理，':'处理完成，')+'已处理 '+Number(s.bytes||0).toLocaleString()+' 字节');if(!s.running){if(s.code>=400)throw Error(s.result.error||'文件任务失败');return s.result;}if(Date.now()-start>1800000)throw Error('文件任务仍未完成，请检查设备状态');await new Promise(r=>setTimeout(r,blockTransfer?20:180));}
}
async function v2FileAction(fn){if(v2FileBusy)return;v2FileBusy=true;if(snapshot)v2Render(snapshot);try{await fn();}catch(e){v2Message('fileMessage',e.message);}finally{v2FileBusy=false;if(snapshot)v2Render(snapshot);}}
function v2Join(parent,name){return(parent==='/'?'':parent)+'/'+name;}
async function v2RefreshFiles(append=false){
  if(!v2Ready||v2FileBusy)return;
  await v2FileAction(async()=>{
    v2Path=v2Id('filePath').value||'/';if(!append){v2FolderOffset=0;v2Id('fileRows').replaceChildren();v2Selected=null;}
    const list=await v2FileOp('list',{path:v2Path,offset:v2FolderOffset});
    for(const e of list.entries){const row=document.createElement('tr');row.tabIndex=0;const name=document.createElement('td'),type=document.createElement('td'),size=document.createElement('td');name.textContent=e.name;type.textContent=e.directory?'文件夹':'文件';size.textContent=e.directory?'—':Number(e.bytes).toLocaleString()+' B';row.append(name,type,size);
      row.onclick=()=>{v2Id('fileRows').querySelectorAll('tr').forEach(r=>r.classList.remove('selected'));row.classList.add('selected');v2Selected={...e,path:v2Join(v2Path,e.name)};};
      row.ondblclick=()=>{if(e.directory){v2Id('filePath').value=v2Join(v2Path,e.name);v2RefreshFiles();}};
      row.onkeydown=event=>{if(event.key==='Enter'){row.onclick();if(e.directory)row.ondblclick();}};v2Id('fileRows').append(row);}
    v2FolderOffset=list.next;v2Id('nextFiles').hidden=!list.more;v2Message('fileMessage','目录已读取。选中文件后操作；双击文件夹进入。');
  });
}
v2Id('refreshFiles').onclick=()=>v2RefreshFiles();v2Id('nextFiles').onclick=()=>v2RefreshFiles(true);
v2Id('parentFolder').onclick=()=>{v2Id('filePath').value=v2Path.slice(0,v2Path.lastIndexOf('/'))||'/';v2RefreshFiles();};
v2Id('newFolder').onclick=()=>{const name=prompt('新文件夹名称');if(name)v2FileAction(async()=>{await v2FileOp('mkdir',{path:v2Join(v2Path,name)});v2Message('fileMessage','文件夹已创建，请刷新。');});};
v2Id('copyFile').onclick=()=>{if(v2Selected){v2Clipboard=v2Selected.path;v2Message('fileMessage','已复制 '+v2Selected.name+'；切换目标目录后粘贴。');}};
v2Id('pasteFile').onclick=()=>{if(!v2Clipboard)return;const base=v2Clipboard.slice(v2Clipboard.lastIndexOf('/')+1),name=prompt('副本名称',v2Join(v2Path,base)===v2Clipboard?base+' - 副本':base);if(name)v2FileAction(async()=>{await v2FileOp('copy',{path:v2Clipboard,destination:v2Join(v2Path,name)});v2Message('fileMessage','副本已完整校验并保存，请刷新。');});};
v2Id('renameFile').onclick=()=>{if(!v2Selected)return;const entry=v2Selected,name=prompt('新的名称',entry.name);if(name&&name!==entry.name)v2FileAction(async()=>{await v2FileOp('rename',{path:entry.path,destination:v2Join(v2Path,name)});v2Message('fileMessage','已重命名，请刷新。');});};
v2Id('deleteFile').onclick=()=>{if(!v2Selected)return;const entry=v2Selected;v2FileAction(async()=>{const m=await v2FileOp('measure',{path:entry.path});if(!confirm('永久删除 '+entry.path+' ？\n共 '+m.files+' 个文件，'+m.bytes+' 字节。\n无法通过本软件恢复。')){v2Message('fileMessage','已取消删除，文件保持原样。');return;}await v2FileOp('delete',{path:entry.path,confirmed:true,revision:m.revision});v2Message('fileMessage','永久删除已完成，请刷新。');});};
v2Id('downloadFile').onclick=()=>{if(!v2Selected||v2Selected.directory){v2Message('fileMessage','请选中一个文件下载。');return;}const a=document.createElement('a');a.href='/api/v2/download?path='+encodeURIComponent(v2Selected.path);a.download=v2Selected.name;document.body.append(a);a.click();a.remove();v2Message('fileMessage','已请求浏览器下载；请以浏览器下载结果为准。');};
async function v2Upload(files){
  v2UploadAbort=false;
  await v2FileAction(async()=>{
    const folders=new Set();
    for(const file of files){
      if(v2UploadAbort)throw Error('上传已取消，未完成临时文件保留');
      const relative=file.webkitRelativePath||file.name,parts=relative.split('/');let folder=v2Path;
      for(const part of parts.slice(0,-1)){folder=v2Join(folder,part);if(!folders.has(folder)){await v2FileOp('mkdir',{path:folder});folders.add(folder);}}
      const begin=await v2FileOp('begin_upload',{path:v2Join(folder,parts[parts.length-1]),bytes:file.size});v2Transfer=begin.transfer;const hash=new IncrementalSHA256();
      for(let offset=0;offset<file.size;offset+=2048){if(v2UploadAbort)throw Error('上传已取消，未完成临时文件保留');const bytes=new Uint8Array(await file.slice(offset,offset+2048).arrayBuffer());hash.update(bytes);let binary='';for(const b of bytes)binary+=String.fromCharCode(b);await v2FileOp('chunk',{transfer:v2Transfer,offset,data:btoa(binary)});}
      if(v2UploadAbort)throw Error('上传已取消，未完成临时文件保留');
      await v2FileOp('finish',{transfer:v2Transfer,sha256:hash.hex()});v2Transfer='';
    }
    v2Message('fileMessage','上传已逐文件完整校验并保存，请刷新目录。');
  });
}
v2Id('uploadFiles').onclick=()=>v2Id('uploadPicker').click();v2Id('uploadFolder').onclick=()=>v2Id('folderPicker').click();
v2Id('uploadPicker').onchange=event=>v2Upload([...event.target.files]);v2Id('folderPicker').onchange=event=>v2Upload([...event.target.files]);
document.addEventListener('paste',event=>{if(!v2Files.hidden&&event.clipboardData?.files.length){event.preventDefault();v2Upload([...event.clipboardData.files]);}});
v2Id('cancelFile').onclick=async()=>{v2UploadAbort=true;try{await v2Api('files/cancel',{job:v2Uuid(),transfer:v2Transfer});v2Transfer='';v2Message('fileMessage','已请求取消；未完成的临时文件会保留。');}catch(e){v2Message('fileMessage',e.message);}};
