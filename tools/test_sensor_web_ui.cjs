/* Browser checks against a local mock only. No serial, WiFi device or TF access.
   Run with Node and Playwright available in NODE_PATH. Screenshots are labeled simulated. */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'local-data', 'validation', 'sensor-web-ui');
fs.mkdirSync(output, {recursive:true});
const boot='abcdefabcdefabcdefabcdefabcdefab';
let s={boot,phase:'idle',ready:true,connected:true,ip:'192.168.1.23',card_serial:'10044BA5',card_bytes:'7948206080',
  run:'',seconds:0,written:0,elapsed_us:0,errors:0,queue:0,fifo_high:0,queue_high:0,write_us:0,sync_us:0,free_heap:150000,
  ina_valid:false,ina_us:0,bus_mv:0,current_ua:0,power_uw:0,detail:'软件模拟状态；非实机数据',sha256:'',preview_seq:0,preview_us:0,preview:[]};
let posts=[], unavailable=false, requests=0, outstanding=0,maxOutstanding=0,delayAck=false;
const html=fs.readFileSync(path.join(root,'面包板样机_固件/diagnostics/sensor_web_recorder/src/index.html'),'utf8').replace('{{KEY}}',boot);
const server=http.createServer(async(req,res)=>{
  res.setHeader('Cache-Control','no-store');
  if(req.url==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
  if(req.url==='/api/status'){
    requests++;outstanding++;maxOutstanding=Math.max(maxOutstanding,outstanding);
    await new Promise(r=>setTimeout(r,30));outstanding--;
    res.statusCode=unavailable?503:200;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(s));return;
  }
  if(req.method==='POST'){
    let body='';for await(const data of req)body+=data;
    posts.push({url:req.url,key:req.headers['x-record-key'],body});
    assert.equal(req.headers['x-record-key'],boot);
    if(req.url==='/api/start'){
      assert(!['queued','recording'].includes(s.phase));
      const r=JSON.parse(body);assert.equal(r.card_serial,s.card_serial);assert.equal(r.card_bytes,s.card_bytes);
      assert.match(r.run,/^[0-9A-F]{8}$/);s={...s,run:r.run,seconds:r.seconds,phase:'queued'};
    }
    if(delayAck)await new Promise(r=>setTimeout(r,3000));
    res.setHeader('Content-Type','application/json');res.end('{"accepted":true}');return;
  }
  res.statusCode=404;res.end();
});
async function main(){
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const address='http://127.0.0.1:'+server.address().port;
  const browser=await chromium.launch({headless:true,channel:process.env.WEB_TEST_BROWSER||'msedge'});
  const context=await browser.newContext({viewport:{width:1360,height:1080}});
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const check=async(label,fn)=>{await fn();console.log('PASS: '+label);};
  try{
    await page.goto(address);
    await check('startup only queries; no auto record; invalid INA blank',async()=>{
      await page.waitForFunction(()=>!document.getElementById('start').disabled);
      assert.equal(posts.length,0);assert.equal(await page.locator('#voltage').innerText(),'—');
    });
    await check('invalid duration rejects locally',async()=>{
      await page.locator('#seconds').fill('301');await page.locator('#start').click();assert.equal(posts.length,0);
    });
    await check('double click creates one request and reserves run',async()=>{
      await page.locator('#seconds').fill('300');
      await page.evaluate(()=>{document.getElementById('start').click();document.getElementById('start').click();});
      await page.waitForFunction(()=>document.getElementById('phase').textContent==='请求已排队');
      assert.equal(posts.length,1);assert.equal(await page.locator('#start').isDisabled(),true);
    });
    s={...s,phase:'recording',written:2630000,elapsed_us:100000000,queue:4,write_us:155434,sync_us:111534,
      ina_valid:true,ina_us:100000000,bus_mv:3290,current_ua:750,power_uw:2500,preview_seq:1,preview_us:99997000,
      preview:Array.from({length:96},(_,i)=>[Math.round(Math.sin(i/7)*5000),Math.round(Math.cos(i/9)*3000),16384+Math.round(Math.sin(i/3)*1500)])};
    await page.waitForFunction(()=>document.getElementById('phase').textContent==='正在录制');
    await check('recording shows actual server state and preview',async()=>{
      assert.equal(await page.locator('#voltage').innerText(),'3.290');
      assert.match(await page.locator('#previewLabel').innerText(),/96 点/);
      assert.equal(await page.locator('#start').isDisabled(),true);assert.equal(await page.locator('#stop').isEnabled(),true);
    });
    await page.evaluate(()=>{const banner=document.createElement('div');banner.textContent='软件模拟页面 · 非实机数据';banner.style='background:#efc589;color:#201b13;padding:8px;text-align:center;font-weight:bold';document.body.prepend(banner);});
    await page.screenshot({path:path.join(output,'desktop-simulated.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await check('mobile layout fits viewport',async()=>{assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));});
    await page.screenshot({path:path.join(output,'mobile-simulated.png'),fullPage:true});
    await page.setViewportSize({width:1360,height:1080});
    await check('network loss disables control and reconnect never replays start',async()=>{
      unavailable=true;await page.waitForFunction(()=>document.getElementById('connection').textContent.includes('连接中断'));
      assert.equal(await page.locator('#stop').isDisabled(),true);assert.equal(posts.length,1);
      unavailable=false;await page.waitForFunction(()=>!document.getElementById('stop').disabled);assert.equal(posts.length,1);
    });
    await check('stop is a separate empty request',async()=>{
      await page.locator('#stop').click();await page.waitForFunction(()=>document.getElementById('message').textContent.includes('请求已接收'));
      assert.equal(posts.length,2);assert.equal(posts[1].url,'/api/stop');assert.equal(posts[1].body,'');
    });
    await check('closing and verify are not presented as passed',async()=>{
      s.phase='verifying';await page.waitForFunction(()=>document.getElementById('phase').textContent==='完整回读校验');
      assert.equal(await page.locator('#start').isDisabled(),true);assert.equal(await page.locator('#stop').isDisabled(),true);
    });
    await check('only confirmed result enables next run; stale INA labeled',async()=>{
      s.phase='passed';s.sha256='a'.repeat(64);await page.waitForFunction(()=>!document.getElementById('start').disabled);
      assert.match(await page.locator('#inaState').innerText(),/最后快照/);
      assert.match(await page.locator('#sha').innerText(),/a{64}/);
    });
    await check('refresh reconnect queries without replay',async()=>{
      await page.reload();await page.waitForFunction(()=>!document.getElementById('start').disabled);assert.equal(posts.length,2);
    });
    await check('lost POST acknowledgement never auto-retries',async()=>{
      delayAck=true;await page.locator('#start').click();
      await page.waitForFunction(()=>document.getElementById('message').textContent.includes('结果未确认'));
      assert.equal(posts.length,3);await new Promise(r=>setTimeout(r,500));assert.equal(posts.length,3);delayAck=false;
    });
    await check('failure locks new recording',async()=>{
      s.phase='failed';await page.waitForFunction(()=>document.getElementById('phase').textContent.includes('失败'));
      assert.equal(await page.locator('#start').isDisabled(),true);
    });
    await check('reboot invalidates old page control session',async()=>{
      s.phase='idle';s.boot='f'.repeat(32);await page.waitForFunction(()=>document.getElementById('bootWarning').style.display==='block');
      assert.equal(await page.locator('#start').isDisabled(),true);assert.equal(posts.length,3);
    });
    assert.equal(maxOutstanding,1);assert.deepEqual(errors,[]);
    const report={simulated_only:true,hardware_accessed:false,browser:await browser.version(),playwright:require('playwright/package.json').version,
      assertions:13,posts:posts.length,status_requests:requests,max_parallel_status:maxOutstanding,page_errors:errors,
      screenshots:['desktop-simulated.png','mobile-simulated.png']};
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
  }finally{await browser.close();server.close();}
}
main().catch(e=>{console.error(e);server.close();process.exitCode=1;});
