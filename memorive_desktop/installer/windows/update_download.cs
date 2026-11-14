using System;
using System.IO;
using System.Net;
using System.Text;
using System.Linq;
using System.Collections;
using System.Collections.Generic;
using File=LongFile;
using Directory=LongDirectory;

class UpdateNetworkPolicy {
    public string proxy_mode="SYSTEM",proxy_address;
    public int maximum_requests=12;
    public long maximum_response_bytes=5L*1024*1024*1024;
}
class UpdateNetworkReceipt {public int index,status;public string at,url,method="GET",error;public long bytes;}

// Every physical request, including failed requests and redirects, is reserved.
// Public requests never attach product/model credentials.
class UpdateDownload {
    readonly string folder;readonly UpdateNetworkPolicy policy;readonly List<UpdateNetworkReceipt> calls;
    public string Etag;public long RetryAfterUtc;
    public UpdateDownload(string root,UpdateNetworkPolicy value){
        folder=root;policy=value??new UpdateNetworkPolicy();
        UpdateProtocol.Require(policy.maximum_requests>0&&policy.maximum_requests<=16&&policy.maximum_response_bytes>0&&policy.maximum_response_bytes<=6L*1024*1024*1024,"UPDATE_NETWORK_POLICY_INVALID");
        string path=Path.Combine(root,"network.json");calls=File.Exists(path)?Engine.Load<List<UpdateNetworkReceipt>>(path):new List<UpdateNetworkReceipt>();
    }
    void Save(){Engine.Save(Path.Combine(folder,"network.json"),calls);}
    public byte[] Fetch(string url,long maximum,string etag,Action<long,long> progress,Func<bool> cancel,string destination) {
        for(int redirects=0;redirects<=3;redirects++){
            Uri uri=UpdateProtocol.AllowedUrl(url,redirects>0);
            UpdateProtocol.Require(calls.Count<policy.maximum_requests&&calls.Sum(x=>x.bytes)<policy.maximum_response_bytes,"UPDATE_NETWORK_BUDGET_EXHAUSTED");
            UpdateProtocol.Require(!cancel(),"UPDATE_CANCELLED");
            var receipt=new UpdateNetworkReceipt{index=calls.Count+1,at=DateTime.UtcNow.ToString("o"),url=uri.GetLeftPart(UriPartial.Path)};calls.Add(receipt);Save();
            var request=(HttpWebRequest)WebRequest.Create(uri);request.Method="GET";request.UserAgent="Memorive-Updater/1";request.Accept="application/vnd.github+json";
            request.AllowAutoRedirect=false;request.Timeout=30000;request.ReadWriteTimeout=30000;request.AutomaticDecompression=DecompressionMethods.None;
            ServicePointManager.SecurityProtocol=SecurityProtocolType.Tls12;
            if(policy.proxy_mode=="DIRECT")request.Proxy=null;
            else if(policy.proxy_mode=="CUSTOM"){
                Uri proxy;UpdateProtocol.Require(Uri.TryCreate(policy.proxy_address,UriKind.Absolute,out proxy)&&new[]{"http","https"}.Contains(proxy.Scheme)&&String.IsNullOrEmpty(proxy.UserInfo),"UPDATE_PROXY_INVALID");request.Proxy=new WebProxy(proxy);
            }else UpdateProtocol.Require(policy.proxy_mode=="SYSTEM","UPDATE_PROXY_INVALID");
            if(etag!=null)request.Headers[HttpRequestHeader.IfNoneMatch]=etag;
            HttpWebResponse response=null;
            try {
                try {response=(HttpWebResponse)request.GetResponse();}catch(WebException ex){response=ex.Response as HttpWebResponse;if(response==null)throw;}
                receipt.status=(int)response.StatusCode;
                if(receipt.status==304){Etag=etag;return null;}
                if(new[]{301,302,303,307,308}.Contains(receipt.status)){
                    UpdateProtocol.Require(redirects<3,"UPDATE_REDIRECT_LIMIT");url=new Uri(uri,response.Headers[HttpResponseHeader.Location]).AbsoluteUri;UpdateProtocol.AllowedUrl(url,true);continue;
                }
                if(receipt.status==403||receipt.status==429){
                    long seconds;DateTime date;string retry=response.Headers[HttpResponseHeader.RetryAfter];
                    if(Int64.TryParse(retry,out seconds))RetryAfterUtc=DateTimeOffset.UtcNow.ToUnixTimeSeconds()+Math.Max(60,Math.Min(seconds,86400));
                    else if(DateTime.TryParse(retry,out date))RetryAfterUtc=new DateTimeOffset(date.ToUniversalTime()).ToUnixTimeSeconds();
                    else if(!Int64.TryParse(response.Headers["X-RateLimit-Reset"],out RetryAfterUtc))RetryAfterUtc=DateTimeOffset.UtcNow.ToUnixTimeSeconds()+3600;
                    throw new UpdateError("UPDATE_RATE_LIMITED");
                }
                UpdateProtocol.Require(receipt.status==200,"UPDATE_HTTP_"+receipt.status);
                UpdateProtocol.Require(response.ContentLength<=maximum,"UPDATE_DOWNLOAD_LIMIT");Etag=response.Headers[HttpResponseHeader.ETag];
                using(var source=response.GetResponseStream())using(Stream output=destination==null?(Stream)new MemoryStream():new FileStream(LongFile.N(destination),FileMode.CreateNew,FileAccess.Write,FileShare.None)){
                    byte[] bytes=new byte[1024*1024];int got;long total=0;
                    while((got=source.Read(bytes,0,bytes.Length))>0){
                        UpdateProtocol.Require(!cancel(),"UPDATE_CANCELLED");total+=got;receipt.bytes=total;
                        UpdateProtocol.Require(total<=maximum&&calls.Sum(x=>x.bytes)<=policy.maximum_response_bytes,"UPDATE_DOWNLOAD_LIMIT");
                        output.Write(bytes,0,got);progress(total,response.ContentLength);
                    }
                    UpdateProtocol.Require(response.ContentLength<0||response.ContentLength==total,"UPDATE_DOWNLOAD_TRUNCATED");
                    if(output is FileStream)((FileStream)output).Flush(true);
                    return output is MemoryStream?((MemoryStream)output).ToArray():new byte[0];
                }
            }catch(Exception ex){receipt.error=ex is UpdateError?ex.Message:"UPDATE_NETWORK_FAILED";throw;}
            finally {if(response!=null)response.Dispose();Save();}
        }
        throw new UpdateError("UPDATE_REDIRECT_LIMIT");
    }
}

class UpdateCheckState {
    public string state="IDLE",error,etag,release_id,package_id,release_version,operation_id;
    public long checked_at,retry_after,downloaded,total;
    public bool automatic_enabled=true,full_fallback,consent_required;
    public string fallback_reason;public Dictionary<string,string> notes;
}

class UpdateDiscovery {
    readonly string folder;public UpdateCheckState state;readonly UpdateNetworkPolicy policy;
    public UpdateDiscovery(string root,UpdateNetworkPolicy network){folder=root;Engine.NoReparse(root);Directory.CreateDirectory(root);policy=network;string file=Path.Combine(folder,"check.json");state=File.Exists(file)?Engine.Load<UpdateCheckState>(file):new UpdateCheckState();}
    public void Save(){Engine.Save(Path.Combine(folder,"check.json"),state);}
    static Dictionary<string,object> Object(object x){return (Dictionary<string,object>)x;}
    static string Asset(Dictionary<string,object> release,string name){
        var matches=((IEnumerable)release["assets"]).Cast<object>().Select(Object).Where(x=>Convert.ToString(x["name"])==name).ToArray();
        UpdateProtocol.Require(matches.Length==1,"UPDATE_INDEX_ASSET_UNAVAILABLE");string url=Convert.ToString(matches[0]["browser_download_url"]);UpdateProtocol.AllowedUrl(url,false);return url;
    }
    void ReconcileCurrent(string currentVersion,string currentPackage){
        // Discovery receipts outlive an installed-version change. Reclassify the
        // cached release before a no-network return; never turn a failed check
        // into success or promote a formerly inapplicable release without recheck.
        if(state.state!="AVAILABLE"&&state.state!="UP_TO_DATE"&&!(state.state=="NO_COMPATIBLE_UPDATE"&&state.error=="UPDATE_SAME_VERSION_DIFFERENT_PACKAGE"))return;
        if(String.IsNullOrEmpty(state.release_version)||String.IsNullOrEmpty(state.package_id))return;
        int order=Engine.ReleaseOrder(state.release_version).CompareTo(Engine.ReleaseOrder(currentVersion));
        if(order<0||(order==0&&state.package_id==currentPackage)){
            state.state="UP_TO_DATE";state.error=null;state.total=0;state.downloaded=0;state.full_fallback=false;state.consent_required=false;state.fallback_reason=null;
        }else if(order==0){state.state="NO_COMPATIBLE_UPDATE";state.error="UPDATE_SAME_VERSION_DIFFERENT_PACKAGE";}
        else if(state.state!="AVAILABLE"){state.state="IDLE";state.error=null;state.checked_at=0;}
    }
    public UpdateCheckState Check(string currentVersion,string currentPackage,bool automatic){
        if(String.IsNullOrEmpty(UpdateProtocol.Trust.public_key_xml)){state.state="CHECK_FAILED";state.error="UPDATE_RELEASE_TRUST_NOT_CONFIGURED";Save();return state;}
        ReconcileCurrent(currentVersion,currentPackage);
        long now=DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        if(automatic&&(!state.automatic_enabled||now-state.checked_at<86400))return state;
        UpdateProtocol.Require(now>=state.retry_after,"UPDATE_RATE_LIMITED");
        using(var lockFile=new FileStream(LongFile.N(Path.Combine(folder,"check.lock")),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
            // Re-read shared state after acquiring the cross-process lease.
            if(File.Exists(Path.Combine(folder,"check.json")))state=Engine.Load<UpdateCheckState>(Path.Combine(folder,"check.json"));
            ReconcileCurrent(currentVersion,currentPackage);
            if(automatic&&(!state.automatic_enabled||now-state.checked_at<86400))return state;
            UpdateProtocol.Require(now>=state.retry_after,"UPDATE_RATE_LIMITED");
            string attempt=Path.Combine(folder,"checks",Guid.NewGuid().ToString("N"));Directory.CreateDirectory(attempt);
            var web=new UpdateDownload(attempt,policy);state.state="CHECKING";state.error=null;state.checked_at=now;Save();
            try {
                byte[] raw=web.Fetch(UpdateProtocol.Trust.latest_url,1024*1024,state.etag,(a,b)=>{},()=>false,null);
                string cache=Path.Combine(folder,"release-cache.json");
                if(raw==null){UpdateProtocol.Require(File.Exists(cache),"UPDATE_CACHE_MISSING");raw=System.IO.File.ReadAllBytes(LongFile.N(cache));}
                else File.WriteAllBytes(cache,raw);
                state.etag=web.Etag;var release=Engine.Json.Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(raw));
                UpdateProtocol.Require(release.ContainsKey("draft")&&release["draft"] is bool&&!(bool)release["draft"]&&release.ContainsKey("prerelease")&&release["prerelease"] is bool&&!(bool)release["prerelease"],"UPDATE_FORMAL_RELEASE_REQUIRED");
                var indexRaw=web.Fetch(Asset(release,UpdateProtocol.Trust.index_asset),UpdateProtocol.Trust.max_index_bytes,null,(a,b)=>{},()=>false,null);
                var signature=web.Fetch(Asset(release,UpdateProtocol.Trust.signature_asset),2048,null,(a,b)=>{},()=>false,null);
                var index=UpdateProtocol.ParseIndex(indexRaw,Encoding.UTF8.GetString(signature));
                UpdateProtocol.Require(Convert.ToString(release["tag_name"])=="v"+index.release_version,"UPDATE_RELEASE_TAG_MISMATCH");
                state.release_id=Convert.ToString(release["id"]);state.package_id=index.package_id;state.release_version=index.release_version;state.notes=index.notes;
                int order=Engine.ReleaseOrder(index.release_version).CompareTo(Engine.ReleaseOrder(currentVersion));
                if(order<0||(order==0&&index.package_id==currentPackage)){state.state="UP_TO_DATE";}
                else if(order==0){state.state="NO_COMPATIBLE_UPDATE";state.error="UPDATE_SAME_VERSION_DIFFERENT_PACKAGE";}
                else {state.state="AVAILABLE";state.total=index.full.size;File.WriteAllBytes(Path.Combine(folder,"verified-index.json"),indexRaw);File.WriteAllBytes(Path.Combine(folder,"verified-index.sig"),signature);}
            }catch(UpdateError ex){state.state=ex.Code=="UPDATE_INDEX_ASSET_UNAVAILABLE"?"NO_COMPATIBLE_UPDATE":"CHECK_FAILED";state.error=ex.Code;state.retry_after=web.RetryAfterUtc;}
            catch {state.state="CHECK_FAILED";state.error="UPDATE_NETWORK_FAILED";}
            Save();return state;
        }
    }
    public UpdateIndex VerifiedIndex(){return UpdateProtocol.ParseIndex(System.IO.File.ReadAllBytes(LongFile.N(Path.Combine(folder,"verified-index.json"))),File.ReadAllText(Path.Combine(folder,"verified-index.sig"),Encoding.UTF8));}
}
