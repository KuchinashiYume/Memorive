// Release-side tool only. Private key is DPAPI protected and never bundled.
using System;
using System.IO;
using System.Text;
using System.Security.Cryptography;

class ReleaseSigner {
    static string Get(string[] args,string name) {int i=Array.IndexOf(args,name);return i>=0&&i+1<args.Length?args[i+1]:null;}
    static int Main(string[] args) {
        try {
            string path=Environment.GetEnvironmentVariable("MEMORIVE_RELEASE_KEY_FILE");
            if(String.IsNullOrWhiteSpace(path)||!Path.IsPathRooted(path))throw new Exception("KEY_REFERENCE_REQUIRED");
            using(var rsa=new RSACryptoServiceProvider(3072)) {
                rsa.PersistKeyInCsp=false;
                if(Array.IndexOf(args,"--new-test-key")>=0) {
                    if(File.Exists(path))throw new Exception("KEY_ALREADY_EXISTS");
                    byte[] clear=Encoding.UTF8.GetBytes(rsa.ToXmlString(true));
                    try {File.WriteAllBytes(path,ProtectedData.Protect(clear,null,DataProtectionScope.CurrentUser));}
                    finally {Array.Clear(clear,0,clear.Length);}
                    File.WriteAllText(Get(args,"--public-output"),rsa.ToXmlString(false),new UTF8Encoding(false));
                    Console.WriteLine("TEST_KEY_CREATED_PRIVATE_NOT_EXPORTED");return 0;
                }
                byte[] secret=ProtectedData.Unprotect(File.ReadAllBytes(path),null,DataProtectionScope.CurrentUser);
                try {rsa.FromXmlString(Encoding.UTF8.GetString(secret));}finally {Array.Clear(secret,0,secret.Length);}
                if(rsa.KeySize!=3072)throw new Exception("RSA3072_REQUIRED");
                byte[] data=File.ReadAllBytes(Get(args,"--sign"));
                if(data.Length>262144)throw new Exception("INDEX_LIMIT");
                File.WriteAllText(Get(args,"--output"),Convert.ToBase64String(rsa.SignData(data,CryptoConfig.MapNameToOID("SHA256"))),new UTF8Encoding(false));
                Console.WriteLine("SIGNED");return 0;
            }
        } catch(Exception) {Console.Error.WriteLine("RELEASE_SIGNING_FAILED");return 1;}
    }
}
