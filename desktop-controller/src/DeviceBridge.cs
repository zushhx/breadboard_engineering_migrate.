using System;
using System.Collections.Generic;
using System.Globalization;
using System.Diagnostics;
using System.IO;
using System.IO.Ports;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using Microsoft.Win32;
namespace BreadboardController
{
    public sealed class BridgeResponse
    {
        public int Code;
        public string Type;
        public byte[] Body;
        public string Text { get { return Encoding.UTF8.GetString(Body); } }
    }
    public interface IDeviceBackend : IDisposable
    {
        BridgeResponse Request(string method,string path,string body,string key);
        string Description { get; }
    }
    public sealed class NetworkBackend : IDeviceBackend
    {
        private readonly Uri address;
        private readonly HttpClient client;
        public NetworkBackend(Uri address)
        {
            this.address=address;
            client=new HttpClient(new HttpClientHandler {UseProxy=false,AllowAutoRedirect=false,UseCookies=false,UseDefaultCredentials=false});
            client.Timeout=TimeSpan.FromSeconds(5);client.MaxResponseContentBufferSize=524288;
            client.DefaultRequestHeaders.ConnectionClose=true;
        }
        public string Description {get{return AddressPolicy.Display(address);}}
        public BridgeResponse Request(string method,string path,string body,string key)
        {
            using(var request=new HttpRequestMessage(new HttpMethod(method),new Uri(address,path)))
            {
                if(method=="POST")request.Content=new StringContent(body,Encoding.UTF8,"application/json");
                if(!String.IsNullOrEmpty(key))request.Headers.TryAddWithoutValidation("X-Record-Key",key);
                using(var response=client.SendAsync(request).GetAwaiter().GetResult())
                    return new BridgeResponse {Code=(int)response.StatusCode,Type=response.Content.Headers.ContentType==null?"application/json":response.Content.Headers.ContentType.ToString(),Body=response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()};
            }
        }
        public void Dispose(){client.Dispose();}
    }
    public static class UsbFrames
    {
        public static uint Crc(byte[] bytes,int offset,int length,uint seed)
        {
            uint value=seed;for(int i=0;i<length;i++){value^=bytes[offset+i];for(int j=0;j<8;j++)value=(value>>1)^(0xedb88320u&unchecked((uint)-(int)(value&1)));}return value;
        }
        public static uint Get32(byte[] b,int i){return (uint)b[i]|((uint)b[i+1]<<8)|((uint)b[i+2]<<16)|((uint)b[i+3]<<24);}
        public static void Put32(byte[] b,int i,uint n){for(int j=0;j<4;j++)b[i+j]=(byte)(n>>(8*j));}
        public static byte[] Encode(byte kind,uint id,byte[] payload)
        {
            if(payload.Length>4096)throw new ArgumentException("USB请求超过4096字节");
            byte[] frame=new byte[16+payload.Length];Encoding.ASCII.GetBytes("BRC2").CopyTo(frame,0);
            frame[4]=2;frame[5]=kind;frame[6]=(byte)payload.Length;frame[7]=(byte)(payload.Length>>8);Put32(frame,8,id);
            payload.CopyTo(frame,16);Put32(frame,12,~Crc(frame,16,payload.Length,Crc(frame,4,8,0xffffffff)));return frame;
        }
        public static bool Valid(byte[] frame)
        {
            if(frame.Length<16||Encoding.ASCII.GetString(frame,0,4)!="BRC2"||frame[4]!=2)return false;
            int length=frame[6]|(frame[7]<<8);
            return length<=4096&&frame.Length==16+length&&Get32(frame,12)==~Crc(frame,16,length,Crc(frame,4,8,0xffffffff));
        }
    }
    public sealed class UsbBackend : IDeviceBackend
    {
        private readonly SerialPort port;
        private readonly object wire=new object();
        private uint sequence;
        public string Description {get{return "USB "+port.PortName;}}
        public UsbBackend(string name)
        {
            if(!System.Text.RegularExpressions.Regex.IsMatch(name,@"\ACOM[1-9][0-9]*\z"))throw new ArgumentException("请选择原生USB串口");
            port=new SerialPort(name,115200,Parity.None,8,StopBits.One) {DtrEnable=false,RtsEnable=false,Handshake=Handshake.None,ReadTimeout=2500,WriteTimeout=2500};
            port.Open(); // No reset, DTR/RTS toggle, bootloader command, or flashing.
        }
        public static string[] Ports()
        {
            var names=new List<string>();
            using(var usb=Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Enum\USB"))
            {
                if(usb!=null)foreach(string device in usb.GetSubKeyNames().Where(n=>n.StartsWith("VID_303A&PID_1001",StringComparison.OrdinalIgnoreCase)))
                using(var key=usb.OpenSubKey(device))
                {
                    if(key==null)continue;
                    foreach(string instance in key.GetSubKeyNames())
                    using(var parameters=key.OpenSubKey(instance+@"\Device Parameters"))
                    {
                        var value=parameters==null?null:parameters.GetValue("PortName") as string;
                        if(value!=null&&SerialPort.GetPortNames().Contains(value))names.Add(value);
                    }
                }
            }
            return names.Distinct().OrderBy(n=>n).ToArray();
        }
        private byte[] ReadFrame()
        {
            byte[] header=new byte[16];int used=0;var deadline=Stopwatch.StartNew();
            for(;;)
            {
                if(deadline.ElapsedMilliseconds>5000)throw new TimeoutException("USB帧未能同步，请重新连接USB");
                int n=port.ReadByte();if(n<0)throw new EndOfStreamException();
                header[used++]=(byte)n;
                if(used<4)continue;
                if(Encoding.ASCII.GetString(header,0,4)!="BRC2"){Buffer.BlockCopy(header,1,header,0,--used);continue;}
                while(used<16)used+=port.Read(header,used,16-used);
                int length=header[6]|(header[7]<<8);
                if(length>4096||header[4]!=2){used=0;continue;}
                byte[] frame=new byte[16+length];header.CopyTo(frame,0);int got=0;
                while(got<length)got+=port.Read(frame,16+got,length-got);
                if(!UsbFrames.Valid(frame))throw new InvalidDataException("USB数据校验失败，命令没有自动重发");
                return frame;
            }
        }
        public BridgeResponse Request(string method,string path,string body,string key)
        {
            lock(wire)
            {
                var data=new Dictionary<string,object>{{"method",method},{"path",path},{"body",body??""},{"key",key??""}};
                byte[] outgoing=UsbFrames.Encode(1,++sequence,Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(data)));
                port.Write(outgoing,0,outgoing.Length);
                BridgeResponse response=null;long expected=-1;
                using(var buffer=new MemoryStream())
                {
                    for(;;)
                    {
                        var frame=ReadFrame();if(UsbFrames.Get32(frame,8)!=sequence)continue;
                        int length=frame.Length-16;
                        if(frame[5]==2)
                        {
                            if(response!=null)throw new InvalidDataException("USB响应顺序错误");
                            var metadata=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(frame,16,length));
                            expected=Convert.ToInt64(metadata["length"],CultureInfo.InvariantCulture);
                            if(expected<0||expected>524288)throw new InvalidDataException("USB响应超过上限");
                            response=new BridgeResponse {Code=Convert.ToInt32(metadata["code"],CultureInfo.InvariantCulture),Type=Convert.ToString(metadata["type"],CultureInfo.InvariantCulture)};
                            if(response.Code<100||response.Code>599)throw new InvalidDataException("USB响应状态无效");
                        }
                        else if(frame[5]==3)
                        {
                            if(response==null||buffer.Length+length>expected)throw new InvalidDataException("USB响应长度不符");
                            buffer.Write(frame,16,length);
                        }
                        else if(frame[5]==4)
                        {
                            if(response==null||buffer.Length!=expected)throw new InvalidDataException("USB响应未完整接收");
                            response.Body=buffer.ToArray();return response;
                        }
                        else throw new InvalidDataException("USB帧类型无效");
                    }
                }
            }
        }
        public void Dispose(){port.Dispose();}
    }
    // User-level loopback server: no URL ACL, firewall rule, service or administrator installation.
    public sealed class DeviceBridge : IDisposable
    {
        private readonly IDeviceBackend backend;
        private readonly TcpListener listener;
        private readonly Dictionary<string,string> sessions=new Dictionary<string,string>();
        private volatile bool closed;
        private int connections;
        public readonly Uri Address;
        public DeviceBridge(IDeviceBackend backend)
        {
            this.backend=backend;listener=new TcpListener(IPAddress.Loopback,0);listener.Start(12);
            Address=new Uri("http://127.0.0.1:"+((IPEndPoint)listener.LocalEndpoint).Port+"/");
            Task.Run((Func<Task>)Accept);
        }
        private async Task Accept()
        {
            while(!closed)
            {
                TcpClient client;
                try{client=await listener.AcceptTcpClientAsync();}catch(ObjectDisposedException){return;}catch(SocketException){return;}
                if(Interlocked.Increment(ref connections)>12){Interlocked.Decrement(ref connections);client.Close();continue;}
                Observe(Task.Run(()=>Serve(client)));

            }
        }
        private static void Observe(Task task) { task.ContinueWith(t=>{if(t.IsFaulted){var ignored=t.Exception;}}); }
        private static byte[] Exact(Stream stream,int length)
        {
            byte[] data=new byte[length];int count=0;while(count<length){int n=stream.Read(data,count,length-count);if(n==0)throw new EndOfStreamException();count+=n;}return data;
        }
        private static Dictionary<string,string> Query(string query)
        {
            var result=new Dictionary<string,string>();foreach(var part in query.TrimStart('?').Split('&')){int at=part.IndexOf('=');if(at>=0)result[WebUtility.UrlDecode(part.Substring(0,at))]=WebUtility.UrlDecode(part.Substring(at+1));}return result;
        }
        private static void Reply(Stream stream,int code,string type,byte[] data)
        {
            string header="HTTP/1.1 "+code+" Response\r\nContent-Type: "+type.Replace("\r","").Replace("\n","")+"\r\nContent-Length: "+data.Length+"\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nContent-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'\r\nConnection: close\r\n\r\n";
            byte[] bytes=Encoding.ASCII.GetBytes(header);stream.Write(bytes,0,bytes.Length);stream.Write(data,0,data.Length);
        }
        private void Serve(TcpClient socket)
        {
            using(socket)
            {
                try
                {
                    socket.ReceiveTimeout=7000;socket.SendTimeout=7000;
                    using(var stream=socket.GetStream())
                    {
                        var bytes=new List<byte>();int last=0;
                        while(bytes.Count<8192)
                        {
                            int b=stream.ReadByte();if(b<0)throw new EndOfStreamException();bytes.Add((byte)b);
                            last=((last<<8)|b);if(last==0x0d0a0d0a)break;
                        }
                        if(bytes.Count>=8192)throw new InvalidDataException("HTTP请求头过长");
                        string[] lines=Encoding.ASCII.GetString(bytes.ToArray()).Split(new[]{"\r\n"},StringSplitOptions.None);
                        string[] first=lines[0].Split(' ');
                        if(first.Length!=3||!first[1].StartsWith("/")||first[1].StartsWith("//"))throw new InvalidDataException("HTTP目标无效");
                        var headers=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
                        foreach(string line in lines.Skip(1)){int at=line.IndexOf(':');if(at>0)headers[line.Substring(0,at)]=line.Substring(at+1).Trim();}
                        int length=0;string value;
                        if(headers.TryGetValue("Transfer-Encoding",out value))throw new InvalidDataException("不支持分块请求");
                        if(headers.TryGetValue("Content-Length",out value)&&(!Int32.TryParse(value,out length)||length<0||length>4096))throw new InvalidDataException("请求长度无效");
                        string body=Encoding.UTF8.GetString(Exact(stream,length));
                        string host,origin;
                        if(!headers.TryGetValue("Host",out host)||host!="127.0.0.1:"+Address.Port||
                           (headers.TryGetValue("Origin",out origin)&&origin!=Address.GetLeftPart(UriPartial.Authority)))
                        {Reply(stream,403,"text/plain",Encoding.UTF8.GetBytes("只接受本机同源请求"));return;}
                        var target=new Uri(Address,first[1]);
                        if(first[0]=="GET"&&target.AbsolutePath=="/api/v2/live"){Live(stream,headers,Query(target.Query));return;}
                        if(first[0]=="GET"&&target.AbsolutePath=="/api/v2/download"){try { Download(stream,Query(target.Query)); } catch(Exception) { } return;}
                        if((first[0]!="GET"&&first[0]!="POST")||
                           !(target.AbsolutePath=="/"||target.AbsolutePath=="/api/status"||target.AbsolutePath=="/api/trend"||
                             System.Text.RegularExpressions.Regex.IsMatch(target.AbsolutePath,@"\A/api/v2/[a-z/_]+\z")))
                        {Reply(stream,404,"text/plain",new byte[0]);return;}
                        string key;headers.TryGetValue("X-Record-Key",out key);
                        var response=backend.Request(first[0],target.AbsolutePath,body,key);
                        Reply(stream,response.Code,response.Type,response.Body);
                    }
                }
                catch(Exception error)
                {
                    if(error is IOException||error is SocketException||error is TimeoutException||error is HttpRequestException||
                       error is InvalidOperationException||error is ArgumentException||error is KeyNotFoundException)
                    {try{Reply(socket.GetStream(),502,"application/json",Encoding.UTF8.GetBytes("{\"error\":\"连接中断或响应无效；命令不会自动重发\"}"));}catch(Exception){}}
                    else throw;
                }
                finally{Interlocked.Decrement(ref connections);}
            }
        }
        private Dictionary<string,object> Api(string path,Dictionary<string,object> data)
        {
            var serializer=new JavaScriptSerializer();
            var response=backend.Request(data==null?"GET":"POST","/api/v2/"+path,data==null?"":serializer.Serialize(data),"");
            var result=serializer.Deserialize<Dictionary<string,object>>(response.Text);
            if(response.Code>=400)throw new IOException(result.ContainsKey("error")?Convert.ToString(result["error"]):"主控拒绝请求");
            return result;
        }
        private static void WsSend(Stream stream,byte kind,byte[] payload)
        {
            if(payload.Length>125)throw new InvalidDataException();
            stream.WriteByte((byte)(128|kind));stream.WriteByte((byte)payload.Length);stream.Write(payload,0,payload.Length);
        }
        private void Live(NetworkStream stream,Dictionary<string,string> headers,Dictionary<string,string> query)
        {
            string id,boot,key;
            if(!query.TryGetValue("client",out id)||!System.Text.RegularExpressions.Regex.IsMatch(id,@"\A[0-9a-fA-F]{32}\z")||
               !query.TryGetValue("boot",out boot)||!System.Text.RegularExpressions.Regex.IsMatch(boot,@"\A[0-9a-fA-F]{32}\z")||
               !headers.TryGetValue("Sec-WebSocket-Key",out key)||Convert.FromBase64String(key).Length!=16)throw new InvalidDataException("控制连接无效");
            Api("session/touch",new Dictionary<string,object>{{"client",id},{"boot",boot}});
            lock(sessions)sessions[id]=boot;
            string accept;using(var hash=SHA1.Create())accept=Convert.ToBase64String(hash.ComputeHash(Encoding.ASCII.GetBytes(key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11")));
            byte[] handshake=Encoding.ASCII.GetBytes("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "+accept+"\r\n\r\n");
            stream.Write(handshake,0,handshake.Length);stream.ReadTimeout=5000;
            try
            {
                while(!closed)
                {
                    WsSend(stream,9,new byte[0]);bool pong=false;
                    while(!pong&&!closed)
                    {
                        byte[] h=Exact(stream,2);int kind=h[0]&15,n=h[1]&127;
                        if((h[0]&128)==0||(h[1]&128)==0||n>125)throw new InvalidDataException("控制帧无效");
                        byte[] mask=Exact(stream,4),payload=Exact(stream,n);for(int i=0;i<n;i++)payload[i]^=mask[i%4];
                        if(kind==8){WsSend(stream,8,new byte[0]);return;}
                        if(kind==9)WsSend(stream,10,payload);
                        if(kind==10)pong=true;
                    }
                    if(pong)Api("session/touch",new Dictionary<string,object>{{"client",id},{"boot",boot}});
                    Thread.Sleep(2000);
                }
            }
            finally
            {
                lock(sessions)sessions.Remove(id);
                try{Api("session/release",new Dictionary<string,object>{{"client",id},{"boot",boot}});}catch(Exception){}
            }
        }
        private Dictionary<string,object> FileAction(string action,Dictionary<string,object> args,string boot)
        {
            string job=Guid.NewGuid().ToString("N");args["job"]=job;
            args["boot"]=boot;Api("files/"+action,args);
            var timer=Stopwatch.StartNew();
            while(timer.ElapsedMilliseconds<7000)
            {
                Thread.Sleep(20);var state=Api("files/state",null);
                if(Convert.ToString(state["job"])!=job)throw new IOException("文件任务状态已变化");
                if(!(bool)state["running"])
                {
                    var result=(Dictionary<string,object>)state["result"];
                    if(Convert.ToInt32(state["code"],CultureInfo.InvariantCulture)>=400)throw new IOException(Convert.ToString(result["error"]));
                    return result;
                }
            }
            throw new TimeoutException("文件任务尚未完成");
        }
        private void Download(NetworkStream stream,Dictionary<string,string> query)
        {
            string path;if(!query.TryGetValue("path",out path))throw new ArgumentException("缺少文件路径");
            var status=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(backend.Request("GET","/api/status","","").Text);
            string boot=Convert.ToString(status["boot"]);
            var begin=FileAction("begin_read",new Dictionary<string,object>{{"path",path}},boot);
            string transfer=Convert.ToString(begin["transfer"]);
            long total=Convert.ToInt64(begin["bytes"],CultureInfo.InvariantCulture),received=0;
            try
            {
                string name=path.Substring(path.LastIndexOf('/')+1);
                byte[] header=Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; filename*=UTF-8''"+Uri.EscapeDataString(name)+"\r\nContent-Length: "+total+"\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n");
                stream.Write(header,0,header.Length);
                byte[] held=new byte[0];
                using(var hash=SHA256.Create())
                {
                    for(;;)
                    {
                        var chunk=FileAction("read",new Dictionary<string,object>{{"transfer",transfer}},boot);
                        if((bool)chunk["done"])
                        {
                            hash.TransformFinalBlock(new byte[0],0,0);
                            var verified=(Dictionary<string,object>)chunk["verified"];
                            string digest=BitConverter.ToString(hash.Hash).Replace("-","").ToLowerInvariant();
                            if(received!=total||digest!=Convert.ToString(verified["sha256"]))throw new InvalidDataException("下载摘要不一致");
                            stream.Write(held,0,held.Length);break;
                        }
                        byte[] data=Convert.FromBase64String(Convert.ToString(chunk["data"]));
                        if(data.Length>2048||received+data.Length>total)throw new InvalidDataException("文件长度无效");
                        hash.TransformBlock(data,0,data.Length,data,0);
                        if(held.Length>0)stream.Write(held,0,held.Length);
                        held=data;received+=data.Length;
                    }
                }
            }
            catch
            {
                try{FileAction("cancel",new Dictionary<string,object>{{"transfer",transfer}},boot);}catch(Exception){}
                throw;
            }
        }
        public void Dispose()
        {
            if(closed)return;
            KeyValuePair<string,string>[] copy;lock(sessions)copy=sessions.ToArray();
            var release=Task.Run(()=>{foreach(var pair in copy)try{Api("session/release",new Dictionary<string,object>{{"client",pair.Key},{"boot",pair.Value}});}catch(Exception){}});
            release.Wait(1400);closed=true;listener.Stop();backend.Dispose();
        }
    }
}
