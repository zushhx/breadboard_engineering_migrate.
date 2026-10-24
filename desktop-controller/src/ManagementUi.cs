using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using Microsoft.Win32;

namespace BreadboardController
{
    public sealed partial class ControllerWindow
    {
        private string tfPath = "/", tfCopy = "", networkReadTarget, networkReadBoot, tfReadTarget, tfReadBoot;
        private int tfOffset;
        private bool tfMore, managing;
        private readonly List<TfEntry> tfEntries = new List<TfEntry>();
        private void BindManagement()
        {
            Bind("ReadDeviceNetwork", ReadNetworkAsync, () => !working);
            Bind("ScanDeviceWifi", ScanWifiAsync, () => !working);
            Bind("SaveDeviceNetwork", SaveNetworkAsync, () => !working);
            Bind("ViewApPassword", delegate { if (!preview) MessageBox.Show(Window, Find<PasswordBox>("DeviceApPassword").Password, "主控热点密码", MessageBoxButton.OK, MessageBoxImage.Information); return Task.FromResult(0); }, () => !working);
            Bind("CopyDeviceUrls", delegate { if (!preview) Clipboard.SetText(Find<TextBox>("DeviceUrls").Text); return Task.FromResult(0); }, () => !working);
            Bind("RefreshTf", () => ReadFilesAsync(false), () => !working);
            Bind("NextTf", () => ReadFilesAsync(true), () => !working && tfMore);
            Bind("ParentTf", delegate { Find<TextBox>("TfPath").Text = tfPath.Substring(0, Math.Max(0, tfPath.LastIndexOf('/'))); return ReadFilesAsync(false); }, () => !working);
            Bind("NewTfFolder", NewFolderAsync, () => !working && TfReady);
            Bind("RenameTf", RenameTfAsync, () => !working && TfReady && SelectedTf != null);
            Bind("CopyTf", delegate { tfCopy = SelectedTf.Path; if (!preview) Clipboard.SetText(tfCopy); Text("TfMessage", "已复制 " + tfCopy + "，进入目标目录后粘贴。源文件保留。"); return Task.FromResult(0); }, () => !working && TfReady && SelectedTf != null);
            Bind("PasteTf", PasteTfAsync, () => !working && TfReady);
            Bind("UploadTf", UploadTfAsync, () => !working && TfReady);
            Bind("DownloadTf", DownloadTfAsync, () => !working && TfReady && SelectedTf != null && !SelectedTf.Directory);
            Bind("DeleteTf", DeleteTfAsync, () => !working && TfReady && SelectedTf != null);
            Bind("CancelTf", delegate { if (managing && operation != null) operation.Cancel(); Text("TfMessage", "已请求取消；请等待当前请求结束。未完成上传可能保留临时文件。"); return Task.FromResult(0); }, () => managing && operation != null && !operation.IsCancellationRequested);
            Find<ComboBox>("DeviceWifiChoices").SelectionChanged += delegate
            {
                var choice = Find<ComboBox>("DeviceWifiChoices").SelectedItem as WifiChoice;
                if (choice != null) Find<TextBox>("DeviceSsid").Text = choice.Ssid;
            };
            var grid = Find<DataGrid>("TfGrid");
            Find<TextBox>("TfPath").TextChanged += delegate { if (!TfReady) { grid.SelectedItem = null; tfMore = false; } CommandManager.InvalidateRequerySuggested(); };
            grid.SelectionChanged += delegate { CommandManager.InvalidateRequerySuggested(); };
            grid.MouseDoubleClick += async delegate { if (!working && SelectedTf != null && SelectedTf.Directory) { Find<TextBox>("TfPath").Text = SelectedTf.Path; await ReadFilesAsync(false); } };
            grid.PreviewKeyDown += async (sender, e) =>
            {
                if (working || !TfReady) return;
                try
                {
                if (e.Key == Key.Enter && SelectedTf != null && SelectedTf.Directory) { e.Handled = true; Find<TextBox>("TfPath").Text = SelectedTf.Path; await ReadFilesAsync(false); }
                else if (e.Key == Key.C && Keyboard.Modifiers == ModifierKeys.Control && SelectedTf != null) { e.Handled = true; tfCopy = SelectedTf.Path; if (!preview) Clipboard.SetText(tfCopy); Text("TfMessage", "已复制 " + tfCopy + "，进入目标目录后粘贴。"); }
                else if (e.Key == Key.V && Keyboard.Modifiers == ModifierKeys.Control) { e.Handled = true; await PasteTfAsync(); }
                }
                catch (Exception error) { Text("TfMessage", error.Message); }
            };
        }
        private TfEntry SelectedTf { get { return Find<DataGrid>("TfGrid").SelectedItem as TfEntry; } }
        private bool TfReady { get { return preview || (tfReadTarget != null && tfReadTarget == backendTarget && Find<TextBox>("TfPath").Text == tfPath); } }
        private void RequireTfDirectory(ManagementClient client)
        {
            if (!TfReady || tfReadBoot != client.Boot) throw new IOException("主控或目录已变化，请先重新读取目录，再进行文件操作。");
        }
        private void ResetManagement()
        {
            networkReadTarget = networkReadBoot = tfReadTarget = tfReadBoot = null;
            tfCopy = ""; tfMore = false; tfEntries.Clear();
            Find<DataGrid>("TfGrid").ItemsSource = null;
            Find<PasswordBox>("DeviceWifiPassword").Password = "";
            Find<PasswordBox>("DeviceApPassword").Password = "";
            Find<TextBox>("DeviceUrls").Text = "连接已变化，请重新读取主控设置。";
            Text("DeviceNetworkMessage", "连接已变化，请重新读取主控设置。");
            Text("TfMessage", "连接已变化，请重新读取 TF 目录。");
        }
        private void ManagementEnabled(bool enabled)
        {
            Find<FrameworkElement>("DeviceNetworkForm").IsEnabled = enabled;
            Find<FrameworkElement>("TfPath").IsEnabled = enabled;
            Find<FrameworkElement>("TfGrid").IsEnabled = enabled;
        }
        private async Task ManageAsync(string statusName, Func<ManagementClient, Task> action)
        {
            if (preview) { Text(statusName, "离线预览：未向主控发送任何请求。"); return; }
            BeginWork("正在处理主控请求……"); managing = true;
            ManagementClient client = null;
            try
            {
                Text(statusName, "正在连接主控……");
                await EnsureBackendAsync(); if (closed) return;
                client = new ManagementClient(backend, operation.Token, message => Window.Dispatcher.BeginInvoke(new Action(() => { if (!closed) Text(statusName, message); })));
                await Task.Run(() => client.Begin());
                await action(client);
            }
            catch (OperationCanceledException) { if (!closed) Text(statusName, "已取消。未完成上传可能保留临时文件，请刷新确认结果。"); }
            catch (Exception error) { if (!closed) Text(statusName, error.Message + "\n结果不明时先刷新确认；不会自动重发变更请求。"); }
            if (client != null) await Task.Run(() => client.CancelOwnedTask());
            managing = false;
            if (!closed) EndWork();
        }
        private Task ReadNetworkAsync()
        {
            return ManageAsync("DeviceNetworkMessage", async client =>
            {
                var n = await Task.Run(() => client.Get("/api/v2/network/state"));
                Find<CheckBox>("DeviceStaEnabled").IsChecked = ManagementClient.Flag(n, "sta_enabled");
                Find<CheckBox>("DeviceApEnabled").IsChecked = ManagementClient.Flag(n, "ap_enabled");
                Find<TextBox>("DeviceSsid").Text = ManagementClient.Str(n, "ssid");
                Find<PasswordBox>("DeviceWifiPassword").Password = "";
                Find<TextBox>("DeviceApSsid").Text = ManagementClient.Str(n, "ap_ssid");
                Find<PasswordBox>("DeviceApPassword").Password = ManagementClient.Str(n, "ap_password");
                networkReadTarget = backendTarget;
                networkReadBoot = client.Boot;
                string wifiIp = ManagementClient.Str(n, "sta_ip"), apIp = ManagementClient.Str(n, "ap_ip");
                Find<TextBox>("DeviceUrls").Text = "本电脑入口：" + bridge.Address.AbsoluteUri +
                    (wifiIp.Length > 0 ? "\nWi-Fi 检测页：http://" + wifiIp + "/" : "") +
                    (ManagementClient.Flag(n, "ap_enabled") ? "\n主控热点检测页：http://" + apIp + "/" : "");
                Text("DeviceNetworkMessage", ManagementClient.Flag(n, "applying") ? "配置正在应用，请稍后重新读取。" : !ManagementClient.Flag(n, "apply_ok") ? "主控网络未启动成功，请检查配置。" :
                    ManagementClient.Flag(n, "connected") ? "主控 Wi-Fi 已连接：" + wifiIp : "主控 Wi-Fi 尚未连接。可通过 USB 配置。" + (ManagementClient.Flag(n, "ap_enabled") ? "主控热点已开启。" : ""));
            });
        }
        private Task ScanWifiAsync()
        {
            return ManageAsync("DeviceNetworkMessage", async client =>
            {
                var values = await Task.Run(() => client.Scan());
                Find<ComboBox>("DeviceWifiChoices").ItemsSource = values.Cast<Dictionary<string, object>>().Select(n => new WifiChoice { Ssid = ManagementClient.Str(n, "ssid"), Rssi = ManagementClient.Str(n, "rssi") }).Where(n => n.Ssid.Length > 0).ToList();
                Text("DeviceNetworkMessage", "扫描完成。选择网络，输入密码，勾选启用 Wi-Fi 后保存。隐藏网络可手填名称。");
            });
        }
        private Task SaveNetworkAsync()
        {
            var values = ManagementClient.Args("sta_enabled", Find<CheckBox>("DeviceStaEnabled").IsChecked == true, "ap_enabled", Find<CheckBox>("DeviceApEnabled").IsChecked == true,
                "ssid", Find<TextBox>("DeviceSsid").Text, "password", Find<PasswordBox>("DeviceWifiPassword").Password,
                "ap_ssid", Find<TextBox>("DeviceApSsid").Text, "ap_password", Find<PasswordBox>("DeviceApPassword").Password);
            try { ManagementClient.ValidateNetwork(values); }
            catch (ArgumentException e) { Text("DeviceNetworkMessage", e.Message); return Task.FromResult(0); }
            return ManageAsync("DeviceNetworkMessage", async client =>
            {
                if (networkReadTarget != backendTarget || networkReadBoot != client.Boot) throw new IOException("请先读取当前主控设置，再修改并保存。");
                // Network change may disconnect LAN. Only a deliberate user click reaches here.
                await Task.Run(() => client.SaveNetwork(values));
                Find<PasswordBox>("DeviceWifiPassword").Password = "";
                Find<TextBox>("DeviceUrls").Text = "网络配置已变化，请重新读取实际地址。USB 本电脑入口：" + bridge.Address.AbsoluteUri;
                Text("DeviceNetworkMessage", "配置已保存并已确认主控应用完成。请用新密码重新连接热点；Windows 已保存的旧密码需更新。密码未保存在电脑。");
            });
        }
        private Task ReadFilesAsync(bool append)
        {
            string path = Find<TextBox>("TfPath").Text.Trim(); if (path.Length == 0) path = "/";
            if (!path.StartsWith("/") || path.Contains("\\") || path.Split('/').Any(p => p == "." || p == "..")) { Text("TfMessage", "请输入以 / 开头的 TF 绝对目录。"); return Task.FromResult(0); }
            path = path == "/" ? path : path.TrimEnd('/');
            if (append && path != tfPath) append = false;
            int offset = append ? tfOffset : 0;
            return ManageAsync("TfMessage", async client =>
            {
                if (append && tfReadBoot != client.Boot) throw new IOException("主控已重启，请从第一页重新读取目录。");
                var list = await Task.Run(() => client.FileAction("list", ManagementClient.Args("path", path, "offset", offset)));
                if (!append) tfEntries.Clear();
                foreach (Dictionary<string, object> item in (IEnumerable)list["entries"])
                    tfEntries.Add(new TfEntry { Name = ManagementClient.Str(item, "name"), Path = ManagementClient.Join(path, ManagementClient.Str(item, "name")), Directory = ManagementClient.Flag(item, "directory"), Bytes = ManagementClient.Number(item, "bytes") });
                tfPath = path; tfReadTarget = backendTarget; tfReadBoot = client.Boot;
                Find<TextBox>("TfPath").Text = tfPath; tfOffset = (int)ManagementClient.Number(list, "next"); tfMore = ManagementClient.Flag(list, "more");
                Find<DataGrid>("TfGrid").ItemsSource = null; Find<DataGrid>("TfGrid").ItemsSource = tfEntries;
                Text("TfMessage", "已读取 " + tfEntries.Count + " 项" + (tfMore ? "，可点击下一页继续加载。" : "。") + "双击文件夹进入；采集、保存或交接期间主控会拒绝文件操作。");
            });
        }
        private string AskName(string title, string initial)
        {
            var dialog = new Window { Title = title, Owner = Window, Width = 440, Height = 180, ResizeMode = ResizeMode.NoResize, WindowStartupLocation = WindowStartupLocation.CenterOwner };
            var panel = new StackPanel { Margin = new Thickness(20) };
            var input = new TextBox { Text = initial, MinHeight = 36, Margin = new Thickness(0, 0, 0, 14) };
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
            var ok = new Button { Content = "确定", IsDefault = true, Padding = new Thickness(22, 7, 22, 7), Margin = new Thickness(0, 0, 10, 0) };
            var cancel = new Button { Content = "取消", IsCancel = true, Padding = new Thickness(22, 7, 22, 7) };
            ok.Click += delegate { dialog.DialogResult = true; }; buttons.Children.Add(ok); buttons.Children.Add(cancel); panel.Children.Add(input); panel.Children.Add(buttons); dialog.Content = panel;
            dialog.Loaded += delegate { input.Focus(); input.SelectAll(); };
            return dialog.ShowDialog() == true ? input.Text : null;
        }
        private async Task ChangeFileAsync(string action, Dictionary<string, object> args, string success)
        {
            bool changed = false;
            await ManageAsync("TfMessage", async client => { RequireTfDirectory(client); await Task.Run(() => client.FileAction(action, args)); changed = true; Text("TfMessage", success); });
            if (changed && !closed) await ReadFilesAsync(false);
        }
        private Task NewFolderAsync()
        {
            string name = AskName("新文件夹名称", "新文件夹"); if (name == null) return Task.FromResult(0);
            return ChangeFileAsync("mkdir", ManagementClient.Args("path", ManagementClient.Join(tfPath, name)), "文件夹已创建。");
        }
        private Task RenameTfAsync()
        {
            var entry = SelectedTf; string name = AskName("重命名", entry.Name); if (name == null || name == entry.Name) return Task.FromResult(0);
            return ChangeFileAsync("rename", ManagementClient.Args("path", entry.Path, "destination", ManagementClient.Join(tfPath, name)), "重命名已完成。");
        }
        private async Task PasteTfAsync()
        {
            if (!preview && Clipboard.ContainsFileDropList())
            {
                var paths = Clipboard.GetFileDropList().Cast<string>().ToArray();
                if (MessageBox.Show(Window, "将剪贴板中的 " + paths.Length + " 个电脑项目上传到 TF 目录 " + tfPath + "？\n支持文件；文件夹请先进入后选择文件。", "上传到主控", MessageBoxButton.OKCancel, MessageBoxImage.Question) == MessageBoxResult.OK) await UploadPathsAsync(paths);
                return;
            }
            if (tfCopy.Length == 0) { Text("TfMessage", "请先选中 TF 项目并点击复制，或从电脑复制文件后粘贴。"); return; }
            string name = tfCopy.Substring(tfCopy.LastIndexOf('/') + 1);
            name = AskName("粘贴到 " + tfPath + "：副本名称", ManagementClient.Join(tfPath, name) == tfCopy ? name + " - 副本" : name);
            if (name != null) await ChangeFileAsync("copy", ManagementClient.Args("path", tfCopy, "destination", ManagementClient.Join(tfPath, name)), "副本已保存并完整校验。");
        }
        private Task UploadTfAsync()
        {
            if (preview) return ManageAsync("TfMessage", c => Task.FromResult(0));
            var dialog = new OpenFileDialog { Title = "选择上传到 TF 的文件", Multiselect = true, CheckFileExists = true };
            return dialog.ShowDialog(Window) == true ? UploadPathsAsync(dialog.FileNames) : Task.FromResult(0);
        }
        private async Task UploadPathsAsync(string[] paths)
        {
            if (paths.Any(p => !File.Exists(p))) { Text("TfMessage", "请选择普通文件；本版电脑上传不支持文件夹。TF 卡内文件夹支持复制粘贴。"); return; }
            bool done = false;
            await ManageAsync("TfMessage", async client => { RequireTfDirectory(client); foreach (string local in paths) { string target = ManagementClient.Join(tfPath, Path.GetFileName(local)); await Task.Run(() => client.Upload(local, target)); } done = true; Text("TfMessage", "上传已完成，SHA-256校验通过。"); });
            if (done && !closed) await ReadFilesAsync(false);
        }
        private async Task DownloadTfAsync()
        {
            var entry = SelectedTf;
            if (preview) { Text("TfMessage", "离线预览：不下载文件。"); return; }
            var dialog = new SaveFileDialog { Title = "下载 TF 文件到电脑", FileName = entry.Name, OverwritePrompt = true };
            if (dialog.ShowDialog(Window) != true) return;
            string target = dialog.FileName; bool replace = File.Exists(target);
            await ManageAsync("TfMessage", async client => { RequireTfDirectory(client); await Task.Run(() => client.Download(entry.Path, target, replace)); Text("TfMessage", "下载已完成，SHA-256校验通过。\n已保存：" + target); });
        }
        private async Task DeleteTfAsync()
        {
            var entry = SelectedTf; bool deleted = false;
            await ManageAsync("TfMessage", async client =>
            {
                RequireTfDirectory(client);
                var measured = await Task.Run(() => client.FileAction("measure", ManagementClient.Args("path", entry.Path)));
                if (closed || operation.IsCancellationRequested) return;
                string detail = "永久删除 " + entry.Path + "？\n共 " + ManagementClient.Number(measured, "files") + " 个文件，" + ManagementClient.Number(measured, "bytes").ToString("N0") + " 字节。\n无法通过本软件恢复。";
                if (MessageBox.Show(Window, detail, "确认永久删除", MessageBoxButton.YesNo, MessageBoxImage.Warning, MessageBoxResult.No) != MessageBoxResult.Yes) { Text("TfMessage", "已取消删除。"); return; }
                await Task.Run(() => client.FileAction("delete", ManagementClient.Args("path", entry.Path, "confirmed", true, "revision", measured["revision"])));
                deleted = true;
            });
            if (deleted && !closed) await ReadFilesAsync(false);
        }
        private sealed class WifiChoice
        {
            public string Ssid, Rssi;
            public override string ToString() { return Ssid + "  " + Rssi + " dBm"; }
        }
    }
}
