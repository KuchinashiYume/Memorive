"""A local HTTP server that stands in for the arXiv and Crossref metadata services.

Every request goes to 127.0.0.1; the answers are synthetic records.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler


class LoopbackMetadata:
    def __init__(self):
        self.calls = []
        self.entries = 2
        self.status = 200
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):pass
            def do_GET(self):
                source = self.path.strip('/')
                fixture.calls.append(source)
                data = fixture.payload(source)
                self.send_response(fixture.status)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def payload(self, source):
        if source == 'ARXIV':
            entries = ''.join('<entry><id>https://arxiv.org/abs/2609.10%03dv1</id><title>Synthetic workflow %d</title><author><name>Test Author</name></author><published>2026-09-01T00:00:00Z</published><updated>2026-09-01T00:00:00Z</updated><summary>Non-real scientific metadata for local regression only.</summary><arxiv:doi>10.9999/sample.%d</arxiv:doi></entry>' % (i,i,i) for i in range(self.entries))
            return ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom"><opensearch:totalResults>%d</opensearch:totalResults><opensearch:startIndex>0</opensearch:startIndex><opensearch:itemsPerPage>%d</opensearch:itemsPerPage>%s</feed>' % (self.entries,self.entries,entries)).encode()
        assert source == 'CROSSREF'
        items = [{'DOI':'10.9999/sample.'+str(i),'title':['Synthetic workflow '+str(i)],'author':[{'given':'Test','family':'Author'}],'published':{'date-parts':[[2026,9,1]]},'type':'journal-article','URL':'https://doi.org/10.9999/sample.'+str(i)} for i in range(self.entries)]
        return json.dumps({'status':'ok','message-type':'work-list','message-version':'1.0.0','message':{'items':items,'total-results':len(items),'items-per-page':len(items),'query':{'start-index':0}}}).encode()

    def request(self, **params):
        from memorive.desktop.settings.metadata_transport import MetadataResponse
        host = urlsplit(params['url']).hostname
        source = {'export.arxiv.org':'ARXIV','api.crossref.org':'CROSSREF'}[host]
        opener = build_opener(ProxyHandler({}))
        try:
            response = opener.open('http://127.0.0.1:%d/%s' % (self.server.server_port,source),timeout=5)
        except __import__('urllib.error',fromlist=['HTTPError']).HTTPError as exc:
            response = exc
        with response:
            return MetadataResponse(response.status,response.read(),{'test_network_scope':'LOOPBACK_ONLY'})

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
