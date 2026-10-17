// Explicit hardware-trial host only. Never invoked by normal build or CI.
using System;
using System.IO;
using BreadboardController;
public static class DeviceHost
{
    public static int Main(string[] args)
    {
        if(args.Length!=2||(args[0]!="--approved-usb-host"&&args[0]!="--approved-usb-open")||String.IsNullOrEmpty(Environment.GetEnvironmentVariable("BREADBOARD_V2_APPROVAL")))
        {Console.Error.WriteLine("Requires the explicitly approved controller_v2_trial.py parent.");return 2;}
        try
        {
            if(args[0]=="--approved-usb-open"){using(var wire=new UsbWire(args[1])){Console.WriteLine("OPEN_ONLY");System.Threading.Thread.Sleep(1200);}return 0;}
            using(var backend=new UsbBackend(args[1]))
            using(var bridge=new DeviceBridge(backend))
            {
                Console.WriteLine("BRIDGE_URL="+bridge.Address.AbsoluteUri);Console.Out.Flush();
                Console.ReadLine();
            }
            return 0;
        }
        catch(Exception error){Console.Error.WriteLine(error.GetType().Name+": "+error.Message);return 1;}
    }
}
