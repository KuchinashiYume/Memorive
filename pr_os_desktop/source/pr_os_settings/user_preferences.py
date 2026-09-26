"""Local-only presentation preferences. Never contains an original avatar path."""
from __future__ import annotations
import base64
import binascii
import struct
import zlib
import unicodedata
from pathlib import Path
from threading import RLock
from pr_os_research_runtime.common import read, write, sealed

_LOCK = RLock()
AVATAR_BYTES = 384 * 1024  # base64 + envelope remains below the 1 MiB IPC frame.

def normalize_avatar(value):
    if not isinstance(value, str) or len(value) > 4 * ((AVATAR_BYTES + 2) // 3):
        raise ValueError('USER_AVATAR_TOO_LARGE')
    if not value:
        return ''
    try:
        data = base64.b64decode(value, validate=True)
        if len(data) > AVATAR_BYTES or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('USER_AVATAR_FORMAT_INVALID')
        if len(data)<33 or data[8:16]!=b'\x00\x00\x00\rIHDR':
            raise ValueError('USER_AVATAR_FORMAT_INVALID')
        width,height=struct.unpack('>II',data[16:24])
        if not (1<=width<=256 and 1<=height<=256):
            raise ValueError('USER_AVATAR_DIMENSIONS_INVALID')
        pos=8;ended=False
        while pos+12<=len(data):
            length=struct.unpack('>I',data[pos:pos+4])[0];end=pos+12+length
            if end>len(data):raise ValueError('USER_AVATAR_FORMAT_INVALID')
            kind=data[pos+4:pos+8]
            if kind==b'acTL' or zlib.crc32(data[pos+4:end-4])!=struct.unpack('>I',data[end-4:end])[0]:
                raise ValueError('USER_AVATAR_FORMAT_INVALID')
            pos=end
            if kind==b'IEND':ended=True;break
        if not ended or pos!=len(data):raise ValueError('USER_AVATAR_FORMAT_INVALID')
        # Reuse the Windows image decoder already shipped with the desktop.
        import clr
        clr.AddReference('System.Drawing')
        from System import Array, Byte
        from System.IO import MemoryStream
        from System.Drawing import Image, Bitmap, Graphics, Color
        from System.Drawing.Imaging import ImageFormat, PixelFormat
        source=MemoryStream(Array[Byte](data));output=MemoryStream()
        image=clean=graphics=None
        try:
            image=Image.FromStream(source,False,True)
            clean=Bitmap(width,height,PixelFormat.Format32bppArgb)
            graphics=Graphics.FromImage(clean);graphics.Clear(Color.Transparent)
            graphics.DrawImageUnscaled(image,0,0)
            clean.Save(output,ImageFormat.Png)
            return base64.b64encode(bytes(output.ToArray())).decode('ascii')
        finally:
            for resource in (graphics,clean,image,output,source):
                if resource is not None:resource.Dispose()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('USER_AVATAR_FORMAT_INVALID') from exc

def validate(config):
    if not isinstance(config, dict) or set(config) != {'username', 'avatar_png', 'hide_expressions'}:
        raise ValueError('USER_PREFERENCES_FIELDS_INVALID')
    name = config['username']
    if not isinstance(name, str) or len(name) > 64 or any(unicodedata.category(c).startswith('C') for c in name):
        raise ValueError('USER_NAME_INVALID')
    if type(config['hide_expressions']) is not bool:
        raise ValueError('USER_PREFERENCES_INVALID')
    return dict(username=unicodedata.normalize('NFC', name).strip(),
                avatar_png=normalize_avatar(config['avatar_png']),
                hide_expressions=config['hide_expressions'])

class UserPreferences:
    def __init__(self, root):
        self.path = Path(root) / 'user_preferences.json'

    def get(self):
        with _LOCK:
            if not self.path.exists():
                return dict(schema_version='LocalUserPreferences-v1', revision=0,
                            config=dict(username='', avatar_png='', hide_expressions=False))
            value = read(self.path)
            validate(value['config'])
            return value

    def save(self, *, config, expected_revision):
        accepted = validate(config)
        with _LOCK:
            current = self.get()
            if type(expected_revision) is not int or expected_revision != current['revision']:
                raise ValueError('USER_PREFERENCES_REVISION_CONFLICT')
            value = sealed(dict(schema_version='LocalUserPreferences-v1',
                                revision=current['revision']+1, config=accepted))
            write(self.path, value)
            return value
