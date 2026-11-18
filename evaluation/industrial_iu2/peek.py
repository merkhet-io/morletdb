"""Lists the remote archive's directory and looks at one file through HTTP range requests (nothing is stored)."""
import io, zipfile, urllib.request, json, sys, csv, collections
URL = "https://ndownloader.figshare.com/files/52969793"
class Remote(io.RawIOBase):
    def __init__(s, url):
        r = urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"User-Agent": "research-client"}))
        s.url, s.size, s.pos, s.n = r.url, int(r.headers["Content-Length"]), 0, 0
    def seekable(s): return True
    def readable(s): return True
    def tell(s): return s.pos
    def seek(s, off, wh=0):
        s.pos = off if wh == 0 else s.pos + off if wh == 1 else s.size + off; return s.pos
    def readinto(s, b):
        n = min(len(b), s.size - s.pos)
        if n <= 0: return 0
        rq = urllib.request.Request(s.url, headers={"Range": f"bytes={s.pos}-{s.pos+n-1}", "User-Agent": "research-client"})
        d = urllib.request.urlopen(rq).read(); b[:len(d)] = d; s.pos += len(d); s.n += len(d); return len(d)
rem = Remote(URL); z = zipfile.ZipFile(io.BufferedReader(rem, 1 << 20))
inf = z.infolist()
files = [i for i in inf if not i.is_dir()]
print("entries", len(inf), "files", len(files), "compressed GB", sum(i.compress_size for i in files) / 1e9, "uncompressed GB", sum(i.file_size for i in files) / 1e9)
print("extensions", collections.Counter(i.filename.rsplit(".", 1)[-1].lower() for i in files))
print("first names", [i.filename for i in files[:6]])
sz = sorted(i.file_size for i in files); print("file size MB min/median/max", sz[0] / 1e6, sz[len(sz) // 2] / 1e6, sz[-1] / 1e6)
json.dump([[i.filename, i.file_size, i.compress_size] for i in files], open("/w/iu2/zip_listing.json", "w"))
small = min(files, key=lambda i: i.file_size)
print("peek", small.filename, small.file_size, small.compress_size)
with z.open(small) as f:
    head = f.read(3000)
print(head[:1500])
print("bytes fetched", rem.n)
