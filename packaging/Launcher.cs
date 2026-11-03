using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class Launcher
{
    private static string Quote(string value)
    {
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char item in value)
        {
            if (item == '\\') { slashes++; continue; }
            if (item == '"') { result.Append('\\', slashes * 2 + 1); result.Append('"'); slashes = 0; continue; }
            result.Append('\\', slashes); slashes = 0; result.Append(item);
        }
        result.Append('\\', slashes * 2); result.Append('"');
        return result.ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
#if CONSOLE
        bool console = true;
#else
        bool console = false;
#endif
        string root = AppDomain.CurrentDomain.BaseDirectory;
        try
        {
            string binary = console ? "python.exe" : "pythonw.exe";
            string python = Path.Combine(root, "runtime", binary);
            if (!File.Exists(python)) python = Path.Combine(root, ".venv", "Scripts", binary);
            if (!File.Exists(python)) throw new FileNotFoundException("Python runtime is missing. Extract the complete portable folder, or restore the project .venv.");
            string script = Path.Combine(root, "launch_reader.py");
            if (!File.Exists(script)) throw new FileNotFoundException("launch_reader.py is missing. Keep the complete application folder together.");
            StringBuilder arguments = new StringBuilder(Quote(script));
            foreach (string value in args) arguments.Append(" ").Append(Quote(value));
            ProcessStartInfo info = new ProcessStartInfo(python, arguments.ToString());
            info.WorkingDirectory = console ? Environment.CurrentDirectory : root;
            info.UseShellExecute = false; info.CreateNoWindow = !console;
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            info.RedirectStandardOutput = console; info.RedirectStandardError = console;
            info.RedirectStandardInput = console && Console.IsInputRedirected;
            if (console) { info.StandardOutputEncoding = new UTF8Encoding(false); info.StandardErrorEncoding = new UTF8Encoding(false); }
            using (Process process = new Process())
            {
                process.StartInfo = info;
                if (console)
                {
                    Console.OutputEncoding = new UTF8Encoding(false);
                    Console.InputEncoding = new UTF8Encoding(false);
                    process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Console.Out.WriteLine(e.Data); };
                    process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Console.Error.WriteLine(e.Data); };
                }
                process.Start();
                if (console)
                {
                    process.BeginOutputReadLine(); process.BeginErrorReadLine();
                    if (info.RedirectStandardInput)
                        Task.Factory.StartNew(delegate { try { string input = Console.In.ReadToEnd(); process.StandardInput.Write(input); process.StandardInput.Close(); } catch (IOException) { } });
                }
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            try { Directory.CreateDirectory(Path.Combine(root,"data","logs")); File.AppendAllText(Path.Combine(root,"data","logs","bootstrap_error.txt"), DateTime.UtcNow.ToString("o") + "\r\n" + error + "\r\n", Encoding.UTF8); } catch { }
            if (console) Console.Error.WriteLine(error.ToString());
            else MessageBox.Show(error.Message, "TreeSea Reader - Startup failed", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
