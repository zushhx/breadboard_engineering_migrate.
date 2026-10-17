'use strict';
class IncrementalSHA256 {
  constructor() {
    if(!IncrementalSHA256.constants) {
      const primes=[]; for(let n=2;primes.length<64;n++) {let prime=true;for(let i=2;i*i<=n;i++)if(n%i===0){prime=false;break;}if(prime)primes.push(n);}
      IncrementalSHA256.constants=primes.map(n=>Math.floor((Math.cbrt(n)%1)*4294967296)>>>0);
      IncrementalSHA256.initial=primes.slice(0,8).map(n=>Math.floor((Math.sqrt(n)%1)*4294967296)>>>0);
    }
    this.h=IncrementalSHA256.initial.slice();this.buffer=new Uint8Array(64);this.used=0;this.length=0;this.finished=false;
  }
  block(bytes) {
    const w=new Uint32Array(64),rotate=(x,n)=>(x>>>n)|(x<<(32-n));
    for(let i=0;i<16;i++)w[i]=(bytes[4*i]<<24)|(bytes[4*i+1]<<16)|(bytes[4*i+2]<<8)|bytes[4*i+3];
    for(let i=16;i<64;i++){const a=w[i-15],b=w[i-2];w[i]=(w[i-16]+(rotate(a,7)^rotate(a,18)^(a>>>3))+w[i-7]+(rotate(b,17)^rotate(b,19)^(b>>>10)))>>>0;}
    let [a,b,c,d,e,f,g,h]=this.h;
    for(let i=0;i<64;i++){
      const t1=(h+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+IncrementalSHA256.constants[i]+w[i])>>>0;
      const t2=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))>>>0;
      [h,g,f,e,d,c,b,a]=[g,f,e,(d+t1)>>>0,c,b,a,(t1+t2)>>>0];
    }
    [a,b,c,d,e,f,g,h].forEach((v,i)=>this.h[i]=(this.h[i]+v)>>>0);
  }
  update(bytes) {
    if(this.finished)throw Error('摘要已经完成');
    this.length+=bytes.length;
    for(let i=0;i<bytes.length;){const n=Math.min(64-this.used,bytes.length-i);this.buffer.set(bytes.subarray(i,i+n),this.used);this.used+=n;i+=n;if(this.used===64){this.block(this.buffer);this.used=0;}}
    return this;
  }
  hex() {
    const bits=this.length*8,pad=new Uint8Array(this.used<56?64-this.used:128-this.used);pad[0]=128;
    const view=new DataView(pad.buffer);view.setUint32(pad.length-8,Math.floor(bits/4294967296));view.setUint32(pad.length-4,bits>>>0);
    this.update(pad);this.finished=true;return this.h.map(n=>n.toString(16).padStart(8,'0')).join('');
  }
}
if(typeof module!=='undefined')module.exports=IncrementalSHA256;
