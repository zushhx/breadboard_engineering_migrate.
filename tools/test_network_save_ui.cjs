const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../面包板样机_固件/diagnostics/sensor_web_recorder/src/controller_v2.js'),'utf8');
const lock=source.slice(source.indexOf('function v2UpdateNetworkLocks'),source.indexOf('function v2Render'));
const handler=source.slice(source.indexOf('let v2NetworkSaving=false,'),source.indexOf('\nasync function v2FileOp'));
(async()=>{for(const mode of ['success','failure','mismatch','disconnect']){
const els={};for(const id of ['saveNetwork','reloadNetwork','scanWifi','staEnabled','apEnabled','wifiSsid','wifiPassword','apSsid','apPassword'])els[id]={value:id==='apPassword'?'synthetic-password':id,checked:true,disabled:false};
let posts=0,message='',saved;
const context={v2Ready:true,active:new Set(['recording']),v2Id:id=>els[id],v2Network:{querySelectorAll:()=>Object.values(els)},setTimeout:r=>r(),v2Message:(_,m)=>message=m,v2Api:async(action,body)=>{if(action==='network/save'){posts++;saved={...body};context.v2UpdateNetworkLocks({phase:'idle',files_busy:false});assert(Object.values(els).every(e=>e.disabled));els.apPassword.value='edited-after-submit';await context.v2Id('saveNetwork').onclick();return {accepted:true};}if(mode==='disconnect')throw Error('offline');return {...saved,apply_ok:mode!=='failure',ap_password:mode==='mismatch'?'other-password':saved.ap_password};}};
vm.createContext(context);vm.runInContext(lock+handler,context);context.v2UpdateNetworkLocks({phase:'idle',files_busy:false});await els.saveNetwork.onclick();assert.equal(posts,1);assert(Object.values(els).every(e=>!e.disabled));assert(message.includes(mode==='success'?'确认应用完成':'结果未确认'));}
console.log('PASS 4 network save cases: snapshot, reentry lock, restore, success/failure/mismatch/disconnect');})();
