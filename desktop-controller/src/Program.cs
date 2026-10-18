using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Microsoft.Win32;

[assembly: AssemblyTitle("主控控制台")]
[assembly: AssemblyDescription("直插式面包板样机桌面连接助手")]
[assembly: AssemblyProduct("主控控制台")]
[assembly: AssemblyVersion("0.3.0.0")]
[assembly: AssemblyFileVersion("0.3.0.0")]

namespace BreadboardController
{
    public sealed class UiCommand : ICommand
    {
        private readonly Func<Task> action;
        private readonly Func<bool> canRun;
        private readonly Action<Exception> error;
        private bool running;
        public UiCommand(Func<Task> action, Func<bool> canRun, Action<Exception> error) { this.action = action; this.canRun = canRun; this.error = error; }
        public event EventHandler CanExecuteChanged { add { CommandManager.RequerySuggested += value; } remove { CommandManager.RequerySuggested -= value; } }
        public bool CanExecute(object parameter) { return !running && (canRun == null || canRun()); }
        public async void Execute(object parameter)
        {
            if (!CanExecute(parameter)) return;
            running = true; CommandManager.InvalidateRequerySuggested();
            try { await action(); }
            catch (Exception exception) { error(exception); }
            finally { running = false; CommandManager.InvalidateRequerySuggested(); }
        }
    }

    public sealed partial class ControllerWindow
    {
        private readonly bool preview;
        private readonly string storage;
        private readonly AppSettings settings;
        private readonly List<string> log = new List<string>();
        private CancellationTokenSource operation;
        private bool working, closed, populating;
        private DeviceInfo lastDevice;
        private DeviceBridge bridge;
        private IDeviceBackend backend;
        private string backendTarget;
        public readonly Window Window;
        private TextBox Address { get { return Find<TextBox>("AddressBox"); } }
        private T Find<T>(string name) where T : class { return Window.FindName(name) as T; }
        private void Text(string name, string value) { Find<TextBlock>(name).Text = value; }

        public ControllerWindow(bool previewMode)
        {
            preview = previewMode;
            storage = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BreadboardController");
            settings = preview ? new AppSettings() : AppSettings.Load(storage);
            using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("MainWindow.xaml"))
                Window = (Window)XamlReader.Load(stream);
            using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("AppIcon.ico"))
            {
                if (stream != null) { var icon = BitmapFrame.Create(stream, BitmapCreateOptions.None, BitmapCacheOption.OnLoad); icon.Freeze(); Window.Icon = icon; }
            }
            Address.Text = settings.Address ?? "http://172.28.247.119";
            Address.TextChanged += delegate
            {
                ResetManagement();
                lastDevice = null;
                if (backend is UsbBackend) { if (bridge != null) bridge.Dispose(); bridge = null; backend = null; }
                Badge("尚未检测", "#E9EDF4", "#526179");
                Text("DeviceSummary", "地址已变更，请重新检测。");
            };
            Bind("RefreshUsb", delegate { RefreshUsb(); return Task.FromResult(0); }, delegate { return !working; });
            Bind("ConnectUsb", ConnectUsbAsync, delegate { return !working; });
            Bind("OpenDeviceNetwork", delegate { return OpenPageAsync("network"); }, delegate { return !working; });
            Bind("SaveAddress", SaveAddressAsync, delegate { return !working; });
            Bind("CheckConnection", CheckAsync, delegate { return !working; });
            Bind("OpenDetection", OpenDetectionAsync, delegate { return !working; });
            Bind("ToggleDiscovery", delegate
            {
                var panel = Find<StackPanel>("DiscoveryPanel");
                panel.Visibility = panel.Visibility == Visibility.Visible ? Visibility.Collapsed : Visibility.Visible;
                if (panel.Visibility == Visibility.Visible) RefreshNetworks();
                return Task.FromResult(0);
            }, delegate { return !working; });
            Bind("RefreshNetworks", delegate { RefreshNetworks(); return Task.FromResult(0); }, delegate { return !working; });
            Bind("SearchDevices", SearchAsync, delegate { return !working && Find<ComboBox>("Networks").SelectedItem != null; });
            Bind("CancelSearch", delegate { if (operation != null) operation.Cancel(); return Task.FromResult(0); }, null);
            Bind("UseDevice", UseDeviceAsync, delegate { return !working && Find<ListBox>("FoundDevices").SelectedItem != null; });
            Bind("OpenWifi", delegate { OpenSystem("ms-settings:network-wifi", "已打开 Windows Wi-Fi 设置，请在系统窗口中连接网络。"); return Task.FromResult(0); }, null);
            Bind("OpenHotspot", delegate { OpenSystem("ms-settings:network-mobilehotspot", "已打开 Windows 移动热点设置；请在系统窗口设置并开启热点。"); return Task.FromResult(0); }, null);
            Bind("RefreshNetworkInfo", delegate { RefreshNetworks(); return Task.FromResult(0); }, delegate { return !working; });
            Bind("ExportLog", ExportAsync, null);
            Bind("CopyDiagnostic", delegate { if (!preview) Clipboard.SetText(Diagnostic()); AddLog("已复制连接诊断。"); return Task.FromResult(0); }, null);
            Bind("OpenLogFolder", delegate
            {
                if (!preview) { Directory.CreateDirectory(storage); Shell(storage); }
                return Task.FromResult(0);
            }, null);
            BindManagement();
            foreach (string page in new[] { "Device", "Network", "Files", "Logs", "Help" })
            {
                string name = page;
                Find<RadioButton>("Nav" + name).Checked += delegate { ShowPage(name); };
            }
            Find<ComboBox>("Networks").SelectionChanged += delegate
            {
                var network = Find<ComboBox>("Networks").SelectedItem as NetworkChoice;
                Text("SearchScope", network == null ? "没有可用于查找的局域网，请先连接 Wi-Fi。" : "查找范围：" + network.ScopeLabel);
                CommandManager.InvalidateRequerySuggested();
            };
            Find<ListBox>("FoundDevices").SelectionChanged += delegate { CommandManager.InvalidateRequerySuggested(); };
            Window.InputBindings.Add(new KeyBinding(Find<Button>("OpenDetection").Command, new KeyGesture(Key.O, ModifierKeys.Control)));
            Window.InputBindings.Add(new KeyBinding(Find<Button>("CheckConnection").Command, new KeyGesture(Key.F5)));
            Window.Closing += delegate
            {
                closed = true;
                if (operation != null) operation.Cancel();
                if (!preview)
                {
                    Uri uri;
                    if (TryAddress(out uri)) Save(uri, false);
                    if (bridge != null) bridge.Dispose();
                    AddLog("程序已关闭，已释放本程序打开的页面会话；失联保护最多10秒发起安全停采。");
                }
            };
            RefreshNetworks();
            RefreshUsb();
            Text("AboutText", "版本：0.3.0 · Windows 本机便携版\n快捷键：Ctrl + O 打开检测页面；F5 检测连接。\n设置与连接日志：" +
                storage + "\n主控 Wi-Fi、热点和 TF 文件可以在本控制台直接管理。密码不写入设置或日志。关闭本程序会断开本程序打开页面的控制会话。");
            AddLog(preview ? "界面预览：离线模拟，不访问实机、不打开外部程序。" : "程序启动。等待用户操作，未查询设备。");
            if (preview) { Window.Title += " · 离线界面预览"; Text("FooterText", "离线界面预览 · 没有实机数据"); }
        }

        private void Bind(string name, Func<Task> action, Func<bool> canRun)
        {
            Find<Button>(name).Command = new UiCommand(action, canRun, HandleError);
        }
        private static void Shell(string target)
        {
            Process.Start(new ProcessStartInfo { FileName = target, UseShellExecute = true });
        }
        private void OpenSystem(string uri, string message)
        {
            if (preview) { Notify("界面预览模式不会打开系统设置。", false); return; }
            Shell(uri);
            AddLog(message);
            Text("FooterText", message);
        }
        private void HandleError(Exception error)
        {
            if (closed) return;
            string message = error is ArgumentException ? error.Message :
                error is System.ComponentModel.Win32Exception ? "Windows 未能打开目标程序。请检查默认浏览器或系统设置是否可用。" :
                error is IOException || error is UnauthorizedAccessException ? "本机文件操作失败。请检查文件是否被占用，以及目标文件夹的访问权限。" :
                "操作未完成。请重新尝试，必要时导出连接记录。";
            Notify(message, true);
            if (Find<FrameworkElement>("FilesPage").IsVisible) Text("TfMessage", message);
            if (Find<FrameworkElement>("NetworkPage").IsVisible) Text("DeviceNetworkMessage", message);
            AddLog(message + " [" + error.GetType().Name + "]");
        }
        private void Badge(string title, string background, string foreground)
        {
            Text("StatusText", preview ? "模拟 · " + title : title);
            Find<Border>("StatusBadge").Background = Brush(background);
            Find<TextBlock>("StatusText").Foreground = Brush(foreground);
        }
        private static SolidColorBrush Brush(string color) { return (SolidColorBrush)new BrushConverter().ConvertFromString(color); }
        private void Notify(string message, bool failure)
        {
            Text("Notice", message);
            Find<Border>("NoticeBorder").Background = Brush(failure ? "#FFF4E8" : "#EFF5FF");
            Find<TextBlock>("Notice").Foreground = Brush(failure ? "#80561B" : "#264873");
            Text("FooterText", failure ? "连接需要处理 · 查看设备连接页" : "就绪 · " + DateTime.Now.ToString("HH:mm:ss"));
        }
        private bool TryAddress(out Uri uri)
        {
            try { uri = AddressPolicy.Normalize(Address.Text, false); return true; }
            catch (ArgumentException error) { uri = null; if (!closed) Notify(error.Message, true); return false; }
        }
        private void Save(Uri uri, bool announce)
        {
            settings.Address = AddressPolicy.Display(uri);
            if (!preview)
            {
                try { settings.Save(storage); }
                catch (Exception error)
                {
                    if (!(error is IOException) && !(error is UnauthorizedAccessException)) throw;
                    Notify("地址可用于本次操作，但保存失败；请检查本机设置文件夹的访问权限。", true);
                    AddLog("地址保存失败 [" + error.GetType().Name + "]");
                    return;
                }
            }
            Text("AddressHint", "已保存：" + settings.Address + " · 下次启动直接使用");
            if (announce) { AddLog("已保存主控地址 " + settings.Address); Notify("地址已保存。可以点击「打开检测页面」或先检测连接。", false); }
        }
        private Task SaveAddressAsync()
        {
            Uri uri;
            if (TryAddress(out uri)) {
                if (backend is UsbBackend) { if (bridge != null) bridge.Dispose(); bridge = null; backend = null; Text("UsbHint", "已切换到 Wi-Fi 地址；需要直连时重新点击连接 USB。"); }
                Save(uri, true);
            }
            return Task.FromResult(0);
        }
        private void BeginWork(string message)
        {
            working = true;
            operation = new CancellationTokenSource();
            Address.IsEnabled = false;
            Find<ComboBox>("Networks").IsEnabled = false;
            ManagementEnabled(false);
            Text("FooterText", message);
            CommandManager.InvalidateRequerySuggested();
        }
        private void EndWork()
        {
            working = false;
            if (operation != null) { operation.Dispose(); operation = null; }
            Address.IsEnabled = true;
            Find<ComboBox>("Networks").IsEnabled = true;
            ManagementEnabled(true);
            Find<Button>("CancelSearch").Visibility = Visibility.Collapsed;
            CommandManager.InvalidateRequerySuggested();
        }
        private void Present(DeviceInfo info)
        {
            lastDevice = info;
            Badge("连接检查通过", "#E7F5EF", "#176248");
            Text("DeviceSummary", String.Format(CultureInfo.InvariantCulture, "检查时间 {0:HH:mm:ss}  ·  {1}\nTF {2}  ·  总容量 {3}{4}",
                info.CheckedAt, DeviceInfo.PhaseName(info.Phase), info.CardSerial, DeviceInfo.Gigabytes(info.Capacity),
                info.SpaceValid ? "  ·  剩余 " + DeviceInfo.Gigabytes(info.FreeBytes) : ""));
            Notify(info.Ready ? "主控已响应。点击「打开检测页面」即可使用原来的采集与监测功能。" :
                "主控已响应，但采集尚未就绪；请打开检测页面查看具体原因。", !info.Ready);
        }
        private async Task<bool> CheckInternalAsync(Uri uri)
        {
            Badge("正在检测", "#EDF3FF", "#245DD6");
            Text("DeviceSummary", "正在读取主控状态，请稍候……");
            AddLog("检测连接 " + AddressPolicy.Display(uri));
            CheckResult result;
            if (preview)
            {
                result = new CheckResult { Device = ExampleDevice(uri) };
            }
            else result = await DeviceReader.CheckAsync(uri, 2800, operation.Token);
            if (closed) return false;
            if (!result.Success)
            {
                lastDevice = null;
                Badge("未连接", "#FFF1E6", "#935215");
                Text("DeviceSummary", "最近一次检查未连通，当前主控状态未知。");
                Notify(result.Error + " 可在「Wi-Fi 与热点」查看连接步骤，或使用「查找主控」。", true);
                AddLog(result.Error);
                return false;
            }
            Present(result.Device);
            AddLog("主控响应：" + DeviceInfo.PhaseName(result.Device.Phase) + "；TF " + result.Device.CardSerial + "。");
            return true;
        }
        private async Task CheckAsync()
        {
            if (backend is UsbBackend && !preview) {
                BeginWork("正在读取 USB 主控状态……");
                try { var response = await Task.Run(() => backend.Request("GET", "/api/status", "", ""));
                    if (response.Code != 200) throw new IOException("主控状态读取失败");
                    if (!closed) Present(StatusParser.Parse(response.Text, bridge.Address));
                } catch (Exception error) { if (!closed) Notify("USB 状态读取失败：" + error.Message, true); }
                finally { EndWork(); }
                return;
            }
            Uri uri;
            if (!TryAddress(out uri)) return;
            Save(uri, false);
            BeginWork("正在检测连接……");
            try { await CheckInternalAsync(uri); }
            catch (OperationCanceledException) { }
            finally { EndWork(); }
        }
        private void RefreshUsb()
        {
            var ports = preview ? new[] { "COM4（模拟）" } : UsbBackend.Ports();
            Find<ComboBox>("UsbPorts").ItemsSource = ports;
            if (ports.Length > 0) Find<ComboBox>("UsbPorts").SelectedIndex = 0;
            Text("UsbHint", ports.Length > 0 ? "选择 ESP32 原生 USB 端口；连接需要主控 v2 固件。" : "未找到 ESP32 原生 USB。请连接带数据功能的 USB 线，再刷新。");
        }
        private async Task ConnectUsbAsync()
        {
            if (preview) { Notify("离线预览：USB 不会打开。", false); return; }
            string name = Find<ComboBox>("UsbPorts").SelectedItem as string;
            if (String.IsNullOrEmpty(name)) { Notify("请选择 ESP32 原生 USB 串口。", true); return; }
            BeginWork("正在连接 USB……");
            IDeviceBackend candidate = null;
            try
            {
                candidate = await Task.Run<IDeviceBackend>(() => new UsbBackend(name));
                await Task.Run(() => ValidateV2(candidate));
                if (closed) return;
                if (bridge != null) bridge.Dispose();
                ResetManagement();
                backend = candidate; candidate = null;
                bridge = new DeviceBridge(backend); backendTarget = name;
                Text("UsbHint", "已连接 " + name + "；不需要电脑连接 Wi-Fi。");
                Text("DeviceSummary", "USB 本机检测网址：" + bridge.Address.AbsoluteUri);
                Badge("USB 已连接", "#E7F5EF", "#176248");
                Notify("USB 已连接。点击「打开检测页面」或进入「Wi-Fi 与热点」配置主控。", false);
            }
            catch (Exception error) { Notify("USB 连接未完成：" + error.Message + "。旧固件须先完成升级。", true); }
            finally { if (candidate != null) candidate.Dispose(); EndWork(); }
        }
        private static void ValidateV2(IDeviceBackend candidate)
        {
            var response = candidate.Request("GET", "/api/v2/capabilities", "", "");
            if (response.Code != 200 || !response.Text.Contains("\"version\":2"))
                throw new IOException("主控未提供 v2 控制接口");
        }
        private Task OpenDetectionAsync() { return OpenPageAsync(""); }
        private async Task EnsureBackendAsync()
        {
            if (backend is UsbBackend) return;
            Uri uri = AddressPolicy.Normalize(Address.Text, false);
            if (bridge == null || backendTarget != uri.AbsoluteUri)
            {
                var candidate = new NetworkBackend(uri);
                try { await Task.Run(() => ValidateV2(candidate)); }
                catch { candidate.Dispose(); throw; }
                if (closed) { candidate.Dispose(); return; }
                if (bridge != null) bridge.Dispose();
                ResetManagement();
                backend = candidate; bridge = new DeviceBridge(backend); backendTarget = uri.AbsoluteUri;
            }
            Save(uri, false);
        }
        private async Task OpenPageAsync(string panel)
        {
            if (preview) { Notify("离线预览：这里会打开统一网页；未访问主控。", false); return; }
            BeginWork("正在准备主控网页……");
            try
            {
                await EnsureBackendAsync();
                if (closed) return;
                string url = bridge.Address.AbsoluteUri + (panel.Length == 0 ? "" : "?panel=" + panel);
                Shell(url);
                Text("DeviceSummary", backend.Description + "\n本机网页：" + bridge.Address.AbsoluteUri);
                Notify("已打开网页。关闭采集页面或本程序会触发安全停采；保存完成时间另计。", false);
                AddLog("已打开统一网页，连接方式：" + backend.Description);
            }
            catch (Exception error) { Notify("无法打开 v2 网页：" + error.Message + "。请检查连接和主控固件版本。", true); }
            finally { EndWork(); }
        }
        private void RefreshNetworks()
        {
            if (populating) return;
            populating = true;
            try
            {
                var networks = preview ? new List<NetworkChoice> { new NetworkChoice { Name = "WLAN（模拟）", Address = System.Net.IPAddress.Parse("192.168.137.1"), Mask = System.Net.IPAddress.Parse("255.255.255.0"), Wireless = true } } : NetworkChoice.List();
                Find<ComboBox>("Networks").ItemsSource = networks;
                if (networks.Count > 0) Find<ComboBox>("Networks").SelectedIndex = 0;
                else Text("SearchScope", "没有找到已连接的局域网，请先连接 Wi-Fi。");
                Text("NetworkInfo", networks.Count == 0 ? "没有可用的局域网 IPv4 地址。请打开 Wi-Fi 设置连接网络后刷新。" :
                    String.Join("\n", networks.Select(n => n.ToString())) + "\n\nIP 信息用于核对当前电脑网络；同一网段并不保证热点允许设备互访。");
            }
            catch (System.Net.NetworkInformation.NetworkInformationException) { Text("NetworkInfo", "无法读取网卡信息，请在 Windows 设置中检查网络。"); }
            finally { populating = false; CommandManager.InvalidateRequerySuggested(); }
        }
        private async Task SearchAsync()
        {
            var network = Find<ComboBox>("Networks").SelectedItem as NetworkChoice;
            if (network == null) return;
            if (bridge != null && !preview && MessageBox.Show(Window, "请先关闭已有的检测页面，再查找主控，避免在采集中增加网络负载。\n\n现在开始查找吗？",
                "查找前确认", MessageBoxButton.YesNo, MessageBoxImage.Information, MessageBoxResult.No) != MessageBoxResult.Yes) return;
            Uri saved = null;
            try { saved = AddressPolicy.Normalize(Address.Text, false); } catch (ArgumentException) { }
            var candidates = network.Candidates(saved);
            var found = new List<DeviceInfo>();
            Find<ListBox>("FoundDevices").ItemsSource = null;
            Find<ListBox>("FoundDevices").Visibility = Visibility.Collapsed;
            Find<Button>("UseDevice").Visibility = Visibility.Collapsed;
            Find<ProgressBar>("SearchBar").Value = 0;
            BeginWork("正在查找局域网主控……");
            var searchOperation = operation;
            Find<Button>("CancelSearch").Visibility = Visibility.Visible;
            Text("SearchStatus", "正在查找，最多约一分钟；可以随时取消。");
            AddLog("开始查找：" + network.ScopeLabel + "，候选地址 " + candidates.Count + " 个。");
            IProgress<SearchProgress> progress = new Progress<SearchProgress>(p =>
            {
                if (closed || !working || !Object.ReferenceEquals(operation, searchOperation)) return;
                Find<ProgressBar>("SearchBar").Value = p.Total == 0 ? 0 : 100.0 * p.Done / p.Total;
                Text("SearchStatus", "已检查 " + p.Done + " / " + p.Total + "，找到 " + (found.Count + (p.Found == null ? 0 : 1)) + " 台兼容主控。");
                if (p.Found != null)
                {
                    found.Add(p.Found);
                    Find<ListBox>("FoundDevices").ItemsSource = null;
                    Find<ListBox>("FoundDevices").ItemsSource = found.ToArray();
                    Find<ListBox>("FoundDevices").SelectedIndex = 0;
                    Find<ListBox>("FoundDevices").Visibility = Visibility.Visible;
                    Find<Button>("UseDevice").Visibility = Visibility.Visible;
                    AddLog("发现兼容主控 " + p.Found.DisplayName);
                }
            });
            try
            {
                if (preview)
                {
                    progress.Report(new SearchProgress { Done = candidates.Count, Total = candidates.Count, Found = ExampleDevice(new Uri("http://192.168.137.20/")) });
                    await Task.Delay(50);
                }
                else await DeviceDiscovery.SearchAsync(candidates, operation.Token, progress);
                // Progress<T> posts to the UI context; process its last queued updates before reporting completion.
                await System.Windows.Threading.Dispatcher.Yield(System.Windows.Threading.DispatcherPriority.Background);
                Text("SearchStatus", found.Count == 0 ? "没有找到。请核对网络，或从热点设备列表获取 IP 后手动填写。" : "查找完成，找到 " + found.Count + " 台；选中后点击「使用选中地址」。");
                AddLog("查找完成，发现 " + found.Count + " 台兼容主控；结果只覆盖本次已检查地址。");
            }
            catch (OperationCanceledException) { if (!closed) { Text("SearchStatus", "查找已取消。已找到的结果仍可选择。"); AddLog("查找已取消。"); } }
            finally { EndWork(); if (!closed) Text("FooterText", "就绪 · 查找结束"); }
        }
        private Task UseDeviceAsync()
        {
            var device = Find<ListBox>("FoundDevices").SelectedItem as DeviceInfo;
            if (device != null)
            {
                Address.Text = AddressPolicy.Display(device.Address);
                Save(device.Address, false);
                Present(device);
                AddLog("已选择主控 " + device.DisplayName);
                Find<ScrollViewer>("DevicePage").ScrollToTop();
            }
            return Task.FromResult(0);
        }
        private string Diagnostic()
        {
            Uri uri;
            string target = TryAddress(out uri) ? AddressPolicy.Display(uri) : "无效地址";
            return "主控控制台 0.3.0\n生成时间：" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss zzz") + "\n主控地址：" + target +
                "\n最近检查：" + Find<TextBlock>("DeviceSummary").Text + "\n电脑网络：" + Find<TextBlock>("NetworkInfo").Text +
                "\n说明：程序只做按需连接检查，采集与图表在原检测页面。\n\n本次记录：\n" + String.Join("\n", log);
        }
        private Task ExportAsync()
        {
            if (preview) return Task.FromResult(0);
            var dialog = new SaveFileDialog { Title = "导出本次连接记录", Filter = "文本文件 (*.txt)|*.txt",
                FileName = "主控连接记录_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".txt" };
            if (dialog.ShowDialog(Window) == true)
            {
                File.WriteAllText(dialog.FileName, Diagnostic(), new UTF8Encoding(true));
                AddLog("本次连接记录已导出。");
            }
            return Task.FromResult(0);
        }
        private void AddLog(string message)
        {
            string line = "[" + DateTime.Now.ToString("HH:mm:ss") + "] " + message;
            log.Add(line);
            if (log.Count > 400) log.RemoveAt(0);
            Find<TextBox>("LogText").Text = String.Join(Environment.NewLine, log);
            Find<TextBox>("LogText").ScrollToEnd();
            if (preview) return;
            try
            {
                Directory.CreateDirectory(storage);
                string path = Path.Combine(storage, "connection.log");
                if (File.Exists(path) && new FileInfo(path).Length > 1024 * 1024)
                {
                    string previous = Path.Combine(storage, "connection.previous.log");
                    if (File.Exists(previous)) File.Delete(previous);
                    File.Move(path, previous);
                }
                File.AppendAllText(path, DateTime.Now.ToString("yyyy-MM-dd ") + line + Environment.NewLine, new UTF8Encoding(false));
            }
            catch (IOException) { Text("FooterText", "本次记录保留在窗口中；日志文件暂时无法写入"); }
            catch (UnauthorizedAccessException) { Text("FooterText", "本次记录保留在窗口中；无权写入日志文件夹"); }
        }
        public void ShowPage(string page)
        {
            foreach (string name in new[] { "Device", "Network", "Files", "Logs", "Help" })
                Find<FrameworkElement>(name + "Page").Visibility = name == page ? Visibility.Visible : Visibility.Collapsed;
            string title = page == "Device" ? "设备连接" : page == "Network" ? "主控 Wi-Fi 与热点" : page == "Files" ? "TF 文件" : page == "Logs" ? "连接记录" : "使用说明";
            Text("PageTitle", title);
            Text("PageSubtitle", page == "Device" ? "连接主控，打开熟悉的检测页面。" : page == "Network" ? "直接设置主控接入的 Wi-Fi 和主控自身热点。" : page == "Files" ? "在控制台管理主控 TF 卡里的文件。" :
                page == "Logs" ? "查看和导出本次连接过程，方便自己排查。" : "以后直接从桌面操作，无需通过 Codex。");
            Find<RadioButton>("Nav" + page).IsChecked = true;
        }
        private static DeviceInfo ExampleDevice(Uri uri)
        {
            return new DeviceInfo { Address = uri, CardSerial = "10044BA5", Phase = "idle", Ready = true, Connected = true,
                Capacity = 7948206080, SpaceValid = true, FreeBytes = 6774000000, CheckedAt = DateTime.Now };
        }
        private void Capture(string path)
        {
            Window.UpdateLayout();
            var content = (FrameworkElement)Window.Content;
            var bitmap = new RenderTargetBitmap((int)Math.Ceiling(content.ActualWidth), (int)Math.Ceiling(content.ActualHeight), 96, 96, PixelFormats.Pbgra32);
            bitmap.Render(content);
            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using (var output = File.Create(path)) encoder.Save(output);
        }
        public async Task PreviewAsync(string folder)
        {
            Directory.CreateDirectory(folder);
            var checks = new List<string>();
            Action<bool, string> verify = (passed, message) =>
            {
                if (!passed) throw new InvalidOperationException("UI smoke check failed: " + message);
                checks.Add("PASS: " + message);
            };
            await Task.Delay(250);
            verify(lastDevice == null && bridge == null && !working, "startup does not query or open a device");
            verify(Window.InputBindings.Count == 2, "keyboard shortcuts are wired");
            Badge("尚未检测", "#E9EDF4", "#526179");
            Notify("离线界面预览 · 本页没有实机数据。", false);
            Capture(Path.Combine(folder, "01-device-offline-simulated.png"));
            Address.Text = "https://192.168.137.20";
            await SaveAddressAsync();
            verify(Find<TextBlock>("Notice").Text.Contains("HTTP") && settings.Address == null, "invalid address is explained and not saved");
            Address.Text = "http://192.168.137.20";
            await SaveAddressAsync();
            verify(settings.Address == "http://192.168.137.20", "address save command");
            BeginWork("模拟操作");
            verify(!Find<Button>("OpenDetection").Command.CanExecute(null) && !Address.IsEnabled, "repeat operations are disabled while working");
            EndWork();
            await CheckAsync();
            verify(lastDevice != null && lastDevice.Ready && !working, "connection check command renders device snapshot");
            await OpenDetectionAsync();
            verify(bridge == null && Find<TextBlock>("Notice").Text.Contains("未访问主控"), "preview open command does not launch a browser");
            Notify("离线界面预览 · 以下连接信息为模拟数据。", false);
            Capture(Path.Combine(folder, "02-device-connected-simulated.png"));
            ShowPage("Network"); await Task.Delay(50);
            verify(Find<FrameworkElement>("NetworkPage").IsVisible && !Find<FrameworkElement>("DevicePage").IsVisible, "network navigation");
            Capture(Path.Combine(folder, "03-network-simulated.png"));
            verify(Find<Button>("SaveDeviceNetwork").Command != null && Find<PasswordBox>("DeviceApPassword") != null, "native network form and save are wired");
            await ReadNetworkAsync();
            verify(Find<TextBlock>("DeviceNetworkMessage").Text.Contains("未向主控"), "native management preview sends no requests");
            ShowPage("Files"); await Task.Delay(50);
            verify(Find<FrameworkElement>("FilesPage").IsVisible && !Find<FrameworkElement>("NetworkPage").IsVisible, "native TF navigation");
            Find<DataGrid>("TfGrid").ItemsSource = new[] { new TfEntry { Name = "中文记录.bin（模拟）", Bytes = 5001, Path = "/中文记录.bin" }, new TfEntry { Name = "REC_示例（模拟）", Directory = true, Path = "/REC_示例" } };
            Find<DataGrid>("TfGrid").SelectedIndex = 0;
            verify(Find<Button>("DownloadTf").Command.CanExecute(null) && Find<Button>("DeleteTf").Command.CanExecute(null), "file selection enables download and guarded delete");
            BeginWork("模拟文件任务");
            verify(!Find<Button>("ConnectUsb").Command.CanExecute(null) && !Find<Button>("SaveDeviceNetwork").Command.CanExecute(null) && !Find<DataGrid>("TfGrid").IsEnabled, "management task prevents backend switching and overlapping changes");
            EndWork();
            Capture(Path.Combine(folder, "08-files-simulated.png"));
            Window.Width = 1000; Window.Height = 700; await Task.Delay(50);
            verify(Find<DataGrid>("TfGrid").ActualHeight > 70 && Find<Button>("CancelTf").IsVisible, "TF list and cancel remain available at minimum size");
            Capture(Path.Combine(folder, "09-files-minimum-simulated.png"));
            Window.Width = 1160; Window.Height = 820;
            ShowPage("Logs"); await Task.Delay(50);
            verify(Find<TextBox>("LogText").Text.Contains("离线模拟"), "logs identify simulated source");
            Capture(Path.Combine(folder, "04-logs-simulated.png"));
            ShowPage("Help"); await Task.Delay(50);
            verify(Find<FrameworkElement>("HelpPage").IsVisible, "help navigation");
            Capture(Path.Combine(folder, "05-help-simulated.png"));
            ShowPage("Device");
            Find<StackPanel>("DiscoveryPanel").Visibility = Visibility.Visible;
            await SearchAsync();
            verify(Find<ListBox>("FoundDevices").Items.Count == 1 && !working, "discovery reports a simulated compatible device");
            await UseDeviceAsync();
            verify(lastDevice != null && Address.Text == "http://192.168.137.20", "selected discovery result fills and saves address");
            Find<ScrollViewer>("DevicePage").ScrollToBottom();
            await Task.Delay(50); Capture(Path.Combine(folder, "06-discovery-simulated.png"));
            Window.Width = 1000; Window.Height = 700;
            Find<ScrollViewer>("DevicePage").ScrollToTop();
            await Task.Delay(100); Capture(Path.Combine(folder, "07-minimum-simulated.png"));
            verify(Find<Button>("OpenDetection").ActualWidth >= 202 && Address.ActualWidth > 300, "primary controls fit minimum desktop size");
            File.WriteAllText(Path.Combine(folder, "preview-result.txt"), String.Join("\n", checks) +
                "\nRESULT: " + checks.Count + " UI smoke checks, nine screenshots. No HTTP requests, shell launches, settings writes or real device data.\n", new UTF8Encoding(false));
            Window.Close();
        }
    }

    public static class Program
    {
        [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
        [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);
        [STAThread]
        public static int Main(string[] args)
        {
            bool preview = args.Length == 2 && args[0] == "--preview";
            if (args.Length != 0 && !preview) { MessageBox.Show("支持的参数：--preview <输出文件夹>。正常使用请直接双击程序。", "主控控制台"); return 2; }
            bool created;
            using (var mutex = new Mutex(true, "Local\\BreadboardControllerV2" + (preview ? "-preview" : ""), out created))
            {
                if (!created)
                {
                    foreach (var process in Process.GetProcessesByName(Process.GetCurrentProcess().ProcessName))
                    {
                        if (process.Id == Process.GetCurrentProcess().Id || process.MainWindowHandle == IntPtr.Zero || process.MainWindowTitle != "主控控制台") continue;
                        ShowWindow(process.MainWindowHandle, 9);
                        SetForegroundWindow(process.MainWindowHandle);
                        return 0;
                    }
                    MessageBox.Show("主控控制台已经在运行，请从任务栏打开。", "主控控制台");
                    return 0;
                }
                try
                {
                    var app = new Application();
                    var window = new ControllerWindow(preview);
                    app.DispatcherUnhandledException += delegate(object sender, System.Windows.Threading.DispatcherUnhandledExceptionEventArgs e)
                    {
                        MessageBox.Show(window.Window, "操作未完成：" + e.Exception.GetType().Name + "。\n请在主控网页检查当前状态；失联后会触发安全停采。", "主控控制台", MessageBoxButton.OK, MessageBoxImage.Warning);
                        e.Handled = true;
                    };
                    if (preview) window.Window.Loaded += async delegate
                    {
                        try { await window.PreviewAsync(args[1]); }
                        catch (Exception error) { File.WriteAllText(Path.Combine(args[1], "preview-error.txt"), error.ToString()); window.Window.Close(); }
                    };
                    return app.Run(window.Window);
                }
                catch (Exception error)
                {
                    if (preview)
                    {
                        Directory.CreateDirectory(args[1]);
                        File.WriteAllText(Path.Combine(args[1], "preview-error.txt"), error.ToString());
                    }
                    else MessageBox.Show("程序未能启动：" + error.GetType().Name + "。\n请确认 Windows 的 .NET Framework 4.8 组件可用。", "主控控制台", MessageBoxButton.OK, MessageBoxImage.Error);
                    return 1;
                }
                finally { mutex.ReleaseMutex(); }
            }
        }
    }
}
