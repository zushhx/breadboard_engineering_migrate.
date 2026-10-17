using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using BreadboardController;

public sealed class OneShotDevice : IDisposable
{
    private readonly TcpListener listener;
    public readonly Task Work;
    public readonly Uri Address;
    public string Request;
    public OneShotDevice(string body, int status, string headers, int delay)
    {
        listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        Address = new Uri("http://127.0.0.1:" + ((IPEndPoint)listener.LocalEndpoint).Port + "/");
        Work = Task.Run(async delegate
        {
            try
            {
                using (var client = await listener.AcceptTcpClientAsync())
                using (var stream = client.GetStream())
                {
                    client.ReceiveTimeout = 3000;
                    var data = new List<byte>();
                    byte[] buffer = new byte[1024];
                    while (data.Count < 4096)
                    {
                        int count = await stream.ReadAsync(buffer, 0, buffer.Length);
                        if (count == 0) return;
                        data.AddRange(buffer.Take(count));
                        Request = Encoding.ASCII.GetString(data.ToArray());
                        if (Request.Contains("\r\n\r\n")) break;
                    }
                    if (delay > 0) await Task.Delay(delay);
                    byte[] bytes = Encoding.UTF8.GetBytes(body);
                    byte[] head = Encoding.ASCII.GetBytes("HTTP/1.1 " + status + " Test\r\nContent-Type: application/json; charset=utf-8\r\nConnection: close\r\nContent-Length: " + bytes.Length + "\r\n" + headers + "\r\n");
                    await stream.WriteAsync(head, 0, head.Length);
                    await stream.WriteAsync(bytes, 0, bytes.Length);
                }
            }
            catch (IOException) { }
            catch (SocketException) { }
            catch (ObjectDisposedException) { }
        });
    }
    public void Dispose() { listener.Stop(); }
}

public static class CoreTests
{
    private static int assertions;
    private const string Json = "{\"boot\":\"abcdefabcdefabcdefabcdefabcdefab\",\"phase\":\"idle\",\"ready\":true,\"connected\":true,\"card_serial\":\"10044BA5\",\"card_bytes\":\"7948206080\",\"space_valid\":true,\"free_bytes\":\"0\",\"preview\":[[1,2,3]]}";
    private static void Check(bool condition, string name)
    {
        if (!condition) throw new Exception("FAIL: " + name);
        assertions++;
        Console.WriteLine("PASS: " + name);
    }
    private static void Throws(Action action, string name)
    {
        try { action(); } catch (ArgumentException) { Check(true, name); return; }
        throw new Exception("FAIL: " + name);
    }
    private static void BadJson(string json, Uri uri, string name)
    {
        try { StatusParser.Parse(json, uri); }
        catch (Exception e) { if (e is FormatException || e is ArgumentException || e is InvalidOperationException) { Check(true, name); return; } throw; }
        throw new Exception("FAIL: " + name);
    }
    private static async Task Run(string folder)
    {
        var uri = AddressPolicy.Normalize(" 192.168.137.20 ", false);
        Check(uri.AbsoluteUri == "http://192.168.137.20/", "plain IPv4 is normalized");
        Check(AddressPolicy.Normalize("http://172.28.247.119/?ui=eb4aba6#x", false).AbsoluteUri == "http://172.28.247.119/", "old UI link is normalized without query");
        Check(AddressPolicy.Normalize("http://10.1.2.3/api/status", false).AbsolutePath == "/", "status link resolves to root detection page");
        foreach (string bad in new[] { "", "https://192.168.1.1", "http://8.8.8.8", "http://example.com", "file:///C:/test",
            "http://127.0.0.1", "http://192.168.1.1@8.8.8.8", "http://u:p@192.168.1.1", "http://192.168.1.1/arbitrary",
            "http://[::1]/", "javascript:alert(1)", "http://192.168.1.1:65536", "http://192.168.1.1:0" })
            Throws(delegate { AddressPolicy.Normalize(bad, false); }, "reject unsafe or unsupported address: " + bad);
        Check(AddressPolicy.Normalize("127.0.0.1:12345", true).Port == 12345, "loopback is available only to explicit test callers");
        var status = StatusParser.Parse(Json, uri);
        Check(status.Ready && status.SpaceValid && status.FreeBytes == 0, "zero free space remains valid");
        Check(status.CardSerial == "10044BA5" && status.Capacity == 7948206080, "device card identity retained");
        Check(!status.Busy && DeviceInfo.PhaseName("recording") == "采集中", "phase names and busy state");
        Check(StatusParser.Parse(Json.Replace("\"idle\"", "\"recording\""), uri).Busy, "recording is busy");
        foreach (var item in new[] {
            new[] { "[]", "reject non-object JSON" },
            new[] { "not json", "reject invalid JSON" },
            new[] { Json.Replace("abcdefabcdefabcdefabcdefabcdefab", "bad"), "reject missing protocol marker" },
            new[] { Json.Replace("idle", "unknown"), "reject unrecognized firmware phase" },
            new[] { Json.Replace("10044BA5", "BAD"), "reject malformed card identity" },
            new[] { Json.Replace("\"ready\":true", "\"ready\":\"true\""), "reject boolean encoded as string" },
            new[] { Json.Replace("\"7948206080\"", "\"0\""), "reject zero capacity when ready" },
            new[] { Json.Replace("\"free_bytes\":\"0\"", "\"free_bytes\":\"99999999999\""), "reject free space above card capacity" },
            new[] { new String('x', 16385), "reject oversized JSON" } })
            BadJson(item[0], uri, item[1]);
        Check(!StatusParser.Parse(Json.Replace("\"space_valid\":true", "\"space_valid\":false"), uri).SpaceValid, "unknown free space stays unknown");
        Check(!typeof(DeviceInfo).GetFields().Any(f => f.Name.ToLowerInvariant().Contains("boot")), "boot control token is never retained in device model");
        var narrow = new NetworkChoice { Name = "test", Address = IPAddress.Parse("192.168.1.18"), Mask = IPAddress.Parse("255.255.255.240") };
        var choices = narrow.Candidates(null);
        Check(choices.Count == 13 && choices.All(u => int.Parse(u.Host.Split('.')[3]) >= 17 && int.Parse(u.Host.Split('.')[3]) <= 30), "discovery respects narrow /28 and excludes own address");
        var broad = new NetworkChoice { Name = "test", Address = IPAddress.Parse("10.100.175.23"), Mask = IPAddress.Parse("255.255.0.0") };
        choices = broad.Candidates(new Uri("http://172.28.247.119/"));
        Check(choices.Count == 254 && choices.Count(u => u.Host.StartsWith("10.100.175.")) == 253, "large /16 search is capped to local /24 plus saved address");
        Check(choices.Distinct().Count() == choices.Count && choices.All(u => u.Host != "10.100.175.23"), "discovery does not duplicate or query self");
        var small = new NetworkChoice { Name = "test", Address = IPAddress.Parse("192.168.1.20"), Mask = IPAddress.Parse("255.255.255.0") };
        Check(small.Candidates(new Uri("http://192.168.1.10/")).Count == 253, "saved address is de-duplicated");
        using (var device = new OneShotDevice(Json, 200, "", 0))
        {
            var result = await DeviceReader.CheckAsync(device.Address, 1000, CancellationToken.None);
            await device.Work;
            Check(result.Success, "HTTP status check recognizes compatible firmware");
            Check(device.Request.StartsWith("GET /api/status HTTP/1.1\r\n") && !device.Request.Contains("X-Record-Key"), "desktop only sends GET without control token");
        }
        using (var device = new OneShotDevice("{}", 503, "", 0))
        {
            var result = await DeviceReader.CheckAsync(device.Address, 1000, CancellationToken.None);
            Check(!result.Success && result.Error.Contains("503"), "HTTP failure is not reported as connected");
        }
        using (var device = new OneShotDevice("", 302, "Location: http://127.0.0.1:1/\r\n", 0))
        {
            var result = await DeviceReader.CheckAsync(device.Address, 1000, CancellationToken.None);
            Check(!result.Success && result.Error.Contains("302"), "redirect is rejected instead of followed");
        }
        using (var device = new OneShotDevice("<html>login</html>", 200, "", 0))
        {
            Check(!(await DeviceReader.CheckAsync(device.Address, 1000, CancellationToken.None)).Success, "hotspot login page is rejected");
        }
        using (var device = new OneShotDevice(new String('x', 20000), 200, "", 0))
        {
            Check(!(await DeviceReader.CheckAsync(device.Address, 1000, CancellationToken.None)).Success, "HTTP body limit is enforced");
        }
        using (var device = new OneShotDevice(Json, 200, "", 400))
        {
            var result = await DeviceReader.CheckAsync(device.Address, 80, CancellationToken.None);
            Check(!result.Success && result.Error.Contains("超时"), "timeout is surfaced without retry");
        }
        using (var device = new OneShotDevice(Json, 200, "", 400))
        using (var cancel = new CancellationTokenSource(50))
        {
            bool cancelled = false;
            try { await DeviceReader.CheckAsync(device.Address, 1000, cancel.Token); } catch (OperationCanceledException) { cancelled = true; }
            Check(cancelled, "user cancellation propagates");
        }
        using (var device = new OneShotDevice(Json, 200, "", 0))
        {
            var progress = new DirectProgress();
            await DeviceDiscovery.SearchAsync(new List<Uri> { device.Address }, CancellationToken.None, progress);
            Check(progress.Found == 1 && progress.Done == 1, "discovery reports verified device once");
        }
        var settings = new AppSettings { Address = AddressPolicy.Display(uri) };
        string settingsFolder = Path.Combine(folder, "settings-test");
        settings.Save(settingsFolder);
        Check(AppSettings.Load(settingsFolder).Address == settings.Address, "local address settings round-trip");
        settings.Address = "http://10.1.2.3"; settings.Save(settingsFolder);
        Check(AppSettings.Load(settingsFolder).Address == settings.Address, "address settings replace existing file");
        File.WriteAllText(Path.Combine(settingsFolder, "settings.json"), "{broken");
        Check(AppSettings.Load(settingsFolder).Address == null, "corrupt settings recover to defaults");
        await BridgeTests.Run(Check);
        Console.WriteLine("RESULT: " + assertions + " meaningful checks passed. All HTTP servers are loopback mocks; no board, serial, reset or TF access.");
    }
    private sealed class DirectProgress : IProgress<SearchProgress>
    {
        public int Found, Done;
        public void Report(SearchProgress value) { if (value.Found != null) Interlocked.Increment(ref Found); Done = value.Done; }
    }
    public static int Main(string[] args)
    {
        try { Run(args[0]).GetAwaiter().GetResult(); return 0; }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
