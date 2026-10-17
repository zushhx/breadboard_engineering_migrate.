using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace BreadboardController
{
    public static class AddressPolicy
    {
        public static bool IsPrivate(IPAddress ip)
        {
            if (ip.AddressFamily != AddressFamily.InterNetwork) return false;
            byte[] b = ip.GetAddressBytes();
            return b[0] == 10 || (b[0] == 172 && b[1] >= 16 && b[1] <= 31) ||
                (b[0] == 192 && b[1] == 168) || (b[0] == 169 && b[1] == 254);
        }

        public static Uri Normalize(string input, bool allowLoopback)
        {
            string text = (input ?? "").Trim();
            if (text.Length == 0 || text.Length > 256) throw new ArgumentException("请填写主控的局域网 IP 地址。");
            if (!text.Contains("://")) text = "http://" + text;
            Uri uri;
            IPAddress ip;
            if (!Uri.TryCreate(text, UriKind.Absolute, out uri) || uri.Scheme != "http" ||
                !IPAddress.TryParse(uri.Host, out ip) || ip.AddressFamily != AddressFamily.InterNetwork ||
                (!IsPrivate(ip) && !(allowLoopback && IPAddress.IsLoopback(ip))) || uri.UserInfo.Length != 0 || uri.Port < 1 ||
                (uri.AbsolutePath != "/" && uri.AbsolutePath != "/api/status"))
                throw new ArgumentException("请输入主控的局域网 IPv4 地址，例如 http://192.168.137.20；当前固件使用 HTTP。");
            return new UriBuilder("http", ip.ToString(), uri.Port, "/").Uri;
        }

        public static string Display(Uri uri) { return uri.AbsoluteUri.TrimEnd('/'); }
    }

    public sealed class DeviceInfo
    {
        public Uri Address;
        public string Phase, CardSerial;
        public bool Ready, Connected, SpaceValid;
        public ulong Capacity, FreeBytes;
        public DateTime CheckedAt;

        public static string PhaseName(string phase)
        {
            switch (phase)
            {
                case "idle": return "待机";
                case "queued": return "等待开始";
                case "preparing": return "准备采集";
                case "recording": return "采集中";
                case "draining": return "排空缓冲";
                case "closing": return "关闭文件";
                case "verifying": return "校验文件";
                case "passed": return "上一轮已通过校验";
                case "rejected": return "上一请求已拒绝";
                case "failed": return "采集失败／已锁定";
                default: return "未知状态";
            }
        }
        public bool Busy { get { return new[] { "queued", "preparing", "recording", "draining", "closing", "verifying" }.Contains(Phase); } }
        public string DisplayName { get { return AddressPolicy.Display(Address) + "  ·  TF " + CardSerial + "  ·  " + PhaseName(Phase); } }
        public static string Gigabytes(ulong bytes) { return (bytes / 1000000000.0).ToString("0.00", CultureInfo.InvariantCulture) + " GB"; }
    }

    public static class StatusParser
    {
        private static string Str(Dictionary<string, object> data, string key)
        {
            object value;
            if (!data.TryGetValue(key, out value) || !(value is string)) throw new FormatException("缺少状态字段 " + key);
            return (string)value;
        }
        private static bool Bool(Dictionary<string, object> data, string key)
        {
            object value;
            if (!data.TryGetValue(key, out value) || !(value is bool)) throw new FormatException("缺少状态字段 " + key);
            return (bool)value;
        }
        public static DeviceInfo Parse(string json, Uri address)
        {
            if (json == null || Encoding.UTF8.GetByteCount(json) > 16384) throw new FormatException("状态响应过长");
            var parser = new JavaScriptSerializer { MaxJsonLength = 16384, RecursionLimit = 12 };
            var data = parser.DeserializeObject(json) as Dictionary<string, object>;
            if (data == null) throw new FormatException("状态不是 JSON 对象");
            string boot = Str(data, "boot"), serial = Str(data, "card_serial"), phase = Str(data, "phase");
            if (!Regex.IsMatch(boot, "\\A[0-9a-fA-F]{32}\\z") || !Regex.IsMatch(serial, "\\A[0-9a-fA-F]{8}\\z") ||
                !new[] { "idle", "queued", "preparing", "recording", "draining", "closing", "verifying", "passed", "rejected", "failed" }.Contains(phase))
                throw new FormatException("未识别的主控协议");
            ulong capacity;
            if (!UInt64.TryParse(Str(data, "card_bytes"), NumberStyles.None, CultureInfo.InvariantCulture, out capacity) || capacity > (1UL << 50))
                throw new FormatException("卡容量字段无效");
            bool ready = Bool(data, "ready");
            if (ready && capacity == 0) throw new FormatException("就绪状态与卡容量不一致");
            var info = new DeviceInfo { Address = address, Phase = phase, Ready = ready, Connected = Bool(data, "connected"),
                CardSerial = serial.ToUpperInvariant(), Capacity = capacity, CheckedAt = DateTime.Now };
            object space;
            if (data.TryGetValue("space_valid", out space) && space is bool && (bool)space)
            {
                ulong free;
                if (!UInt64.TryParse(Str(data, "free_bytes"), NumberStyles.None, CultureInfo.InvariantCulture, out free) || free > capacity)
                    throw new FormatException("剩余容量字段无效");
                info.SpaceValid = true;
                info.FreeBytes = free;
            }
            // boot is checked to identify the protocol, never retained in settings or logs.
            return info;
        }
    }

    public sealed class CheckResult
    {
        public DeviceInfo Device;
        public string Error;
        public bool Success { get { return Device != null; } }
    }

    public static class DeviceReader
    {
        public static async Task<CheckResult> CheckAsync(Uri address, int timeoutMs, CancellationToken cancel)
        {
            try
            {
                using (var handler = new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false, UseCookies = false, UseDefaultCredentials = false })
                using (var client = new HttpClient(handler))
                {
                    client.Timeout = TimeSpan.FromMilliseconds(timeoutMs);
                    client.MaxResponseContentBufferSize = 16384;
                    client.DefaultRequestHeaders.ConnectionClose = true;
                    client.DefaultRequestHeaders.TryAddWithoutValidation("Cache-Control", "no-store");
                    using (var response = await client.GetAsync(new Uri(address, "api/status"), cancel).ConfigureAwait(false))
                    {
                        if (response.StatusCode != HttpStatusCode.OK)
                            return new CheckResult { Error = "设备返回 HTTP " + ((int)response.StatusCode).ToString(CultureInfo.InvariantCulture) + "，未确认是兼容主控。" };
                        string json = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                        return new CheckResult { Device = StatusParser.Parse(json, address) };
                    }
                }
            }
            catch (OperationCanceledException) { cancel.ThrowIfCancellationRequested(); return new CheckResult { Error = "连接超时。请确认主控已供电，电脑与主控连接同一个可互访的网络。" }; }
            catch (HttpRequestException) { return new CheckResult { Error = "无法连接这个地址。请检查网络、主控供电和 IP；主控地址可能已经变化。" }; }
            catch (Exception error)
            {
                if (error is FormatException || error is ArgumentException || error is InvalidOperationException || error is WebException ||
                    error is OverflowException || error is InvalidCastException)
                    return new CheckResult { Error = "地址有响应，但未取得兼容的主控状态；请核对设备和固件。" };
                throw;
            }
        }
    }

    public sealed class NetworkChoice
    {
        public string Name;
        public IPAddress Address;
        public IPAddress Mask;
        public bool Wireless;
        public override string ToString() { return Name + "  ·  " + Address; }
        public static List<NetworkChoice> List()
        {
            var result = new List<NetworkChoice>();
            foreach (var network in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (network.OperationalStatus != OperationalStatus.Up || network.NetworkInterfaceType == NetworkInterfaceType.Loopback ||
                    network.NetworkInterfaceType == NetworkInterfaceType.Tunnel) continue;
                string name = network.Name;
                if (Regex.IsMatch(name + " " + network.Description, "WSL|Hyper-V|vEthernet|Bluetooth|蓝牙", RegexOptions.IgnoreCase)) continue;
                foreach (var unicast in network.GetIPProperties().UnicastAddresses)
                {
                    if (unicast.Address.AddressFamily != AddressFamily.InterNetwork || !AddressPolicy.IsPrivate(unicast.Address) ||
                        unicast.Address.GetAddressBytes()[0] == 169) continue;
                    result.Add(new NetworkChoice { Name = name, Address = unicast.Address, Mask = unicast.IPv4Mask,
                        Wireless = network.NetworkInterfaceType == NetworkInterfaceType.Wireless80211 });
                }
            }
            return result.OrderByDescending(n => n.Wireless).ThenBy(n => n.Name).ToList();
        }
        public List<Uri> Candidates(Uri saved)
        {
            var result = new List<Uri>();
            if (saved != null && saved.Host != Address.ToString()) result.Add(saved);
            byte[] ip = Address.GetAddressBytes(), mask = Mask.GetAddressBytes();
            // A broad network is deliberately limited to the local /24 slice. A narrower mask is respected.
            byte effective = mask[3];
            int start = ip[3] & effective, end = start | (255 ^ effective);
            for (int host = start + 1; host < end; ++host)
            {
                string candidate = ip[0] + "." + ip[1] + "." + ip[2] + "." + host;
                if (candidate == Address.ToString()) continue;
                var uri = new Uri("http://" + candidate + "/");
                if (!result.Contains(uri)) result.Add(uri);
            }
            return result;
        }
        public string ScopeLabel
        {
            get
            {
                byte[] ip = Address.GetAddressBytes();
                return ip[0] + "." + ip[1] + "." + ip[2] + ".x（最多 254 个地址；也检查已保存地址）";
            }
        }
    }

    public sealed class SearchProgress
    {
        public int Done, Total;
        public DeviceInfo Found;
    }

    public static class DeviceDiscovery
    {
        public static async Task SearchAsync(List<Uri> addresses, CancellationToken cancel, IProgress<SearchProgress> progress)
        {
            int cursor = -1, done = 0;
            var workers = new List<Task>();
            for (int i = 0; i < 4; ++i)
            {
                workers.Add(Task.Run(async delegate
                {
                    while (true)
                    {
                        cancel.ThrowIfCancellationRequested();
                        int index = Interlocked.Increment(ref cursor);
                        if (index >= addresses.Count) return;
                        var result = await DeviceReader.CheckAsync(addresses[index], 700, cancel).ConfigureAwait(false);
                        int count = Interlocked.Increment(ref done);
                        progress.Report(new SearchProgress { Done = count, Total = addresses.Count, Found = result.Device });
                    }
                }, cancel));
            }
            await Task.WhenAll(workers).ConfigureAwait(false);
        }
    }

    public sealed class AppSettings
    {
        public string Address { get; set; }
        public static AppSettings Load(string folder)
        {
            try
            {
                string path = Path.Combine(folder, "settings.json");
                if (!File.Exists(path) || new FileInfo(path).Length > 4096) return new AppSettings();
                var settings = new JavaScriptSerializer().Deserialize<AppSettings>(File.ReadAllText(path, Encoding.UTF8));
                if (settings == null) return new AppSettings();
                if (!String.IsNullOrEmpty(settings.Address)) settings.Address = AddressPolicy.Display(AddressPolicy.Normalize(settings.Address, false));
                return settings;
            }
            catch (Exception error)
            {
                if (error is IOException || error is UnauthorizedAccessException || error is ArgumentException || error is InvalidOperationException)
                    return new AppSettings();
                throw;
            }
        }
        public void Save(string folder)
        {
            Directory.CreateDirectory(folder);
            string path = Path.Combine(folder, "settings.json"), temporary = path + ".tmp";
            File.WriteAllText(temporary, new JavaScriptSerializer().Serialize(this), new UTF8Encoding(false));
            if (File.Exists(path)) File.Replace(temporary, path, null);
            else File.Move(temporary, path);
        }
    }
}
