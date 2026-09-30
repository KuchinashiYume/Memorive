"""Read bitmap alpha without per-pixel Python/CLR crossings. Never modify pixels."""
import ctypes

def bitmap_alpha_rows(bitmap):
    from System.Drawing import Rectangle
    from System.Drawing.Imaging import ImageLockMode,PixelFormat
    width,height=int(bitmap.Width),int(bitmap.Height)
    if width<1 or height<1 or bitmap.PixelFormat!=PixelFormat.Format32bppArgb:
        raise ValueError('ASSISTANT_ALPHA_FORMAT_INVALID')
    bits=bitmap.LockBits(Rectangle(0,0,width,height),ImageLockMode.ReadOnly,PixelFormat.Format32bppArgb)
    try:
        stride=int(bits.Stride);address=int(bits.Scan0.ToInt64())
        if not address or abs(stride)<width*4 or int(bits.Width)!=width or int(bits.Height)!=height:
            raise ValueError('ASSISTANT_ALPHA_BUFFER_INVALID')
        # Scan0 is the first logical row; retain the signed stride for bottom-up storage.
        return tuple(ctypes.string_at(address+y*stride,width*4)[3::4] for y in range(height))
    finally:
        bitmap.UnlockBits(bits)
def pet_canvas_bounds(client_width: int, client_height: int) -> tuple[int, int, int]:
    """One centred square for both the sprite and its native input mask."""
    width, height = max(1, int(client_width)), max(1, int(client_height))
    side = min(width, height)
    return (width - side) // 2, (height - side) // 2, side


class LayeredPetSurface:
    """Submit premultiplied pixels to DWM without a colour-key matte.

    The caller owns the window and sprite; this object owns only one bounded
    native DC/DIB pair, reused until the client size changes. UI thread only.
    """

    def __init__(self, form):
        from ctypes import wintypes as w
        self.form = form
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        self.hwnd = 0
        self.dc = self.dib = self.previous = self.pixels = None
        self.size = None
        self.updates = 0
        self.user.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
        self.user.GetWindowLongW.restype = ctypes.c_long
        self.user.SetWindowLongW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_long]
        self.user.SetWindowLongW.restype = ctypes.c_long
        self.user.UpdateLayeredWindow.argtypes = [w.HWND, w.HDC, ctypes.c_void_p,
            ctypes.c_void_p, w.HDC, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, w.DWORD]
        self.user.UpdateLayeredWindow.restype = w.BOOL
        self.gdi.CreateCompatibleDC.argtypes = [w.HDC]
        self.gdi.CreateCompatibleDC.restype = w.HDC
        self.gdi.CreateDIBSection.argtypes = [w.HDC, ctypes.c_void_p, w.UINT,
            ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD]
        self.gdi.CreateDIBSection.restype = w.HANDLE
        self.gdi.SelectObject.argtypes = [w.HDC, w.HANDLE]
        self.gdi.SelectObject.restype = w.HANDLE
        self.gdi.DeleteObject.argtypes = [w.HANDLE]
        self.gdi.DeleteDC.argtypes = [w.HDC]

    def _release_buffer(self):
        if self.dc and self.previous:
            self.gdi.SelectObject(self.dc, self.previous)
        if self.dib:
            self.gdi.DeleteObject(self.dib)
        if self.dc:
            self.gdi.DeleteDC(self.dc)
        self.dc = self.dib = self.previous = self.pixels = None
        self.size = None

    def _buffer(self, width, height):
        if self.size == (width, height):
            return
        import struct
        self._release_buffer()
        self.dc = self.gdi.CreateCompatibleDC(None)
        # Negative height selects top-down BGRA pixels (no vertical flip).
        header = ctypes.create_string_buffer(struct.pack('<IiiHHIIiiII',
            40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0))
        pixels = ctypes.c_void_p()
        self.dib = self.gdi.CreateDIBSection(self.dc, header, 0, ctypes.byref(pixels), None, 0)
        if not self.dc or not self.dib or not pixels.value:
            error = ctypes.get_last_error()
            self._release_buffer()
            raise OSError(error, 'ASSISTANT_ALPHA_BUFFER_CREATE_FAILED')
        self.previous = self.gdi.SelectObject(self.dc, self.dib)
        if not self.previous or self.previous == ctypes.c_void_p(-1).value:
            self.previous = None
            self._release_buffer()
            raise OSError('ASSISTANT_ALPHA_BUFFER_SELECT_FAILED')
        self.pixels = pixels.value
        self.size = (width, height)

    def present(self, bitmap):
        from System.Drawing import Bitmap, Color, Graphics, GraphicsUnit, Rectangle
        from System.Drawing.Drawing2D import CompositingMode, InterpolationMode, PixelOffsetMode
        from System.Drawing.Imaging import ImageLockMode, PixelFormat
        width, height = int(self.form.ClientSize.Width), int(self.form.ClientSize.Height)
        if width < 1 or height < 1 or self.form.IsDisposed:
            return
        hwnd = int(self.form.Handle.ToInt64())
        if hwnd != self.hwnd:
            style = self.user.GetWindowLongW(hwnd, -20)
            # Clear prior SetLayeredWindowAttributes state before switching to
            # UpdateLayeredWindow; Windows otherwise rejects ULW_ALPHA.
            self.user.SetWindowLongW(hwnd, -20, style & ~0x80000)
            self.user.SetWindowLongW(hwnd, -20, style | 0x80000)
            self.hwnd = hwnd
        self._buffer(width, height)
        target = Bitmap(width, height, PixelFormat.Format32bppPArgb)
        try:
            graphics = Graphics.FromImage(target)
            try:
                graphics.Clear(Color.Transparent)
                graphics.CompositingMode = CompositingMode.SourceCopy
                graphics.InterpolationMode = InterpolationMode.HighQualityBicubic
                graphics.PixelOffsetMode = PixelOffsetMode.HighQuality
                left, top, side = pet_canvas_bounds(width, height)
                graphics.DrawImage(bitmap, Rectangle(left, top, side, side), 0, 0,
                    int(bitmap.Width), int(bitmap.Height), GraphicsUnit.Pixel)
            finally:
                graphics.Dispose()
            bits = target.LockBits(Rectangle(0, 0, width, height), ImageLockMode.ReadOnly,
                PixelFormat.Format32bppPArgb)
            try:
                address, stride = int(bits.Scan0.ToInt64()), int(bits.Stride)
                for row in range(height):
                    ctypes.memmove(self.pixels + row * width * 4, address + row * stride, width * 4)
            finally:
                target.UnlockBits(bits)
        finally:
            target.Dispose()
        size = (ctypes.c_long * 2)(width, height)
        origin = (ctypes.c_long * 2)(0, 0)
        blend = (ctypes.c_ubyte * 4)(0, 0, 255, 1)  # AC_SRC_OVER / AC_SRC_ALPHA
        if not self.user.UpdateLayeredWindow(hwnd, None, None, size, self.dc, origin, 0, blend, 2):
            raise OSError(ctypes.get_last_error(), 'ASSISTANT_ALPHA_PRESENT_FAILED')
        self.updates += 1

    def dispose(self):
        self._release_buffer()
