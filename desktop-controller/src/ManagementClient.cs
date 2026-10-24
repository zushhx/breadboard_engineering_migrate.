using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace BreadboardController
{
    // Uses the same v2 commands as the web UI. No acquisition or transport configuration.
    public sealed class ManagementClient
    {
        private readonly IDeviceBackend backend;
        private readonly Action<string> progress;
        private readonly CancellationToken cancellation;
        private string boot, transfer = "", activeJob = "", lastJob = "";
        private bool atomicFileCancel, lastJobAccepted;
        private int responseCode;
        internal Action<int> FilePollWait = Thread.Sleep;
        public string Boot { get { return boot; } }
        public ManagementClient(IDeviceBackend backend, CancellationToken cancellation, Action<string> progress)
        { this.backend = backend; this.cancellation = cancellation; this.progress = progress; }
        public static Dictionary<string, object> Args(params object[] pairs)
        {
            var result = new Dictionary<string, object>();
            for (int i = 0; i < pairs.Length; i += 2) result.Add((string)pairs[i], pairs[i + 1]);
            return result;
        }
        public static string Str(Dictionary<string, object> data, string name)
        { object value; return data.TryGetValue(name, out value) ? Convert.ToString(value, CultureInfo.InvariantCulture) : ""; }
        public static bool Flag(Dictionary<string, object> data, string name)
        { object value; return data.TryGetValue(name, out value) && value is bool && (bool)value; }
        public static long Number(Dictionary<string, object> data, string name)
        { return Convert.ToInt64(data[name], CultureInfo.InvariantCulture); }
        public static string Join(string parent, string name)
        {
            if (String.IsNullOrWhiteSpace(name) || name == "." || name == ".." || name.IndexOfAny(new[] { '/', '\\', ':', '*', '?', '"', '<', '>', '|' }) >= 0 || name.TrimEnd(' ', '.') != name)
                throw new ArgumentException("名称不能为空，不能包含路径分隔符或保留字符，也不能以空格或点结尾。");
            foreach (char c in name) if (Char.IsControl(c)) throw new ArgumentException("名称不能包含控制字符。");
            return (parent == "/" ? "" : parent.TrimEnd('/')) + "/" + name;
        }
        public void Begin()
        {
            atomicFileCancel = false; lastJobAccepted = false;
            cancellation.ThrowIfCancellationRequested();
            var status = Get("/api/status");
            boot = Str(status, "boot");
            if (!System.Text.RegularExpressions.Regex.IsMatch(boot, "\\A[0-9a-fA-F]{32}\\z")) throw new IOException("设备会话无效，请重新连接。");
            try { atomicFileCancel = Flag(Get("/api/v2/capabilities"), "atomic_file_cancel"); }
            catch (Exception) { /* Older/unavailable capability: do not risk global cleanup cancellation. */ }
        }
        private object Request(string method, string path, Dictionary<string, object> body)
        {
            if (body != null) body["boot"] = boot;
            var serializer = new JavaScriptSerializer();
            var response = backend.Request(method, path, body == null ? "" : serializer.Serialize(body), body == null ? "" : boot);
            responseCode = response.Code;
            object parsed;
            try { parsed = serializer.DeserializeObject(response.Text); }
            catch (ArgumentException) { throw new IOException("主控返回了无效响应，请检查连接；变更请求不会自动重发。"); }
            if (response.Code < 200 || response.Code >= 300)
            {
                var error = parsed as Dictionary<string, object>;
                throw new IOException(error != null && error.ContainsKey("error") ? Str(error, "error") : "主控拒绝请求（HTTP " + response.Code + "）。");
            }
            return parsed;
        }
        public Dictionary<string, object> Get(string path) { return (Dictionary<string, object>)Request("GET", path, null); }
        public Dictionary<string, object> Post(string path, Dictionary<string, object> body)
        { cancellation.ThrowIfCancellationRequested(); return (Dictionary<string, object>)Request("POST", "/api/v2/" + path, body); }
        public void SaveNetwork(Dictionary<string, object> settings)
        {
            ValidateNetwork(settings);
            Post("network/save", settings);
            for (int attempt = 0; attempt < 20; ++attempt)
            {
                cancellation.ThrowIfCancellationRequested();
                Thread.Sleep(250);
                Dictionary<string, object> state;
                try { state = Get("/api/v2/network/state"); }
                catch (Exception) { throw new IOException("配置已保存，应用结果未确认，请通过 USB 重新读取。"); }
                if (Flag(state, "applying")) continue;
                if (!Flag(state, "apply_ok")) throw new IOException("配置已保存，但主控应用失败，请通过 USB 检查。");
                if (Str(state, "ap_password") != Str(settings, "ap_password") || Str(state, "ap_ssid") != Str(settings, "ap_ssid") || Flag(state, "ap_enabled") != Flag(settings, "ap_enabled") || Flag(state, "sta_enabled") != Flag(settings, "sta_enabled") || Str(state, "ssid") != Str(settings, "ssid"))
                    throw new IOException("主控读回配置与提交值不同，请重新读取确认。");
                return;
            }
            throw new IOException("配置已保存，应用结果仍待确认，请通过 USB 重新读取。");
        }
        public object[] Scan()
        { cancellation.ThrowIfCancellationRequested(); return (object[])Request("POST", "/api/v2/network/scan", Args()); }
        public static void ValidateNetwork(Dictionary<string, object> settings)
        {
            string ssid = Str(settings, "ssid"), pass = Str(settings, "password"), ap = Str(settings, "ap_ssid"), apPass = Str(settings, "ap_password");
            int s = Encoding.UTF8.GetByteCount(ssid), p = Encoding.UTF8.GetByteCount(pass), a = Encoding.UTF8.GetByteCount(ap), apP = Encoding.UTF8.GetByteCount(apPass);
            if (s > 32 || (Flag(settings, "sta_enabled") && s == 0) || p > 63 || (p > 0 && p < 8) || a < 1 || a > 32 || apP < 8 || apP > 63)
                throw new ArgumentException("网络名称最多32字节；启用 Wi-Fi 时名称不能为空；密码8–63字节。原 Wi-Fi 名称未变时，密码留空可保留。");
        }
        public Dictionary<string, object> FileAction(string action, Dictionary<string, object> args)
        {
            cancellation.ThrowIfCancellationRequested();
            string job = Guid.NewGuid().ToString("N"); args["job"] = job;
            activeJob = job; lastJob = job; lastJobAccepted = false;
            var accepted = Post("files/" + action, args);
            if (responseCode != 202 || Str(accepted, "job") != job)
                throw new IOException("文件任务接收结果未确认；不会重发或自动取消，请查看设备状态。");
            lastJobAccepted = true;
            var watch = Stopwatch.StartNew();
            bool blockTransfer = action == "chunk" || action == "read";
            if (!blockTransfer) FilePollWait(100);
            while (watch.Elapsed < TimeSpan.FromMinutes(30))
            {
                cancellation.ThrowIfCancellationRequested();
                var state = Get("/api/v2/files/state");
                if (Str(state, "job") != job) throw new IOException("文件任务已变化，结果未确认。请刷新，勿重复变更请求。");
                if (progress != null) progress("主控文件任务：" + Number(state, "bytes").ToString("N0") + " 字节");
                if (!Flag(state, "running"))
                {
                    activeJob = "";
                    var result = (Dictionary<string, object>)state["result"];
                    if (Number(state, "code") >= 400) throw new IOException(Str(result, "error"));
                    return result;
                }
                cancellation.ThrowIfCancellationRequested();
                FilePollWait(blockTransfer ? 20 : 100);
            }
            throw new TimeoutException("文件任务仍未完成，请查看设备状态；不会自动重发。");
        }
        public void CancelOwnedTask()
        {
            // Older firmware ignores expected_job; let its transfer expire instead
            // of converting automatic cleanup into a global cancellation.
            if (!atomicFileCancel || !lastJobAccepted) return;
            if (String.IsNullOrEmpty(boot)) return;
            if (String.IsNullOrEmpty(activeJob) && String.IsNullOrEmpty(transfer)) return;
            try
            {
                // Do not cancel somebody else's job after an uncertain outcome.
                var state = Get("/api/v2/files/state");
                if (Str(state, "job") != lastJob) return;
                if (String.IsNullOrEmpty(activeJob) && String.IsNullOrEmpty(transfer)) return;
                Request("POST", "/api/v2/files/cancel", Args("job", Guid.NewGuid().ToString("N"), "expected_job", lastJob, "transfer", transfer));
            }
            catch (Exception) { /* Device transfer timeout also closes abandoned transfers. */ }
        }
        public void Upload(string local, string destination)
        {
            using (var input = new FileStream(local, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var hash = SHA256.Create())
            {
                if (input.Length > UInt32.MaxValue) throw new IOException("单文件不能超过4GB。");
                var begin = FileAction("begin_upload", Args("path", destination, "bytes", input.Length));
                transfer = Str(begin, "transfer");
                try
                {
                    byte[] buffer = new byte[2048]; long offset = 0; int count;
                    while ((count = input.Read(buffer, 0, buffer.Length)) != 0)
                    {
                        cancellation.ThrowIfCancellationRequested();
                        var reply = FileAction("chunk", Args("transfer", transfer, "offset", offset, "data", Convert.ToBase64String(buffer, 0, count)));
                        hash.TransformBlock(buffer, 0, count, buffer, 0); offset += count;
                        if (Number(reply, "offset") != offset) throw new IOException("上传偏移不一致，未自动重放。");
                        if (progress != null) progress("上传 " + Path.GetFileName(local) + "：" + offset.ToString("N0") + " / " + input.Length.ToString("N0") + " 字节");
                    }
                    hash.TransformFinalBlock(new byte[0], 0, 0);
                    string digest = Hex(hash.Hash);
                    var end = FileAction("finish", Args("transfer", transfer, "sha256", digest));
                    transfer = "";
                    if (Str(end, "sha256") != digest || Number(end, "bytes") != offset) throw new IOException("上传确认摘要不一致，结果未确认，请刷新目录。");
                }
                finally { if (transfer.Length != 0) CancelOwnedTask(); }
            }
        }
        public void Download(string source, string destination, bool replaceExisting)
        {
            string temp = destination + "." + Guid.NewGuid().ToString("N") + ".partial";
            var begin = FileAction("begin_read", Args("path", source));
            transfer = Str(begin, "transfer"); long total = Number(begin, "bytes"), received = 0;
            try
            {
                using (var output = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                using (var hash = SHA256.Create())
                {
                    for (;;)
                    {
                        var chunk = FileAction("read", Args("transfer", transfer));
                        if (Flag(chunk, "done"))
                        {
                            hash.TransformFinalBlock(new byte[0], 0, 0);
                            var verified = (Dictionary<string, object>)chunk["verified"];
                            if (received != total || Number(chunk, "offset") != received || Number(verified, "bytes") != received || Str(verified, "sha256") != Hex(hash.Hash))
                                throw new InvalidDataException("下载长度或SHA-256不一致，未替换目标文件。");
                            transfer = ""; output.Flush(true); break;
                        }
                        byte[] data = Convert.FromBase64String(Str(chunk, "data"));
                        if (data.Length == 0 || data.Length > 2048 || received + data.Length > total || Number(chunk, "offset") != received + data.Length)
                            throw new InvalidDataException("下载分块长度或偏移无效，未替换目标文件。");
                        output.Write(data, 0, data.Length); hash.TransformBlock(data, 0, data.Length, data, 0); received += data.Length;
                        if (progress != null) progress("下载：" + received.ToString("N0") + " / " + total.ToString("N0") + " 字节");
                    }
                }
                cancellation.ThrowIfCancellationRequested();
                if (replaceExisting && File.Exists(destination)) File.Replace(temp, destination, null);
                else File.Move(temp, destination);
            }
            finally
            {
                if (transfer.Length != 0) CancelOwnedTask();
                // Only remove the temporary file this call created, never an existing destination.
                if (File.Exists(temp)) File.Delete(temp);
            }
        }
        private static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
    }
    public sealed class TfEntry
    {
        public string Name { get; set; }
        public string Path { get; set; }
        public bool Directory { get; set; }
        public long Bytes { get; set; }
        public string Kind { get { return Directory ? "文件夹" : "文件"; } }
        public string Size { get { return Directory ? "—" : Bytes.ToString("N0") + " B"; } }
    }
}
