using System;
using System.Collections.Generic;
using System.Linq;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Net.WebSockets;
using System.Web.Script.Serialization;
using BreadboardController;
public static class BridgeTests
{
    private const string Boot="abcdefabcdefabcdefabcdefabcdefab";
    private sealed class Fake : IDeviceBackend
    {
        public readonly List<string> Calls=new List<string>();
        public bool Disposed;
        public string Description { get { return "loopback mock"; } }
        public BridgeResponse Request(string method,string path,string body,string key)
        {
            lock(Calls)Calls.Add(method+" "+path+" "+body);
            return new BridgeResponse {Code=path=="/api/v2/fail"?409:200,Type="application/json",Body=Encoding.UTF8.GetBytes(path=="/"?"mock page":"{\"accepted\":true}")};
        }
        public bool Contains(string path,string value) {lock(Calls)return Calls.Any(c=>c.Contains(path)&&c.Contains(value));}
        public void Dispose(){Disposed=true;}
    }
    private sealed class ConnectionFake : IDeviceBackend
    {
        public string Json="{ \"version\" : 2 }";
        public int Code=200,Disposed,Requests;
        public bool FailProbe;
        public Action OnDispose,OnRequest;
        public string Description {get{return "USB connection mock";}}
        public BridgeResponse Request(string method,string path,string body,string key)
        {
            Interlocked.Increment(ref Requests);
            if(OnRequest!=null)OnRequest();
            if(FailProbe)throw new TimeoutException("mock stale USB handle");
            return new BridgeResponse {Code=Code,Body=Encoding.UTF8.GetBytes(Json),Type="application/json"};
        }
        public void Dispose(){if(Interlocked.Increment(ref Disposed)==1&&OnDispose!=null)OnDispose();}
    }
    private static async Task ConnectionChecks(Action<bool,string> check)
    {
        var existing=new ConnectionFake();var existingBridge=new DeviceBridge(existing);
        int opens=0,releases=0;bool occupied=true;
        existing.OnDispose=()=>occupied=false;
        var reused=await BackendConnection.ConnectUsbAsync(existing,existingBridge,true,
            ()=>{releases++;return Task.Run(()=>existingBridge.Dispose());},
            ()=>{opens++;if(occupied)throw new UnauthorizedAccessException("mock exclusive port in use");return new ConnectionFake();},()=>false);
        var reusedAgain=await BackendConnection.ConnectUsbAsync(reused.Backend,reused.Bridge,true,
            ()=>{releases++;return Task.FromResult(0);},()=>{opens++;throw new Exception("unexpected second open");},()=>false);
        check(reused.Reused&&reusedAgain.Reused&&Object.ReferenceEquals(existingBridge,reusedAgain.Bridge)&&opens==0&&releases==0&&existing.Disposed==0,
            "repeated healthy USB connect reuses the exclusive handle and loopback sessions");
        existing.FailProbe=true;
        ConnectionFake replacement=null;
        var reopened=await BackendConnection.ConnectUsbAsync(existing,existingBridge,true,
            ()=>{releases++;return Task.Run(()=>existingBridge.Dispose());},
            ()=>{opens++;if(occupied)throw new UnauthorizedAccessException("mock exclusive port in use");return replacement=new ConnectionFake();},()=>false);
        check(!reopened.Reused&&opens==1&&releases==1&&existing.Disposed==1&&replacement.Requests==1,
            "stale same-port connection releases its handle before reopening and validating");
        reopened.Bridge.Dispose();

        existing=new ConnectionFake {FailProbe=true};existingBridge=new DeviceBridge(existing);releases=0;
        bool retryOpenFailed=false;
        try
        {
            await BackendConnection.ConnectUsbAsync(existing,existingBridge,true,
                ()=>{releases++;existingBridge.Dispose();return Task.FromResult(0);},
                ()=>{throw new UnauthorizedAccessException("mock unplugged USB");},()=>false);
        }
        catch(UnauthorizedAccessException){retryOpenFailed=true;}
        check(retryOpenFailed&&releases==1&&existing.Disposed==1,"failed stale-port reopen leaves the old handle released");
        var afterUnplug=await BackendConnection.ConnectUsbAsync(null,null,false,()=>Task.FromResult(0),()=>new ConnectionFake(),()=>false);
        check(afterUnplug.Backend!=null&&!afterUnplug.Reused,"a fresh connect recovers after a failed stale-port reopen");
        afterUnplug.Bridge.Dispose();

        existing=new ConnectionFake();existingBridge=new DeviceBridge(existing);
        var preserved=existingBridge;releases=0;
        bool openFailed=false;
        try
        {
            await BackendConnection.ConnectUsbAsync(existing,existingBridge,false,
                ()=>{releases++;preserved.Dispose();return Task.FromResult(0);},
                ()=>{throw new UnauthorizedAccessException("mock other port busy");},()=>false);
        }
        catch(UnauthorizedAccessException){openFailed=true;}
        check(openFailed&&releases==0&&existing.Disposed==0,"failed different-port open preserves the working connection");
        var unsupported=new ConnectionFake {Json="{\"version\":20}"};bool rejected=false;
        try
        {
            await BackendConnection.ConnectUsbAsync(existing,existingBridge,false,
                ()=>{releases++;preserved.Dispose();return Task.FromResult(0);},()=>unsupported,()=>false);
        }
        catch(FirmwareCapabilityException){rejected=true;}
        check(rejected&&unsupported.Disposed==1&&releases==0&&existing.Disposed==0,
            "unsupported candidate is closed while the previous connection remains usable");
        var recovered=await BackendConnection.ConnectUsbAsync(existing,existingBridge,false,
            ()=>{releases++;return Task.Run(()=>preserved.Dispose());},()=>new ConnectionFake(),()=>false);
        check(releases==1&&existing.Disposed==1&&!recovered.Reused,"a later retry succeeds after candidate failure");
        recovered.Bridge.Dispose();

        var unavailable=new ConnectionFake {Code=503};bool unavailableIsTransport=false;
        try{BackendConnection.ValidateV2(unavailable);}catch(FirmwareCapabilityException){}catch(IOException){unavailableIsTransport=true;}
        check(unavailableIsTransport,"HTTP 503 is a connection error without an upgrade diagnosis");
        var malformed=new ConnectionFake {Json="{broken"};bool malformedIsResponse=false;
        try{BackendConnection.ValidateV2(malformed);}catch(FirmwareCapabilityException){}catch(InvalidDataException){malformedIsResponse=true;}
        check(malformedIsResponse,"malformed capability JSON is a response error without an upgrade diagnosis");
        var missing=new ConnectionFake {Code=404};bool missingIsUpgrade=false;
        try{BackendConnection.ValidateV2(missing);}catch(FirmwareCapabilityException){missingIsUpgrade=true;}
        check(missingIsUpgrade,"missing capability endpoint receives the explicit firmware upgrade diagnosis");
        var badCleanup=new ConnectionFake {Json="{broken",OnDispose=()=>{throw new IOException("mock cleanup failure");}};
        Exception retained=null;
        try
        {
            await BackendConnection.ConnectUsbAsync(null,null,false,()=>Task.FromResult(0),()=>badCleanup,()=>false);
        }
        catch(Exception error){retained=error;}
        check(retained is InvalidDataException&&retained.Data["UsbCleanupException"] is IOException&&
            retained.StackTrace.Contains("ValidateV2")&&badCleanup.Disposed==1,
            "async cleanup failure preserves the original validation exception and stack trace");

        var opened=new ManualResetEventSlim(false);var finishOpen=new ManualResetEventSlim(false);
        var pending=new ConnectionFake();int closing=0,closeRelease=0;
        var connecting=BackendConnection.ConnectUsbAsync(null,null,false,
            ()=>{closeRelease++;return Task.FromResult(0);},()=>{opened.Set();finishOpen.Wait();return pending;},()=>Volatile.Read(ref closing)!=0);
        check(opened.Wait(3000),"mock connection reaches pending USB open");
        Interlocked.Exchange(ref closing,1);finishOpen.Set();bool cancelled=false;
        try{await connecting;}catch(OperationCanceledException){cancelled=true;}
        check(cancelled&&pending.Disposed==1&&pending.Requests==0&&closeRelease==0,
            "window close during USB open disposes the candidate without validation or installation");
        opened.Dispose();finishOpen.Dispose();

        var releasing=new ManualResetEventSlim(false);var finishRelease=new ManualResetEventSlim(false);
        var old=new ConnectionFake {FailProbe=true};var oldBridge=new DeviceBridge(old);closing=0;opens=0;
        var releasePending=BackendConnection.ConnectUsbAsync(old,oldBridge,true,
            ()=>Task.Run(()=>{releasing.Set();finishRelease.Wait();oldBridge.Dispose();}),
            ()=>{opens++;return new ConnectionFake();},()=>Volatile.Read(ref closing)!=0);
        check(releasing.Wait(3000),"mock reconnect reaches pending previous-handle release");
        Interlocked.Exchange(ref closing,1);finishRelease.Set();cancelled=false;
        try{await releasePending;}catch(OperationCanceledException){cancelled=true;}
        check(cancelled&&old.Disposed==1&&opens==0,
            "close while releasing a stale handle waits for release and prevents a replacement open");
        releasing.Dispose();finishRelease.Dispose();

        releasing=new ManualResetEventSlim(false);finishRelease=new ManualResetEventSlim(false);
        old=new ConnectionFake();oldBridge=new DeviceBridge(old);var prepared=new ConnectionFake();closing=0;
        var candidatePending=BackendConnection.ConnectUsbAsync(old,oldBridge,false,
            ()=>Task.Run(()=>{releasing.Set();finishRelease.Wait();oldBridge.Dispose();}),
            ()=>prepared,()=>Volatile.Read(ref closing)!=0);
        check(releasing.Wait(3000),"replacement candidate validates before previous-bridge release");
        Interlocked.Exchange(ref closing,1);finishRelease.Set();cancelled=false;
        try{await candidatePending;}catch(OperationCanceledException){cancelled=true;}
        check(cancelled&&old.Disposed==1&&prepared.Disposed==1,
            "close during replacement release completes only after both old and prepared connections are closed");
        releasing.Dispose();finishRelease.Dispose();
        var disposed=new ConnectionFake();var concurrentBridge=new DeviceBridge(disposed);
        await Task.WhenAll(Task.Run(()=>concurrentBridge.Dispose()),Task.Run(()=>concurrentBridge.Dispose()));
        check(disposed.Disposed==1,"concurrent bridge disposal closes its backend once");
    }
    private static string Raw(Uri address,string method,string path,string host,string origin,string body)
    {
        using(var client=new TcpClient("127.0.0.1",address.Port))
        {
            client.ReceiveTimeout=3000;
            using(var stream=client.GetStream())
            {
                byte[] data=Encoding.UTF8.GetBytes(body);
                string header=method+" "+path+" HTTP/1.1\r\nHost: "+host+"\r\n"+(origin==null?"":"Origin: "+origin+"\r\n")+"Content-Length: "+data.Length+"\r\nConnection: close\r\n\r\n";
                byte[] bytes=Encoding.ASCII.GetBytes(header);stream.Write(bytes,0,bytes.Length);stream.Write(data,0,data.Length);
                using(var reader=new StreamReader(stream))return reader.ReadToEnd();
            }
        }
    }
    public static async Task Run(Action<bool,string> check)
    {
        await ConnectionChecks(check);
        byte[] digits=Encoding.ASCII.GetBytes("123456789");
        check(~UsbFrames.Crc(digits,0,digits.Length,0xffffffff)==0xcbf43926,"USB CRC matches external standard vector");
        byte[] frame=UsbFrames.Encode(1,0x12345678,digits);
        check(UsbFrames.Valid(frame)&&UsbFrames.Get32(frame,8)==0x12345678,"USB frame round-trip preserves request identity");
        frame[16]^=1;check(!UsbFrames.Valid(frame),"USB payload corruption is rejected");
        frame[16]^=1;frame[8]^=1;check(!UsbFrames.Valid(frame),"USB header corruption is rejected");
        var fake=new Fake();
        using(var bridge=new DeviceBridge(fake))
        {
            string host="127.0.0.1:"+bridge.Address.Port;
            check(Raw(bridge.Address,"GET","/",host,null,"").EndsWith("mock page"),"local bridge forwards root without hardware");
            check(Raw(bridge.Address,"POST","/api/v2/session/start",host,bridge.Address.GetLeftPart(UriPartial.Authority),"{\"test\":1}").StartsWith("HTTP/1.1 200"),"same-origin command forwarded");
            check(fake.Contains("/session/start","{\"test\":1}"),"command body retained exactly");
            check(Raw(bridge.Address,"POST","/api/v2/session/start",host,"http://evil.invalid","{}").StartsWith("HTTP/1.1 403"),"cross-origin command blocked");
            check(Raw(bridge.Address,"GET","/","evil.invalid",null,"").StartsWith("HTTP/1.1 403"),"DNS rebinding Host blocked");
            check(Raw(bridge.Address,"GET","/private",host,null,"").StartsWith("HTTP/1.1 404"),"arbitrary proxy route blocked");
            check(Raw(bridge.Address,"GET","/api/v2/fail",host,null,"").StartsWith("HTTP/1.1 409"),"device conflict status preserved without retry");
            string id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
            using(var socket=new ClientWebSocket())
            {
                await socket.ConnectAsync(new Uri(bridge.Address.AbsoluteUri.Replace("http:","ws:")+"api/v2/live?client="+id+"&boot="+Boot),CancellationToken.None);
                var buffer=new ArraySegment<byte>(new byte[16]);
                var received=socket.ReceiveAsync(buffer,CancellationToken.None); // processes native PING/PONG while waiting for close
                await Task.Delay(2200);
                check(fake.Contains("/session/touch",id),"browser WebSocket heartbeat reaches backend");
                bridge.Dispose();
                check(fake.Contains("/session/release",id)&&fake.Disposed,"desktop close releases associated session and closes backend");
                socket.Abort();
                try{await received;}catch(Exception){}
            }
        }
        check(!fake.Contains("/session/release","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),"unassociated observer session is not released");
    }
}
