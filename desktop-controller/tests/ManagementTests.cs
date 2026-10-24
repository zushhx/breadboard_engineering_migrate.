using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using BreadboardController;

public static class ManagementTests
{
    private const string Boot = "abcdefabcdefabcdefabcdefabcdefab";
    private sealed class Fake : IDeviceBackend
    {
        public string Description { get { return "management mock"; } }
        public readonly List<string> Calls = new List<string>();
        public byte[] Data = Enumerable.Range(0, 5001).Select(i => (byte)(i % 251)).ToArray();
        public bool Corrupt, WrongJob, Reject, UploadFinished, ApplyFailure, WrongAp;
        public Dictionary<string, object> Network;
        public int Position;
        public string Job = "", LastKey, LastBody;
        private object result;
        private MemoryStream upload = new MemoryStream();
        private string Hash(byte[] data) { using (var hash = SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(data)).Replace("-", "").ToLowerInvariant(); }
        private BridgeResponse Reply(object value, int code = 200) { return new BridgeResponse { Code = code, Type = "application/json", Body = Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(value)) }; }
        public BridgeResponse Request(string method, string path, string body, string key)
        {
            Calls.Add(method + " " + path); LastKey = key; LastBody = body;
            if (path == "/api/status") return Reply(ManagementClient.Args("boot", Boot));
            if (path == "/api/v2/capabilities") return Reply(ManagementClient.Args("version", 2, "atomic_file_cancel", true));
            if (path == "/api/v2/network/scan") return Reply(new[] { ManagementClient.Args("ssid", "测试网络", "rssi", -30) });
            if (path == "/api/v2/network/save") { Network = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body); return Reply(ManagementClient.Args("accepted", true), 202); }
            if (path == "/api/v2/network/state") return Reply(ManagementClient.Args("applying", false, "apply_ok", !ApplyFailure, "sta_enabled", Network["sta_enabled"], "ssid", Network["ssid"], "ap_enabled", Network["ap_enabled"], "ap_ssid", Network["ap_ssid"], "ap_password", WrongAp ? "different-test-password" : Network["ap_password"]));
            if (path == "/api/v2/files/state") return Reply(ManagementClient.Args("job", WrongJob ? "other-job" : Job, "running", false, "bytes", Position, "code", Reject ? 409 : 200, "result", Reject ? ManagementClient.Args("error", "正在采集中") : result));
            var args = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
            if (key != Boot || (string)args["boot"] != Boot) return Reply(ManagementClient.Args("error", "bad boot"), 403);
            Job = (string)args["job"];
            switch (path.Substring(path.LastIndexOf('/') + 1))
            {
                case "begin_read": Position = 0; result = ManagementClient.Args("transfer", "read-id", "bytes", Data.Length); break;
                case "read":
                    int count = Math.Min(2048, Data.Length - Position); Position += count;
                    result = count == 0 ? ManagementClient.Args("done", true, "offset", Position, "verified", ManagementClient.Args("bytes", Position, "sha256", Corrupt ? "bad" : Hash(Data))) :
                        ManagementClient.Args("done", false, "offset", Position, "data", Convert.ToBase64String(Data, Position - count, count)); break;
                case "begin_upload": upload = new MemoryStream(); Position = 0; result = ManagementClient.Args("transfer", "upload-id", "bytes", args["bytes"]); break;
                case "chunk":
                    if (Convert.ToInt64(args["offset"]) != upload.Length) throw new Exception("bad upload offset");
                    byte[] buffer = Convert.FromBase64String((string)args["data"]); upload.Write(buffer, 0, buffer.Length); Position = (int)upload.Length;
                    result = ManagementClient.Args("offset", Position); break;
                case "finish": UploadFinished = (string)args["sha256"] == Hash(upload.ToArray()) && upload.ToArray().SequenceEqual(Data); result = ManagementClient.Args("bytes", upload.Length, "sha256", Hash(upload.ToArray())); break;
                case "list": result = ManagementClient.Args("entries", new[] { ManagementClient.Args("name", "中文目录", "directory", true, "bytes", 0) }, "more", true, "next", 16); break;
                default: result = ManagementClient.Args("ok", true); break;
            }
            return Reply(ManagementClient.Args("accepted", true, "job", Job), 202);
        }
        public void Dispose() { upload.Dispose(); }
    }
    public static void Run(Action<bool, string> check, string folder)
    {
        ManagementCancelRaceTests.Run();
        ManagementFilePollTests.Run();
        string root = Path.Combine(folder, "management"); Directory.CreateDirectory(root);
        var fake = new Fake(); var client = new ManagementClient(fake, CancellationToken.None, null); client.Begin();
        var scan = client.Scan(); check(((Dictionary<string, object>)scan[0])["ssid"].ToString() == "测试网络" && fake.LastKey == Boot, "native scan uses current boot and parses Unicode networks");
        var config = ManagementClient.Args("sta_enabled", true, "ap_enabled", true, "ssid", "测试WiFi", "password", "12345678", "ap_ssid", "主控热点", "ap_password", "87654321");
        ManagementClient.ValidateNetwork(config); client.Post("network/save", config);
        check(fake.LastBody.Contains("12345678") && fake.LastKey == Boot, "network save sends explicit configuration with boot guard");
        client.SaveNetwork(config);
        check(fake.Calls.Contains("GET /api/v2/network/state"), "network save confirms applied state");
        fake.ApplyFailure = true; bool applyRejected = false;
        try { client.SaveNetwork(config); } catch (IOException) { applyRejected = true; }
        check(applyRejected, "network apply failure is surfaced"); fake.ApplyFailure = false;
        fake.WrongAp = true; applyRejected = false;
        try { client.SaveNetwork(config); } catch (IOException) { applyRejected = true; }
        check(applyRejected, "AP password readback mismatch is surfaced"); fake.WrongAp = false;
        config["ssid"] = new string('测', 11); bool rejected = false;
        try { ManagementClient.ValidateNetwork(config); } catch (ArgumentException) { rejected = true; }
        check(rejected, "SSID limit is UTF-8 bytes rather than character count");
        check(ManagementClient.Join("/", "中文.bin") == "/中文.bin", "TF root joins Unicode leaf");
        rejected = false; try { ManagementClient.Join("/", "../old"); } catch (ArgumentException) { rejected = true; }
        check(rejected, "TF names reject traversal and separators");
        var list = client.FileAction("list", ManagementClient.Args("path", "/", "offset", 0));
        check(ManagementClient.Flag(list, "more") && ManagementClient.Number(list, "next") == 16, "directory paging metadata retained");
        string source = Path.Combine(root, "source.bin"), output = Path.Combine(root, "download.bin"); File.WriteAllBytes(source, fake.Data);
        client.Upload(source, "/测试.bin"); check(fake.UploadFinished, "multi-chunk upload checks offsets and final SHA-256");
        if (File.Exists(output)) File.Delete(output);
        client.Download("/测试.bin", output, false); check(File.ReadAllBytes(output).SequenceEqual(fake.Data), "native download verifies and publishes exact bytes");
        File.WriteAllText(output, "previous usable file"); fake.Corrupt = true; rejected = false;
        try { client.Download("/bad.bin", output, true); } catch (InvalidDataException) { rejected = true; }
        check(rejected && File.ReadAllText(output) == "previous usable file", "bad download digest preserves existing destination");
        check(Directory.GetFiles(root, "*.partial").Length == 0, "failed download only cleans its own temporary file");
        fake.Corrupt = false; client.Download("/good.bin", output, true); check(File.ReadAllBytes(output).SequenceEqual(fake.Data), "verified download atomically replaces explicitly selected existing file");
        fake.Data = new byte[0]; client.Download("/empty.bin", output, true); check(new FileInfo(output).Length == 0, "empty download validates final digest and size");
        fake.Reject = true; rejected = false; int before = fake.Calls.Count(p => p == "POST /api/v2/files/rename");
        try { client.FileAction("rename", ManagementClient.Args("path", "/a", "destination", "/b")); } catch (IOException e) { rejected = e.Message.Contains("采集"); }
        check(rejected && fake.Calls.Count(p => p == "POST /api/v2/files/rename") == before + 1, "acquisition rejection is shown without replaying mutation");
        fake.Reject = false; fake.WrongJob = true; rejected = false;
        try { client.FileAction("copy", ManagementClient.Args("path", "/a", "destination", "/b")); } catch (IOException) { rejected = true; }
        int cancels = fake.Calls.Count(p => p == "POST /api/v2/files/cancel"); client.CancelOwnedTask();
        check(rejected && fake.Calls.Count(p => p == "POST /api/v2/files/cancel") == cancels, "changed job is not reported complete or cancelled as our task");
        using (var stop = new CancellationTokenSource())
        {
            var cancelled = new ManagementClient(fake, stop.Token, null); stop.Cancel(); before = fake.Calls.Count; rejected = false;
            try { cancelled.Begin(); } catch (OperationCanceledException) { rejected = true; }
            check(rejected && fake.Calls.Count == before, "cancel before start sends no device request");
        }
        fake.WrongJob = false; fake.Data = File.ReadAllBytes(source);
        using (var scanMock = new OneShotDevice("[]", 200, "", 5500))
        using (var net = new NetworkBackend(scanMock.Address))
        {
            var response = net.Request("POST", "/api/v2/network/scan", "{}", Boot);
            check(response.Code == 200 && response.Text == "[]", "Wi-Fi scan tolerates more than the normal five-second request timeout");
        }
        using (var stop = new CancellationTokenSource())
        {
            var cancelled = new ManagementClient(fake, stop.Token, message => { if (fake.Position >= 2048) stop.Cancel(); });
            cancelled.Begin(); rejected = false; cancels = fake.Calls.Count(p => p == "POST /api/v2/files/cancel");
            try { cancelled.Upload(source, "/cancelled.bin"); } catch (OperationCanceledException) { rejected = true; }
            check(rejected && fake.Calls.Count(p => p == "POST /api/v2/files/cancel") == cancels + 1, "cancel during upload releases only this transfer without finishing it");
        }
    }
}

public static class ManagementFilePollTests
{
    private sealed class Fake : IDeviceBackend
    {
        public string Description {get{return "poll sequence mock";}}
        public readonly List<string> Events=new List<string>();
        public int Busy,Posts,Gets;public bool WrongJob;private string job;
        private BridgeResponse Reply(object value,int code=200){return new BridgeResponse {Code=code,Type="application/json",Body=Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(value))};}
        public BridgeResponse Request(string method,string path,string body,string key)
        {
            if(path=="/api/status")return Reply(ManagementClient.Args("boot",new string('a',32)));
            if(path=="/api/v2/capabilities")return Reply(ManagementClient.Args("atomic_file_cancel",true));
            if(method=="POST"){++Posts;Events.Add("post");job=(string)new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(body)["job"];return Reply(ManagementClient.Args("job",job),202);}
            ++Gets;Events.Add("get");return Reply(ManagementClient.Args("job",WrongJob?new string('b',32):job,"running",Busy-->0,"bytes",0,"code",200,"result",ManagementClient.Args("ok",true)));
        }
        public void Dispose(){}
    }
    public static void Run()
    {
        int checks=0;
        foreach(string action in new[]{"chunk","read","copy"})foreach(int busy in new[]{0,2}){
            var fake=new Fake {Busy=busy};var client=new ManagementClient(fake,CancellationToken.None,null);client.Begin();
            client.FilePollWait=ms=>fake.Events.Add("wait:"+ms);
            client.FileAction(action,ManagementClient.Args());
            var expected=new List<string>{"post"};if(action=="copy")expected.Add("wait:100");expected.Add("get");
            for(int i=0;i<busy;i++){expected.Add(action=="copy"?"wait:100":"wait:20");expected.Add("get");}
            if(!fake.Events.SequenceEqual(expected)||fake.Posts!=1)throw new Exception("actual client poll sequence or no-replay contract failed: "+action);++checks;
        }
        {
            var fake=new Fake {WrongJob=true};var client=new ManagementClient(fake,CancellationToken.None,null);client.Begin();client.FilePollWait=ms=>fake.Events.Add("wait:"+ms);
            try{client.FileAction("read",ManagementClient.Args());throw new Exception("wrong job accepted");}catch(IOException){}
            if(fake.Posts!=1||fake.Gets!=1||fake.Events.Any(e=>e.StartsWith("wait:")))throw new Exception("wrong job was replayed or unnecessarily waited");++checks;
        }
        using(var stop=new CancellationTokenSource()){
            var fake=new Fake {Busy=5};var client=new ManagementClient(fake,stop.Token,null);client.Begin();client.FilePollWait=ms=>{fake.Events.Add("wait:"+ms);stop.Cancel();};
            try{client.FileAction("chunk",ManagementClient.Args());throw new Exception("cancellation ignored");}catch(OperationCanceledException){}
            if(fake.Posts!=1||fake.Gets!=1||!fake.Events.SequenceEqual(new[]{"post","get","wait:20"}))throw new Exception("cancelled busy poll continued or replayed");++checks;
        }
        Console.WriteLine("PASS actual ManagementClient file poll event order: "+checks+" checks; no wall-clock thresholds");
    }
}
public static class ManagementCancelRaceTests
{
    private const string Boot="abcdefabcdefabcdefabcdefabcdefab";
    private sealed class Fake : IDeviceBackend
    {
        public string Description { get { return "cancel race mock; no hardware"; } }
        public string Current="", Owned="";
        public int Cancels;
        public bool Interrupt=true, SwapAfterGet, AlreadyChanged, Cancelled;
        public bool RejectSubmit;
        public bool WrongAcceptedJob;
        public int SubmitCode=202;
        public int Capability;
        public Dictionary<string,object> CancelArgs;
        private BridgeResponse Reply(object value,int code=200)
        {return new BridgeResponse {Code=code,Type="application/json",Body=Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(value))};}
        public BridgeResponse Request(string method,string path,string body,string key)
        {
            if(path=="/api/status")return Reply(ManagementClient.Args("boot",Boot));
            if(path=="/api/v2/capabilities") {
                if(Capability==3)throw new IOException("capabilities unavailable");
                if(Capability==1)return Reply(ManagementClient.Args("version",2));
                return Reply(ManagementClient.Args("atomic_file_cancel",Capability==4?(object)"true":(object)(Capability==0)));
            }
            if(path=="/api/v2/files/state")
            {
                if(Interrupt){Interrupt=false;throw new IOException("simulated result-read interruption");}
                if(AlreadyChanged)Current=new string('b',32);
                var reply=Reply(ManagementClient.Args("job",Current,"running",true));
                if(SwapAfterGet)Current=new string('c',32);
                return reply;
            }
            var args=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(body);
            if(path=="/api/v2/files/cancel")
            {
                ++Cancels;CancelArgs=args;
                // This mock evaluates the request contract, not production
                // firmware correctness; the C++ source regression covers that.
                if(!args.ContainsKey("expected_job")||(string)args["expected_job"]!=Current)
                    return Reply(ManagementClient.Args("error","changed job"),409);
                Cancelled=true;return Reply(ManagementClient.Args("accepted",true),202);
            }
            if(RejectSubmit)return Reply(ManagementClient.Args("error","another task"),409);
            Current=Owned=(string)args["job"];
            return Reply(ManagementClient.Args("accepted",true,"job",WrongAcceptedJob?new string('d',32):Current),SubmitCode);
        }
        public void Dispose(){}
    }
    public static void Run()
    {
        int checks=0;
        foreach(int mode in new[]{0,1,2})
        {
            var fake=new Fake();var client=new ManagementClient(fake,CancellationToken.None,null);client.Begin();
            try{client.FileAction("copy",ManagementClient.Args("path","/source","destination","/target"));throw new Exception("missing interruption");}
            catch(IOException){}
            fake.SwapAfterGet=mode==1;fake.AlreadyChanged=mode==2;
            client.CancelOwnedTask();
            if(mode==2){if(fake.Cancels!=0)throw new Exception("pre-observed replacement was cancelled");++checks;continue;}
            if(fake.Cancels!=1||fake.CancelArgs==null||(string)fake.CancelArgs["expected_job"]!=fake.Owned)
                throw new Exception("cleanup must send owned expected_job exactly once");++checks;
            if((string)fake.CancelArgs["transfer"]!="")throw new Exception("transfer contract changed");++checks;
            if(mode==1&&(fake.Cancelled||fake.Current==fake.Owned))throw new Exception("replacement job affected");
            if(mode==0&&!fake.Cancelled)throw new Exception("matching job was not cancelled");++checks;
        }
        foreach(int capability in new[]{1,2,3,4}) {
            var fake=new Fake {Capability=capability};var client=new ManagementClient(fake,CancellationToken.None,null);client.Begin();
            try{client.FileAction("copy",ManagementClient.Args("path","/source","destination","/target"));}catch(IOException){}
            client.CancelOwnedTask();client.CancelOwnedTask();
            if(fake.Cancels!=0)throw new Exception("missing/false/failed/nonboolean capability must not send automatic cancel");++checks;
        }
        {
            var fake=new Fake {RejectSubmit=true};var client=new ManagementClient(fake,CancellationToken.None,null);client.Begin();
            try{client.FileAction("copy",ManagementClient.Args("path","/source","destination","/target"));}catch(IOException){}
            client.CancelOwnedTask();if(fake.Cancels!=0)throw new Exception("rejected submit must not send cleanup cancel");++checks;
        }
        foreach(bool wrongJob in new[]{false,true}) {
            var fake=new Fake {WrongAcceptedJob=wrongJob,SubmitCode=wrongJob?202:200};var client=new ManagementClient(fake,CancellationToken.None,null);client.Begin();
            try{client.FileAction("copy",ManagementClient.Args("path","/source","destination","/target"));}catch(IOException){}
            client.CancelOwnedTask();if(fake.Cancels!=0)throw new Exception("unconfirmed acceptance must not send cleanup cancel");++checks;
        }
        Console.WriteLine("PASS actual ManagementClient cancellation race/capability contract: "+checks+" checks; no hardware");
        return;
    }
}
