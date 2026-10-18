// Explicit read-only device probe; never part of application startup or ordinary build.
using System;
using System.Collections.Generic;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using BreadboardController;
public static class ManagementProbe
{
    public static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        if (args.Length != 1) return 2;
        try
        {
            Uri local;
            bool loopback = Uri.TryCreate(args[0], UriKind.Absolute, out local) && local.Scheme == "http" && local.Host == "127.0.0.1";
            using (IDeviceBackend backend = loopback ? (IDeviceBackend)new NetworkBackend(local) : new UsbBackend(args[0]))
            {
                var client = new ManagementClient(backend, CancellationToken.None, null); client.Begin();
                string before = client.Boot;
                var network = client.Get("/api/v2/network/state");
                var list = client.FileAction("list", ManagementClient.Args("path", "/", "offset", 0));
                client.Begin();
                var output = ManagementClient.Args("port", args[0], "boot_unchanged", before == client.Boot,
                    "connected", ManagementClient.Flag(network, "connected"), "ap_enabled", ManagementClient.Flag(network, "ap_enabled"),
                    "sta_ip", ManagementClient.Str(network, "sta_ip"), "ap_ip", ManagementClient.Str(network, "ap_ip"),
                    "root_entries", list["entries"], "more", list["more"], "actions", "status/network-state/root-list only; no config save or TF write/delete");
                Console.WriteLine(new JavaScriptSerializer().Serialize(output));
            }
            return 0;
        }
        catch (Exception e) { Console.Error.WriteLine(e.GetType().Name + ": " + e.Message); return 1; }
    }
}
