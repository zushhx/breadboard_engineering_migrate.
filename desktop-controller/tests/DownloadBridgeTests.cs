using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using BreadboardController;

public static class DownloadBridgeTests
{
    private const string Boot="abcdefabcdefabcdefabcdefabcdefab";
    private static int checks;
    private static void Check(bool value,string name){if(!value)throw new Exception(name);checks++;Console.WriteLine("PASS "+name);}
    private static string Hash(byte[] data){using(var hash=SHA256.Create())return BitConverter.ToString(hash.ComputeHash(data)).Replace("-","").ToLowerInvariant();}
    private sealed class Fake : IDeviceBackend
    {
        public string Description{get{return "download test";}}
        public string Mode,Job="",LastReadJob="",CancelExpected="",CancelTransfer="";
        public int SubmitCount,ReadCount,CancelCount,Position;
        public bool ReplacementPreserved;
        public readonly byte[] Data;
        private object result;
        public Fake(string mode,int count){Mode=mode;Data=Enumerable.Range(0,count).Select(i=>(byte)(i%251)).ToArray();}
        private BridgeResponse Reply(object value,int code){return new BridgeResponse{Code=code,Type="application/json",Body=Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(value))};}
        private static Dictionary<string,object> Obj(params object[] pairs){var value=new Dictionary<string,object>();for(int i=0;i<pairs.Length;i+=2)value.Add((string)pairs[i],pairs[i+1]);return value;}
        public BridgeResponse Request(string method,string path,string body,string key)
        {
            if(path=="/api/status")return Reply(Obj("boot",Boot),200);
            if(path=="/api/v2/capabilities")return Reply(Mode=="cap-missing"?Obj():Obj("atomic_file_cancel",Mode!="cap-false"),Mode=="cap-fail"?503:200);
            if(path=="/api/v2/files/state")
            {
                if(Mode=="replacement"&&ReadCount==2)Job=new string('b',32);
                int code=Mode=="poll-reject"?409:(Mode=="read-poll-reject"||Mode.StartsWith("cap-"))&&ReadCount==2?500:200;
                return Reply(Obj("job",Job,"running",Mode=="timeout","code",code,"result",result),200);
            }
            var args=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(body);
            if((string)args["boot"]!=Boot)throw new Exception("Request lost current boot");
            if(path=="/api/v2/files/cancel")
            {
                CancelCount++;CancelExpected=(string)args["expected_job"];CancelTransfer=(string)args["transfer"];
                ReplacementPreserved=CancelExpected!=Job;
                return Reply(Obj("error","PRIVATE SSID password file-content"),409);
            }
            SubmitCount++;Job=(string)args["job"];
            if(path.EndsWith("begin_read"))
            {
                if(Mode=="begin-reject")return Reply(Obj("error","PRIVATE SSID password file-content"),409);
                result=Obj("transfer","own-transfer","bytes",Data.Length);
            }
            else
            {
                ReadCount++;LastReadJob=Job;
                if(Mode=="first-read-reject"||(Mode=="second-read-reject"&&ReadCount==2))return Reply(Obj("error","PRIVATE SSID password file-content"),500);
                int remaining=Data.Length-Position;
                int n=(Mode=="short"&&ReadCount==2)?0:Math.Min(2048,remaining);
                if(n==0)result=Obj("done",true,"offset",Position,"verified",Obj("bytes",Position,"sha256",Mode=="bad-sha"?new string('0',64):Hash(Data)));
                else {Position+=n;result=Obj("done",false,"offset",Position,"data",Convert.ToBase64String(Data,Position-n,n));}
            }
            return Reply(Obj("job",Job),202);
        }
        public void Dispose(){}
    }
    private sealed class Response
    {
        public int Code;public long Expected;public byte[] Body;public string Headers;
    }
    private static Response Download(DeviceBridge bridge)
    {
        using(var client=new TcpClient("127.0.0.1",bridge.Address.Port))
        {
            client.ReceiveTimeout=30000;
            using(var stream=client.GetStream())
            {
                string request="GET /api/v2/download?path=%2Fprivate.bin HTTP/1.1\r\nHost: 127.0.0.1:"+bridge.Address.Port+"\r\nConnection: close\r\n\r\n";
                byte[] raw=Encoding.ASCII.GetBytes(request);stream.Write(raw,0,raw.Length);
                using(var all=new MemoryStream())
                {
                    byte[] block=new byte[8192];int n;while((n=stream.Read(block,0,block.Length))!=0)all.Write(block,0,n);
                    raw=all.ToArray();int split=-1;
                    for(int i=0;i+3<raw.Length;i++)if(raw[i]==13&&raw[i+1]==10&&raw[i+2]==13&&raw[i+3]==10){split=i;break;}
                    Check(split>=0,"HTTP response has headers");string headers=Encoding.ASCII.GetString(raw,0,split);
                    string length=headers.Split(new[]{"\r\n"},StringSplitOptions.None).First(s=>s.StartsWith("Content-Length: "));
                    return new Response{Code=Int32.Parse(headers.Split(' ')[1]),Expected=Int64.Parse(length.Substring(16)),Body=raw.Skip(split+4).ToArray(),Headers=headers};
                }
            }
        }
    }
    private static void Case(string mode,int expectedCode,bool truncated,int count)
    {
        var fake=new Fake(mode,count);var logs=new List<string>();
        using(var bridge=new DeviceBridge(fake,line=>logs.Add(line)))
        {
            var watch=Stopwatch.StartNew();
            var response=Download(bridge);
            Check(response.Code==expectedCode,mode+" HTTP status");
            if(truncated)Check(response.Body.Length<response.Expected,mode+" remains incomplete, no appended error JSON");
            else if(expectedCode==200)Check(response.Body.SequenceEqual(fake.Data)&&response.Body.Length==response.Expected,mode+" exact verified bytes");
            else Check(response.Body.Length==response.Expected&&Encoding.UTF8.GetString(response.Body).Contains("stage"),mode+" complete JSON failure");
            if(mode!="good"&&mode!="empty")
            {
                Check(logs.Any(s=>s.Contains("event=download_failed")&&s.Contains("job=")&&s.Contains("stage=")&&s.Contains("code=")),mode+" structured diagnostic");
                Check(!String.Join("\n",logs).Contains("PRIVATE")&&!String.Join("\n",logs).Contains("own-transfer"),mode+" no device text/transfer leak");
                if(expectedCode!=200)Check(!Encoding.UTF8.GetString(response.Body).Contains("PRIVATE"),mode+" HTTP JSON excludes private device error text");
                bool canCancel=mode!="begin-reject"&&mode!="first-read-reject"&&mode!="second-read-reject"&&!mode.StartsWith("cap-");
                Check(fake.CancelCount==(canCancel?1:0),mode+" cleanup requires accepted job and explicit atomic capability");
                if(canCancel)
                {
                    Check(fake.CancelExpected==(fake.LastReadJob.Length==0?fake.Job:fake.LastReadJob),mode+" cancel expected job is failed owned job");
                    if(fake.LastReadJob.Length>0)Check(fake.CancelTransfer=="own-transfer",mode+" cleanup retains own transfer token");
                }
                if(mode=="replacement")Check(fake.ReplacementPreserved,"replacement task is preserved by expected job mismatch");
            }
            if(mode=="good")Check(watch.ElapsedMilliseconds>7000,"long stream has no seven-second total deadline");
            if(mode=="timeout")Check(watch.ElapsedMilliseconds>=7000,"actual per-job seven-second timeout exercised");
            if(mode=="begin-reject")Check(fake.SubmitCount==1,"rejected begin request is never replayed");
            if(mode=="first-read-reject")Check(fake.SubmitCount==2,"rejected first read is never replayed or cancelled");
        }
    }
    public static int Main()
    {
        try
        {
            Case("begin-reject",409,false,5001);
            Case("poll-reject",409,false,5001);
            Case("timeout",504,false,5001);
            Case("first-read-reject",500,false,5001);
            Case("second-read-reject",200,true,5001);
            Case("read-poll-reject",200,true,5001);
            Case("replacement",200,true,5001);
            Case("cap-missing",200,true,5001);
            Case("cap-false",200,true,5001);
            Case("cap-fail",200,true,5001);
            Case("bad-sha",200,true,5001);
            Case("short",200,true,5001);
            Case("empty",200,false,0);
            Case("good",200,false,1048577); // Overall stream exceeds 7s and 512KiB, per-job bound remains 7s.
            Console.WriteLine("DOWNLOAD_BRIDGE_CHECKS="+checks);return 0;
        }
        catch(Exception error){Console.Error.WriteLine(error);return 1;}
    }
}
