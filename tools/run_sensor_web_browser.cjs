/* Actual-device browser trial. Started only by the explicitly gated Python batch. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {execFileSync}=require('node:child_process');
const [url,folder,mode,secondsText]=process.argv.slice(2),seconds=Number(secondsText);
if(!/^http:\/\/\d+\.\d+\.\d+\.\d+\/$/.test(url)||!['main','early','short'].includes(mode)||seconds!==(mode==='short'?10:300))throw Error('invalid trial arguments');
if(!process.env.BREADBOARD_APPROVAL_PATH||!process.env.BREADBOARD_PYTHON)throw Error('requires gated parent approval');
execFileSync(process.env.BREADBOARD_PYTHON,[path.join(__dirname,'sensor_web_batch.py'),'--validate-approval','--approval-record',process.env.BREADBOARD_APPROVAL_PATH],{stdio:'pipe'});
const events=[];let browser;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
function emit(type,data){const e={time:new Date().toISOString(),type,...data};events.push(e);console.log(JSON.stringify(e));}
async function status(page){return page.evaluate(()=>snapshot);}
async function open(context){const page=await context.newPage();await page.goto(url,{waitUntil:'domcontentloaded',timeout:15000});
  await page.waitForFunction(()=>snapshot&&online&&snapshot.connected,{},{timeout:15000});return page;}
async function main(){
  fs.mkdirSync(folder,{recursive:true});
  browser=await chromium.launch({headless:true,channel:'msedge',args:['--no-proxy-server']});
  const context=await browser.newContext({viewport:{width:1360,height:1080}});
  let page=await open(context);let pageErrors=[];page.on('pageerror',e=>pageErrors.push(e.message));
  const initial=await status(page);assert.equal(initial.card_serial,'10044BA5');assert.equal(initial.card_bytes,'7948206080');
  assert(initial.ready&&['idle','passed','rejected'].includes(initial.phase));
  await page.locator('#seconds').fill(String(seconds));
  await page.evaluate(()=>{document.getElementById('start').click();document.getElementById('start').click();});
  await page.waitForFunction(()=>snapshot&&['recording','failed','rejected'].includes(snapshot.phase),{},{timeout:20000});
  let current=await status(page);assert.equal(current.phase,'recording');const run=current.run,boot=current.boot;
  emit('started',{run,seconds,boot,mode});
  const duplicate=await page.evaluate(async()=>{const r=await fetch('/api/start',{method:'POST',headers:{'X-Record-Key':sessionKey,'Content-Type':'application/json'},
      body:JSON.stringify({run:snapshot.run,seconds:snapshot.seconds,card_serial:snapshot.card_serial,card_bytes:snapshot.card_bytes})});return r.status;});
  assert.equal(duplicate,409);emit('duplicate_rejected',{http_status:duplicate});
  const started=Date.now();let next=0,disconnected=false,stopped=false,internet=null;
  while(Date.now()-started<(seconds+120)*1000){
    current=await status(page);assert.equal(current.run,run);assert.equal(current.boot,boot);
    if(current.phase==='failed'||current.phase==='rejected')throw Error('device '+current.phase+': '+current.detail);
    if(current.phase==='passed')break;
    const elapsed=Date.now()-started;
    if(elapsed>=next){emit('state',current);next=elapsed+10000;}
    if(mode==='main'&&elapsed>15000&&!disconnected){
      await page.screenshot({path:path.join(folder,'actual-recording.png'),fullPage:true});
      try{const r=await context.request.get('http://www.msftconnecttest.com/connecttest.txt',{timeout:10000});internet=r.ok()&&(await r.text()).trim()==='Microsoft Connect Test';}
      catch(e){internet=false;emit('internet_probe_error',{message:e.message});}
      emit('internet_probe',{success:internet,url:'http://www.msftconnecttest.com/connecttest.txt'});
      await page.close();emit('page_disconnected',{run});await delay(10000);
      page=await open(context);page.on('pageerror',e=>pageErrors.push(e.message));
      const resumed=await status(page);assert.equal(resumed.run,run);assert.equal(resumed.boot,boot);
      assert(['recording','draining','closing','verifying'].includes(resumed.phase));
      assert(resumed.elapsed_us>current.elapsed_us);assert(await page.locator('#start').isDisabled());
      emit('page_reconnected',{run,elapsed_us:resumed.elapsed_us});disconnected=true;
    }
    if(mode==='early'&&elapsed>10000&&!stopped){await page.locator('#stop').click();emit('early_stop_requested',{run});stopped=true;}
    await delay(250);
  }
  current=await status(page);assert.equal(current.phase,'passed');assert.equal(current.errors,0);
  assert(/^[0-9a-f]{64}$/.test(current.sha256));assert(current.written>0&&current.ina_us>0);
  if(mode==='early')assert(current.elapsed_us>=8000000&&current.elapsed_us<30000000);
  else assert(Math.abs(current.elapsed_us/1000000-seconds)<2);
  assert.deepEqual(pageErrors,[]);
  await page.screenshot({path:path.join(folder,'actual-completed.png'),fullPage:true});
  const result={passed:true,actual_device:true,mode,run,boot,seconds,final:current,public_connect_test_success:internet,
      page_reconnect_verified:disconnected,early_stop_verified:stopped,duplicate_http_status:duplicate,
      browser:await browser.version(),playwright:require('playwright/package.json').version,page_errors:pageErrors};
  fs.writeFileSync(path.join(folder,'browser-result.json'),JSON.stringify(result,null,2)+'\n');emit('passed',{run,sha256:current.sha256});
}
main().catch(e=>{fs.writeFileSync(path.join(folder,'browser-error.json'),JSON.stringify({passed:false,error:e.stack},null,2));console.error(e);process.exitCode=1;})
 .finally(async()=>{fs.writeFileSync(path.join(folder,'browser-events.json'),JSON.stringify(events,null,2)+'\n');if(browser)await browser.close();});
