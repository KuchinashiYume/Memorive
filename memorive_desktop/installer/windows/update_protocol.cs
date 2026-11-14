// File-level update protocol. Shared by installer, standalone helper and app IPC.
using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Net;
using System.Threading;
using System.Diagnostics;
using File=LongFile;
using Directory=LongDirectory;

class UpdateAsset { public string name,url,sha256,source_manifest_sha256; public long size; }
class UpdateIndex {
    public string schema_version,product,platform,channel,release_version,package_id,source_commit,target_manifest_sha256;
    public bool test_only; public int[] version_tuple;public int min_updater_version,min_os;
    public UpdateAsset full;public UpdateAsset[] deltas;public Dictionary<string,string> notes;
    public UpdateDataCompatibility data_compatibility;
}
class UpdateDataCompatibility { public string migration_id;public int[] readable;public int writable; }
class UpdateTarget {
    public string schema_version,product,platform,package_id,release_version,inventory_sha256;
    public long total_bytes;public Member[] files;
}
class UpdateTrust {
    public string schema_version,algorithm,public_key_xml,latest_url,index_asset,signature_asset,release_trust_status,test_root;
    public bool test_only;public int updater_version,max_index_bytes,max_manifest_bytes,max_files;
    public long max_archive_bytes,max_expanded_bytes;
}
class UpdateError:Exception {public readonly string Code;public UpdateError(string code):base(code){Code=code;}}

static class UpdateProtocol {
    public static readonly UpdateTrust Trust=Engine.Json.Deserialize<UpdateTrust>(Encoding.UTF8.GetString(UiBootstrap.Resource("update_trust.json")));
    public static string HashBytes(byte[] bytes){using(var h=SHA256.Create())return BitConverter.ToString(h.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
    public static void Require(bool value,string code){if(!value)throw new UpdateError(code);}
    public static bool Hex(string s){return s!=null&&System.Text.RegularExpressions.Regex.IsMatch(s,@"\A[0-9a-fA-F]{64}\z");}
    public static string Id(string s){Require(s!=null&&System.Text.RegularExpressions.Regex.IsMatch(s,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\z"),"UPDATE_ID_INVALID");return s;}
    public static void Relative(string s){
        Engine.Relative(s);Require(!s.Any(c=>c<32||"*?\"<>|".Contains(c)),"UPDATE_PATH_INVALID");
        foreach(string part in s.Split('/'))Require(!new[]{".git",".env","__pycache__","private-test","sandbox_profile"}.Contains(part.ToLowerInvariant()),"UPDATE_PRIVATE_PAYLOAD_FORBIDDEN");
    }
    public static void Members(Member[] rows,long declared) {
        Require(rows!=null&&rows.Length>0&&rows.Length<=Trust.max_files,"UPDATE_FILE_COUNT_INVALID");
        long total=0;var names=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach(var r in rows){Relative(r.path);Require(names.Add(r.path)&&r.size>=0&&r.size<=Trust.max_expanded_bytes&&Hex(r.sha256),"UPDATE_MEMBER_INVALID");total=checked(total+r.size);}
        Require(total==declared&&total<=Trust.max_expanded_bytes,"UPDATE_EXPANSION_LIMIT");
        // A file cannot also be an ancestor directory of another member.
        foreach(string path in names){string parent=path;while(parent.Contains('/')){parent=parent.Substring(0,parent.LastIndexOf('/'));Require(!names.Contains(parent),"UPDATE_FILE_DIRECTORY_COLLISION");}}
    }
    public static string Fingerprint(IEnumerable<Member> rows) {
        var text=new StringBuilder();foreach(var r in rows.OrderBy(x=>x.path,StringComparer.Ordinal))text.Append(r.path).Append('\t').Append(r.size.ToString(System.Globalization.CultureInfo.InvariantCulture)).Append('\t').Append(r.sha256.ToLowerInvariant()).Append('\n');
        return HashBytes(Encoding.UTF8.GetBytes(text.ToString()));
    }
    public static Member[] Inventory(string root) {
        Engine.NoReparse(root);var rows=new List<Member>();var pending=new Stack<string>();pending.Push(root);
        while(pending.Count>0){foreach(string p in Directory.EnumerateFileSystemEntries(pending.Pop())){
            Engine.NoReparse(p);if(Directory.Exists(p)){pending.Push(p);continue;}
            string name=p.Substring(root.TrimEnd('\\').Length+1).Replace('\\','/');Relative(name);
            rows.Add(new Member{path=name,size=new FileInfo(LongFile.N(p)).Length,sha256=Engine.Hash(p).ToLowerInvariant()});
            Require(rows.Count<=Trust.max_files,"UPDATE_FILE_COUNT_INVALID");
        }}
        Members(rows.ToArray(),rows.Sum(x=>x.size));return rows.OrderBy(x=>x.path,StringComparer.Ordinal).ToArray();
    }
    public static void VerifyFiles(string root,Member[] rows,Action<long,long> progress,bool exact) {
        long i=0;foreach(var r in rows){string p=Engine.Under(root,r.path);Require(File.Exists(p)&&new FileInfo(LongFile.N(p)).Length==r.size&&String.Equals(Engine.Hash(p),r.sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_FILE_HASH_MISMATCH");progress(++i,rows.Length);}
        if(exact){var actual=new HashSet<string>(Directory.GetFiles(root,"*",SearchOption.AllDirectories).Select(p=>p.Substring(root.TrimEnd('\\').Length+1).Replace('\\','/')),StringComparer.OrdinalIgnoreCase);Require(actual.SetEquals(rows.Select(x=>x.path)),"UPDATE_TARGET_MEMBERSHIP_MISMATCH");}
    }
    public static void VerifySignature(byte[] bytes,string signature) {
        Require(Trust.schema_version=="MemoriveUpdateTrust-v1"&&Trust.algorithm=="RSA3072-SHA256-PKCS1-v1_5","UPDATE_TRUST_CONFIGURATION_INVALID");
        Require(bytes.Length<=Trust.max_index_bytes,"UPDATE_INDEX_LIMIT");
        Require(!String.IsNullOrEmpty(Trust.public_key_xml),"UPDATE_RELEASE_TRUST_NOT_CONFIGURED");
        try {using(var rsa=new RSACryptoServiceProvider()){rsa.PersistKeyInCsp=false;rsa.FromXmlString(Trust.public_key_xml);
            Require(rsa.KeySize==3072&&rsa.VerifyData(bytes,CryptoConfig.MapNameToOID("SHA256"),Convert.FromBase64String(signature)),"UPDATE_SIGNATURE_INVALID");}}
        catch(UpdateError){throw;}catch{throw new UpdateError("UPDATE_SIGNATURE_INVALID");}
    }
    public static UpdateIndex ParseIndex(byte[] bytes,string signature) {
        VerifySignature(bytes,signature);UpdateIndex index;
        try {index=Engine.Json.Deserialize<UpdateIndex>(new UTF8Encoding(false,true).GetString(bytes));}catch{throw new UpdateError("UPDATE_INDEX_INVALID");}
        Require(index!=null&&index.schema_version=="MemoriveUpdateIndex-v1"&&index.product=="Memorive"&&index.platform=="win-x64"&&index.channel=="stable","UPDATE_INDEX_INVALID");
        Require(index.test_only==Trust.test_only,"UPDATE_TRUST_DOMAIN_MISMATCH");Id(index.package_id);
        var v=Engine.ReleaseOrder(index.release_version);
        Require(index.version_tuple!=null&&index.version_tuple.SequenceEqual(new[]{v.Major,v.Minor,v.Build,v.Revision})&&Hex(index.target_manifest_sha256),"UPDATE_VERSION_IDENTITY_INVALID");
        Require(index.min_updater_version>=1&&index.min_updater_version<=Trust.updater_version,"UPDATE_ASSISTANT_TOO_OLD");
        Require(index.min_os>=19045&&index.min_os<=Environment.OSVersion.Version.Build,"UPDATE_OS_TOO_OLD");
        Require(!String.IsNullOrWhiteSpace(index.source_commit)&&index.source_commit.Length<=128,"UPDATE_SOURCE_IDENTITY_INVALID");
        Require(index.data_compatibility!=null&&index.data_compatibility.migration_id=="E11-existing-schemas-v1"&&index.data_compatibility.writable==2&&index.data_compatibility.readable!=null&&index.data_compatibility.readable.SequenceEqual(new[]{1,2}),"UPDATE_DATA_CONTRACT_UNSUPPORTED");
        Require(index.notes!=null&&new[]{"zh-CN","en-US","ja-JP"}.All(k=>index.notes.ContainsKey(k)&&index.notes[k]!=null&&index.notes[k].Length<=16000),"UPDATE_NOTES_INVALID");
        ValidateAsset(index.full);Require(index.deltas!=null&&index.deltas.Length<=8,"UPDATE_DELTA_COUNT_INVALID");
        var seen=new HashSet<string>();foreach(var a in index.deltas){ValidateAsset(a);Require(Hex(a.source_manifest_sha256)&&seen.Add(a.source_manifest_sha256.ToLowerInvariant()),"UPDATE_SOURCE_IDENTITY_INVALID");}
        return index;
    }
    public static void ValidateAsset(UpdateAsset a){Require(a!=null&&a.size>0&&a.size<=Trust.max_archive_bytes&&Hex(a.sha256),"UPDATE_ASSET_INVALID");Relative(a.name);Require(!a.name.Contains('/')&&a.name.EndsWith(".zip",StringComparison.OrdinalIgnoreCase),"UPDATE_ASSET_INVALID");AllowedUrl(a.url,false);}
    public static Uri AllowedUrl(string text,bool redirect) {
        Uri u;Require(Uri.TryCreate(text,UriKind.Absolute,out u)&&String.IsNullOrEmpty(u.UserInfo)&&String.IsNullOrEmpty(u.Fragment),"UPDATE_URL_FORBIDDEN");
        Uri test;bool local=Trust.test_only&&Uri.TryCreate(Trust.latest_url,UriKind.Absolute,out test)&&u.Scheme=="http"&&u.Host=="127.0.0.1"&&u.Port==test.Port;
        bool production=u.Scheme=="https"&&u.IsDefaultPort&&((u.Host=="api.github.com"&&u.AbsolutePath=="/repos/KuchinashiYume/Memorive/releases/latest")||(u.Host=="github.com"&&u.AbsolutePath.StartsWith("/KuchinashiYume/Memorive/releases/download/",StringComparison.Ordinal))||(redirect&&u.Host=="release-assets.githubusercontent.com"));
        Require(Trust.test_only?local:production,"UPDATE_URL_FORBIDDEN");return u;
    }
    public static UpdateTarget Target(ZipArchive zip,UpdateIndex index) {
        var entries=zip.Entries.Where(e=>e.FullName=="target-manifest.json").ToArray();Require(entries.Length==1&&entries[0].Length>0&&entries[0].Length<=Trust.max_manifest_bytes,"UPDATE_TARGET_MANIFEST_INVALID");
        byte[] raw;using(var s=entries[0].Open())using(var memory=new MemoryStream()){byte[] b=new byte[65536];int n;while((n=s.Read(b,0,b.Length))>0){Require(memory.Length+n<=Trust.max_manifest_bytes,"UPDATE_MANIFEST_LIMIT");memory.Write(b,0,n);}raw=memory.ToArray();}
        Require(HashBytes(raw)==index.target_manifest_sha256.ToLowerInvariant(),"UPDATE_TARGET_IDENTITY_MISMATCH");
        var target=Engine.Json.Deserialize<UpdateTarget>(new UTF8Encoding(false,true).GetString(raw));
        Require(target.schema_version=="MemoriveTargetManifest-v1"&&target.product=="Memorive"&&target.platform=="win-x64"&&target.package_id==index.package_id&&target.release_version==index.release_version,"UPDATE_TARGET_IDENTITY_MISMATCH");
        Members(target.files,target.total_bytes);Require(Fingerprint(target.files)==target.inventory_sha256,"UPDATE_TARGET_INVENTORY_MISMATCH");
        Require(new[]{"Memorive.exe","_internal/release_identity_binding.json","_internal/update/Memorive.Update.exe","_internal/update/Memorive.Launcher.exe","_internal/update/helper-identity.json"}.All(path=>target.files.Any(x=>x.path==path)),"UPDATE_MAIN_APPLICATION_REQUIRED");
        var names=new HashSet<string>(StringComparer.OrdinalIgnoreCase);var files=target.files.ToDictionary(x=>"files/"+x.path,StringComparer.OrdinalIgnoreCase);
        foreach(var e in zip.Entries){Relative(e.FullName);Require(names.Add(e.FullName),"UPDATE_DUPLICATE_ZIP_MEMBER");int kind=(e.ExternalAttributes>>16)&0xF000;Require((kind==0||kind==0x8000)&&(e.ExternalAttributes&0x400)==0,"UPDATE_ZIP_LINK_FORBIDDEN");
            if(e.FullName=="target-manifest.json")continue;Member m;Require(files.TryGetValue(e.FullName,out m)&&m.size==e.Length,"UPDATE_UNDECLARED_ZIP_MEMBER");}
        Require(zip.Entries.Count<=Trust.max_files+1,"UPDATE_FILE_COUNT_INVALID");return target;
    }
    public static UpdateTarget Assemble(string archive,UpdateAsset asset,UpdateIndex index,string oldApp,Member[] oldFiles,string destination,Action<long,long> progress,Func<bool> cancel) {
        Engine.NoReparse(archive);Require(new FileInfo(LongFile.N(archive)).Length==asset.size&&String.Equals(Engine.Hash(archive),asset.sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_ARCHIVE_HASH_MISMATCH");
        Require(!Directory.Exists(destination)&&!File.Exists(destination),"UPDATE_SLOT_ALREADY_EXISTS");
        if(asset.source_manifest_sha256!=null){Require(oldFiles!=null&&Fingerprint(oldFiles)==asset.source_manifest_sha256,"UPDATE_BASE_MISMATCH");VerifyFiles(oldApp,oldFiles,(i,n)=>{},false);}
        using(var stream=File.OpenRead(archive))using(var zip=new ZipArchive(stream,ZipArchiveMode.Read)){
            var target=Target(zip,index);var map=zip.Entries.ToDictionary(x=>x.FullName,StringComparer.OrdinalIgnoreCase);var old=(oldFiles??new Member[0]).ToDictionary(x=>x.path,StringComparer.OrdinalIgnoreCase);
            var disk=new DriveInfo(Path.GetPathRoot(destination));Require(disk.AvailableFreeSpace>target.total_bytes+1024L*1024*1024,"UPDATE_SPACE_INSUFFICIENT");
            Directory.CreateDirectory(destination);long i=0;
            foreach(var row in target.files){Require(!cancel(),"UPDATE_CANCELLED");string path=Engine.Under(destination,row.path);Directory.CreateDirectory(Path.GetDirectoryName(path));ZipArchiveEntry member;
                if(map.TryGetValue("files/"+row.path,out member)){
                    using(var src=member.Open())using(var dst=new FileStream(LongFile.N(path),FileMode.CreateNew,FileAccess.Write,FileShare.None)){byte[] b=new byte[1024*1024];long total=0;int got;while((got=src.Read(b,0,b.Length))>0){Require(!cancel(),"UPDATE_CANCELLED");total+=got;Require(total<=row.size,"UPDATE_EXPANSION_LIMIT");dst.Write(b,0,got);}Require(total==row.size,"UPDATE_MEMBER_LENGTH_MISMATCH");dst.Flush(true);}
                }else{
                    Member source;Require(asset.source_manifest_sha256!=null&&old.TryGetValue(row.path,out source)&&source.size==row.size&&String.Equals(source.sha256,row.sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_DELTA_REUSE_INVALID");
                    string from=Engine.Under(oldApp,row.path);Require(String.Equals(Engine.Hash(from),row.sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_BASE_MISMATCH");File.Copy(from,path);
                }
                Require(String.Equals(Engine.Hash(path),row.sha256,StringComparison.OrdinalIgnoreCase),"UPDATE_FILE_HASH_MISMATCH");progress(++i,target.files.Length);
            }
            VerifyFiles(destination,target.files,progress,true);
            var identity=Engine.Load<Dictionary<string,object>>(Engine.Under(destination,"_internal/release_identity_binding.json"));
            Require(Convert.ToString(identity["package_id"])==target.package_id&&Convert.ToString(identity["release_version"])==target.release_version&&Convert.ToString(identity["main_executable"])=="Memorive.exe","UPDATE_EMBEDDED_IDENTITY_MISMATCH");
            var helper=Engine.Load<Dictionary<string,object>>(Engine.Under(destination,"_internal/update/helper-identity.json"));
            Require(String.Equals(Convert.ToString(helper["helper_sha256"]),Engine.Hash(Engine.Under(destination,"_internal/update/Memorive.Update.exe")),StringComparison.OrdinalIgnoreCase)&&String.Equals(Convert.ToString(helper["launcher_sha256"]),Engine.Hash(Engine.Under(destination,"_internal/update/Memorive.Launcher.exe")),StringComparison.OrdinalIgnoreCase),"UPDATE_EMBEDDED_HELPER_MISMATCH");
            Require(Convert.ToBoolean(helper["test_only"])==Trust.test_only,"UPDATE_TRUST_DOMAIN_MISMATCH");return target;
        }
    }
}
