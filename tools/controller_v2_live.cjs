/* Actual-board v2 trial, invoked only by the candidate-bound approved Python parent. */
const fs=require('fs'),path=require('path'),assert=require('assert/strict'),crypto=require('crypto'),readline=require('readline');
const {chromium}=require('playwright');
const [base,folder]=process.argv.slice(2);
if(!/^http:\/\/127\.0\.0\.1:[0-9]+\/$/.test(base)||!process.env.BREADBOARD_V2_APPROVAL)throw Error('Requires approved local USB host');
const events=[],records=[];let browser,context;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function emit(type,data={}){const event={time:new Date().toISOString(),type,...data};events.push(event);console.log(JSON.stringify(event));}
async function open(url=base){const p=await context.newPage();await p.goto(url,{waitUntil:'domcontentloaded',timeout:15000});await p.waitForFunction(()=>v2Ready&&snapshot?.ready,{},{timeout:15000});return p;}
async function status(p){return p.evaluate(()=>snapshot);}
async function waitPhase(p,phases,timeout=45000){
 await p.waitForFunction(list=>snapshot&&list.includes(snapshot.phase),phases,{timeout});
 const s=await status(p);if(s.phase==='failed'||s.phase==='rejected')throw Error(s.phase+': '+s.detail);return s;
}
async function start(p,seconds){
 await p.locator('[data-panel=monitor]').click();await p.locator('#seconds').fill(String(seconds));await p.locator('#start').click();
 const s=await waitPhase(p,['recording','failed','rejected'],20000);emit('record_started',{run:s.run,seconds,boot:s.boot});return s;
}
async function passed(p,run,timeout=45000){
 const s=await waitPhase(p,['passed','failed','rejected'],timeout);assert.equal(s.run,run);assert.equal(s.errors,0);assert.match(s.sha256,/^[0-9a-f]{64}$/);assert(s.written>0);
 records.push({run,seconds:s.seconds,elapsed_us:s.elapsed_us,samples:s.written,sha256:s.sha256,fifo_high:s.fifo_high,queue_high:s.queue_high});
 emit('record_passed',records[records.length-1]);return s;
}
async function file(p,action,args){return p.evaluate(async({action,args})=>v2FileOp(action,args),{action,args});}
async function api(p,action,args){return p.evaluate(async({action,args})=>v2Api(action,args),{action,args});}
async function main(){
 browser=await chromium.launch({headless:true,channel:'msedge',args:['--no-proxy-server']});
 context=await browser.newContext({viewport:{width:1360,height:1050}});
 const errors=[];context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
 let p=await open();const initial=await status(p);
 assert.equal(initial.card_serial,'10044BA5');assert.equal(initial.card_bytes,'7948206080');assert.equal(initial.phase,'idle');
 emit('usb_startup',{boot:initial.boot,ready:initial.ready,phase:initial.phase});
 await sleep(12000);assert.equal((await status(p)).boot,initial.boot);assert.equal((await status(p)).phase,'idle');emit('native_heartbeat_12s_pass');
 const network=await api(p,'network/state');assert(network.ap_enabled&&network.apply_ok);
 const scanned=await api(p,'network/scan',{});emit('wifi_scan',{networks:scanned.length});
 await api(p,'network/save',{sta_enabled:false,ap_enabled:true,ssid:'',password:'',ap_ssid:network.ap_ssid,ap_password:network.ap_password});
 await sleep(1800);const configured=await api(p,'network/state');assert(configured.ap_enabled&&configured.apply_ok&&!configured.applying);
 emit('ap_saved',{ssid:configured.ap_ssid,ip:configured.ap_ip,persistent_available:configured.persistent_available});
 const directory='/V2_TEST_'+crypto.randomBytes(4).toString('hex').toUpperCase();
 await file(p,'mkdir',{path:directory});
 const bytes=Buffer.from(Array.from({length:5001},(_,i)=>(i*37+11)&255)),hash=crypto.createHash('sha256').update(bytes).digest('hex');
 const upload=await file(p,'begin_upload',{path:directory+'/中文上传.bin',bytes:bytes.length});
 for(let offset=0;offset<bytes.length;offset+=2048)await file(p,'chunk',{transfer:upload.transfer,offset,data:bytes.subarray(offset,offset+2048).toString('base64')});
 const uploaded=await file(p,'finish',{transfer:upload.transfer,sha256:hash});assert.equal(uploaded.sha256,hash);
 await file(p,'rename',{path:directory+'/中文上传.bin',destination:directory+'/改名.bin'});
 await file(p,'copy',{path:directory+'/改名.bin',destination:directory+'/副本.bin'});
 const read=await file(p,'begin_read',{path:directory+'/副本.bin'}),download=[];
 for(;;){const chunk=await file(p,'read',{transfer:read.transfer});if(chunk.done){assert.equal(chunk.verified.sha256,hash);break;}download.push(Buffer.from(chunk.data,'base64'));}
 assert.deepEqual(Buffer.concat(download),bytes);
 // Exercise the desktop streaming download path as well, with independent SHA on PC.
 const response=await context.request.get(base+'api/v2/download?path='+encodeURIComponent(directory+'/副本.bin'),{timeout:20000});
 assert.equal(response.status(),200);assert.equal(crypto.createHash('sha256').update(await response.body()).digest('hex'),hash);
 const measured=await file(p,'measure',{path:directory+'/副本.bin'});
 await file(p,'delete',{path:directory+'/副本.bin',confirmed:true,revision:measured.revision});
 const listed=await file(p,'list',{path:directory,offset:0});assert(listed.entries.some(x=>x.name==='改名.bin'));assert(!listed.entries.some(x=>x.name==='副本.bin'));
 emit('tf_files_pass',{directory,upload_bytes:bytes.length,sha256:hash,operations:['mkdir','upload','rename','copy','download','stream_download','confirmed_delete']});
 const first=await start(p,10);const firstDone=await passed(p,first.run);assert(Math.abs(firstDone.elapsed_us/1e6-10)<2);
 const others=await Promise.all([open(),open(),open()]);
 emit('four_pages_open');
 const long=await start(p,10);await passed(p,long.run,45000);
 for(const q of others)await q.close();emit('four_page_10s_pass');
 const q=await open();
 const a=await start(p,90);await q.waitForFunction(run=>snapshot?.run===run,a.run);
 await q.locator('#seconds').fill('10');await q.locator('#start').click();await q.locator('#takeoverCancel').click();
 await sleep(700);assert.equal((await status(p)).run,a.run);assert.equal((await status(p)).phase,'recording');emit('takeover_cancel_pass');
 await sleep(2200);await q.locator('#start').click();await q.locator('#takeoverContinue').click();
 await q.waitForFunction(old=>snapshot?.run!==old&&snapshot?.phase==='recording',a.run,{timeout:30000});const b=await status(q);assert.equal(b.seconds,10);
 await passed(q,b.run);emit('takeover_continue_pass',{old_run:a.run,new_run:b.run});await q.close();
 const closePage=await open();const closing=await start(closePage,60);await sleep(3200);await closePage.close();
 await p.waitForFunction(run=>snapshot?.run===run&&['passed','failed','rejected'].includes(snapshot.phase),closing.run,{timeout:30000});
 const closed=await passed(p,closing.run);assert(closed.elapsed_us<20000000);emit('owner_page_close_pass',{elapsed_us:closed.elapsed_us});
 const app=await start(p,60);await sleep(3200);
 fs.writeFileSync(path.join(folder,'request-close-host.json'),JSON.stringify({run:app.run}));
 const rl=readline.createInterface({input:process.stdin});const next=await new Promise(resolve=>rl.once('line',resolve));rl.close();
 const originalBoot=app.boot;await p.close();p=await open(next);
 const after=await status(p);assert.equal(after.boot,originalBoot);assert.equal(after.run,app.run);
 const appDone=await passed(p,app.run);assert(appDone.elapsed_us<25000000);emit('desktop_bridge_close_pass',{elapsed_us:appDone.elapsed_us,boot_unchanged:true});
 const all=await file(p,'measure',{path:directory});
 await file(p,'delete',{path:directory,confirmed:true,revision:all.revision});emit('test_directory_cleaned',{directory});
 assert.deepEqual(errors,[]);
 await p.screenshot({path:path.join(folder,'actual-v2-idle.png'),fullPage:true});
 const result={passed:true,actual_device:true,usb:true,ap_config_saved:true,wifi_scan_count:scanned.length,sta_join_tested:false,network_reachable_over_wifi_tested:false,
   four_page_300s:false,four_page_seconds:10,takeover_cancel:true,takeover_continue:true,owner_page_close:true,desktop_bridge_close:true,records,test_directory:directory,test_directory_deleted:true,
   fixture_sha256:hash,boot:initial.boot,browser:await browser.version(),page_errors:errors,cold_starts:0};
 fs.writeFileSync(path.join(folder,'browser-result.json'),JSON.stringify(result,null,2)+'\n');emit('passed');
}
main().catch(error=>{console.error(error);fs.writeFileSync(path.join(folder,'browser-error.json'),JSON.stringify({passed:false,error:error.stack},null,2));process.exitCode=1;})
.finally(async()=>{fs.writeFileSync(path.join(folder,'browser-events.json'),JSON.stringify(events,null,2)+'\n');if(context)await context.close();if(browser)await browser.close();});
