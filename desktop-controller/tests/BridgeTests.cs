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
