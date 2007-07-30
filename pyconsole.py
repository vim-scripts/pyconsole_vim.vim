# PyConsole project
# Copyright (C) 2007 Michael Graz
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import os, sys, time, mmap, struct, ctypes, ctypes.wintypes, logging, tempfile, threading
import win32api, win32con, win32event, win32process, win32console
user32 = ctypes.windll.user32

_debug = False
if _debug:
    _logging_level = logging.DEBUG
else:
    _logging_level = logging.WARNING

def is_child ():
    return len(sys.argv) >= 4 and sys.argv[1] == '__child__'

# Note: if this module is imported then the logging may be
# started from a different module with different parameters
# TODO how to avoid zero length log files when not debugging
if is_child():
    _filename_log = 'pyconsole_child.log'
else:
    _filename_log = 'pyconsole_parent.log'
_directory_log = tempfile.gettempdir()
logging.basicConfig (level=_logging_level,
    format='%(asctime)s %(levelname)-8s %(message)s\n -- %(pathname)s(%(lineno)d)',
    datefmt='%H:%M:%S',
    filename=os.path.join (_directory_log, _filename_log),
    filemode='w')
logging.info ('starting')

#----------------------------------------------------------------------

class _ConsoleProcessBase:
    def __init__ (self, ipc_key):
        self.ipc_key = ipc_key

    def _initialize (self):
        self.event_p2c_data_empty = self._create_event ('p2c_data_empty')
        self.event_c2p_data_empty = self._create_event ('c2p_data_empty')
        self.event_p2c_data_ready = self._create_event ('p2c_data_ready')
        self.event_c2p_data_ready = self._create_event ('c2p_data_ready')
        self.shmem_p2c = None
        self.shmem_c2p = None
        self.shmem_p2c_bytes_in_use = 0
        self.shmem_c2p_bytes_in_use = 0

    def _create_event (self, name):
        name = 'Global\\%s_%s' % (self.ipc_key, name, )
        return win32event.CreateEvent (None, 0, 0, name)

    def _create_shmem (self, name, access):
        name = "%s_%s" % (self.ipc_key, name, )
        return mmap.mmap (0, 4096, name, access)

#----------------------------------------------------------------------

class ConsoleProcess (_ConsoleProcessBase):
    def __init__ (self, cmd_line, console_update=None, console_update_many=None,
            console_process_end=None, echo=None):
        try:
            self.console_update = console_update
            self.console_update_many = console_update_many
            if not self.console_update and not self.console_update_many:
                raise Exception ('need to pass console_update or console_update_many')
            self.console_process_end = console_process_end
            _ConsoleProcessBase.__init__ (self, os.getpid())
            if echo in [True, False]:
                os.environ['pyconsole_echo'] = str(echo)
            self.console_process_handle = None
            self.y_last = 0
            self._initialize ()
            self._start_remote_output ()
            self._start_console_process (cmd_line)
            self._start_console_monitor ()
        except Exception, e:
            logging.exception ('fatal error')
            self.status_message ('ERROR %s' % e)

    def _start_console_process (self, cmd_line):
        cmd_line = '%s "%s" __child__ %s %s' % (get_python_exe(), get_this_file(), os.getpid(), cmd_line, )
        logging.info ('child cmd_line: %s' % (cmd_line, ))
        flags = win32process.NORMAL_PRIORITY_CLASS
        si = win32process.STARTUPINFO()
        si.dwFlags |= win32con.STARTF_USESHOWWINDOW
        # uncomment the following to allocated console visible
        si.wShowWindow = win32con.SW_HIDE
        # si.wShowWindow = win32con.SW_MINIMIZE
        try:
            tpl_result = win32process.CreateProcess (None, cmd_line, None, None, 0, flags, None, '.', si)
        except:
            self.status_message ('COULD NOT START %s' % cmd_line)
            raise
        self.console_process_handle = tpl_result [0]

    def _start_remote_output (self):
        t = threading.Thread (target=self._remote_output)
        t.setDaemon (True)
        t.start ()

    def _remote_output (self):
        win32event.SetEvent (self.event_c2p_data_empty)
        while True:
            rc = win32event.WaitForSingleObject (self.event_c2p_data_ready, win32event.INFINITE)
            if self.shmem_c2p is None:
                self.shmem_c2p = self._create_shmem ('c2p', mmap.ACCESS_READ)
            lst_msg = shmem_read_text (self.shmem_c2p, 'iii')
            win32event.SetEvent (self.event_c2p_data_empty)
            if not lst_msg:
                continue
            if self.console_update_many:
                self.console_update_many (lst_msg)
                self.y_last = lst_msg[-1][2]
            else:
                for msg in lst_msg:
                    # TODO check for truncated text?
                    msg_type, x, y, text_len, text = msg
                    self.console_update (x, y, text)
                    self.y_last = y

    def write (self, text):
        rc = win32event.WaitForSingleObject (self.event_p2c_data_empty, win32event.INFINITE)
        if self.shmem_p2c is None:
            self.shmem_p2c = self._create_shmem ('p2c', mmap.ACCESS_WRITE)
        self.shmem_p2c.seek (0)
        fmt = 'i'
        max_len = self.shmem_p2c.size() - struct.calcsize(fmt)
        if len(text) > max_len:
            text = text[:max_len]   # TODO warn of truncation
        self.shmem_p2c.write (struct.pack (fmt, len(text)))
        self.shmem_p2c.write (text)
        win32event.SetEvent (self.event_p2c_data_ready)

    def writeline (self, text):
        self.write (text + '\n')

    def _start_console_monitor (self):
        t = threading.Thread (target=self._console_monitor)
        t.setDaemon (True)
        t.start ()

    def _console_monitor (self):
        if not self.console_process_handle:
            return
        win32event.WaitForSingleObject (self.console_process_handle, win32event.INFINITE)
        self.status_message ('ENDED')
        if self.console_process_end:
            self.console_process_end ()

    def status_message (self, text):
        text = 'CONSOLE PROCESS %s' % text
        msg_type = 88
        self.y_last += 1
        x, y = 0, self.y_last
        text_len = len(text)
        if self.console_update_many:
            self.console_update_many ([(msg_type, x, y, text_len, text, )])
        elif self.console_update:
            self.console_update (x, y, text)

#----------------------------------------------------------------------

class _ConsoleChildProcess (_ConsoleProcessBase):
    EVENT_CONSOLE_CARET             = 0x4001
    EVENT_CONSOLE_UPDATE_REGION     = 0x4002
    EVENT_CONSOLE_UPDATE_SIMPLE     = 0x4003
    EVENT_CONSOLE_UPDATE_SCROLL     = 0x4004
    EVENT_CONSOLE_LAYOUT            = 0x4005
    EVENT_CONSOLE_START_APPLICATION = 0x4006
    EVENT_CONSOLE_END_APPLICATION   = 0x4007

    CONSOLE_CARET_SELECTION = 1
    CONSOLE_CARET_VISIBLE   = 2

    def __init__ (self, parent_pid, lst_cmd_line):
        try:
            _ConsoleProcessBase.__init__ (self, parent_pid)
            self.parent_pid = parent_pid
            self._start_parent_monitor ()
            self.cmd_line = ' '.join(lst_cmd_line)
            self.echo = eval (os.environ.get ('pyconsole_echo', 'True'))
            self.child_handle = None
            self.child_pid = None
            self.paused = False
            self.x_max = 0
            self.y_max = 0
            self.y_buffer_max = 0
            self.y_last = 0
            self.y_adjust = 0
            self.y_current = 0
            self.last_event_time = 0
            self._initialize ()
            self._initialize_events ()
            win32console.FreeConsole()
            # alloc 2000 lines ?
            win32console.AllocConsole()
            self.con_stdout = win32console.GetStdHandle (win32console.STD_OUTPUT_HANDLE)
            self.con_stdin = win32console.GetStdHandle (win32console.STD_INPUT_HANDLE)
            win32console.SetConsoleTitle ('console process pid:%s ppid:%s' % (os.getpid(), parent_pid, ))
            # size = win32console.PyCOORDType (X=1000, Y=30)
            # self.con_stdout.SetConsoleScreenBufferSize (size)
            dct_info = self.con_stdout.GetConsoleScreenBufferInfo()
            self.y_buffer_max = dct_info['Size'].Y - 1
            self.con_window = win32console.GetConsoleWindow().handle
            self.set_console_event_hook ()
            self._start_paused_monitor ()
            self._child_create ()
            self._start_remote_input ()
            self.message_pump ()
        except:
            logging.exception ('fatal error')

    def _initialize_events (self):
        self.dct_event = {}
        for k, v in self.__class__.__dict__.items():
            if k.startswith ('EVENT_'):
                self.dct_event[v] = k, getattr(self, k.lower())

    def event_console_caret (self, event_id, id_object, id_child, event_time):
        x = win32api.LOWORD(id_child)
        y = win32api.HIWORD(id_child)
        self.relay_console_cursor (x, y)

    def event_console_update_region (self, event_id, id_object, id_child, event_time):
        left = win32api.LOWORD (id_object)
        top = win32api.HIWORD (id_object)
        right = win32api.LOWORD (id_child)
        bottom = win32api.HIWORD (id_child)
        self.read_console (left, top, right, bottom)

    def read_console (self, left, top, right, bottom):
        coord = win32console.PyCOORDType (X=left, Y=top)
        text = self.con_stdout.ReadConsoleOutputCharacter (Length=(right-left+1), ReadCoord=coord)
        self.relay_console_update (left, top, text)

    def event_console_update_simple (self, event_id, id_object, id_child, event_time):
        x = win32api.LOWORD(id_object)
        y = win32api.HIWORD(id_object)
        char = win32api.LOWORD(id_child)
        attr = win32api.HIWORD(id_child)
        self.relay_console_update (x, y, chr(char))

    def event_console_update_scroll (self, event_id, horizontal, vertical, event_time):
        pass

    def event_console_layout (self, *args):
        pass

    def event_console_start_application (self, event_id, id_object, id_child, event_time):
        pass

    def event_console_end_application (self, event_id, id_object, id_child, event_time):
        if id_object == self.child_pid:
            os._exit (0)

    def console_event_hook (self, win_event_hook, event_id, window, id_object, id_child,
            event_thread, event_time):
        if window != self.con_window:
            return
        name, fcn = self.dct_event [event_id]
        fcn (event_id, id_object, id_child, event_time)

    def set_console_event_hook (self):
        self.console_event_hook_cfunc = ctypes.CFUNCTYPE (ctypes.c_voidp,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int) (self.console_event_hook)
        self.handle = user32.SetWinEventHook (self.EVENT_CONSOLE_CARET, self.EVENT_CONSOLE_END_APPLICATION,
                0, self.console_event_hook_cfunc, 0, 0, 0)

    def _child_create (self):
        flags = win32process.NORMAL_PRIORITY_CLASS
        tpl_result = win32process.CreateProcess (None, self.cmd_line, None, None, 0,
            flags, None, '.', win32process.STARTUPINFO())
        self.child_handle = tpl_result [0]
        self.child_pid = tpl_result [2]

    def _start_parent_monitor (self):
        t = threading.Thread (target=self._parent_monitor)
        t.setDaemon (True)
        t.start ()

    def _parent_monitor (self):
        if not self.parent_pid:
            return
        # need to give child process a chance to start
        time.sleep (0.1)  # TODO better implementation
        parent_handle = win32api.OpenProcess (win32con.SYNCHRONIZE, 0, int(self.parent_pid))
        win32event.WaitForSingleObject (parent_handle, win32event.INFINITE)
        win32api.TerminateProcess (self.child_handle, 0)
        win32api.CloseHandle (self.child_handle)

    def _start_remote_input (self):
        t = threading.Thread (target=self._remote_input)
        t.setDaemon (True)
        t.start ()

    def _remote_input (self):
        while True:
            win32event.SetEvent (self.event_p2c_data_empty)
            rc = win32event.WaitForSingleObject (self.event_p2c_data_ready, win32event.INFINITE)
            if self.shmem_p2c is None:
                self.shmem_p2c = self._create_shmem ('p2c', mmap.ACCESS_READ)
            self.shmem_p2c.seek (0)
            fmt = 'i'
            length = struct.unpack (fmt, self.shmem_p2c.read (struct.calcsize(fmt)))[0]
            size = min (length, self.shmem_p2c.size() - self.shmem_p2c.tell())
            text = self.shmem_p2c.read (size)
            self.console_input (text)

    def _start_paused_monitor (self):
        self.event_paused = self._create_event ('paused')
        t = threading.Thread (target=self._paused_monitor)
        t.setDaemon (True)
        t.start ()

    def _paused_monitor (self):
        while True:
            rc = win32event.WaitForSingleObject (self.event_paused, win32event.INFINITE)
            busy = True
            while busy:
                time.sleep (0.2)
                dct_info = self.con_stdout.GetConsoleScreenBufferInfo()
                cursor_position = dct_info['CursorPosition']
                y_actual = cursor_position.Y
                # TODO: better solution than relying on this fudge factor
                if self.y_current >= (y_actual - 10):
                    break
            self.do_resume ()

    def console_input (self, text):
        # TODO if in paused state, buffer any input until unpaused
        if self.paused:
            self.do_resume ()
        lst_input = []
        for c in text:
            if c == '\n':
                lst_input.append (_input_key_return)
            lst_input.append (make_input_key(c))
        self.con_stdin.WriteConsoleInput (lst_input)

    def pause (self):
        self.con_stdin.WriteConsoleInput ([_input_key_pause])

    def resume (self):
        self.con_stdin.WriteConsoleInput ([_input_key_escape])

    def do_resume (self):
        self.y_adjust += self.y_last
        self.x_max = 0
        self.y_max = 0
        self.y_last = 0
        self.paused = False
        self.clear ()
        self.resume ()

    def clear (self):
        zero = win32console.PyCOORDType (X=0, Y=0)
        self.con_stdout.SetConsoleCursorPosition (zero)
        dct_info = self.con_stdout.GetConsoleScreenBufferInfo()
        size = dct_info['Size']
        length = size.X * size.Y
        self.con_stdout.FillConsoleOutputCharacter (u' ', length, zero)

    def message_pump (self):
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageA (ctypes.byref(msg), 0, 0, 0):
            user32.TranslateMessage (ctypes.byref(msg))
            user32.DispatchMessageA (ctypes.byref(msg))

    def y_adjustment (self, y):
        self.y_last = max (y, self.y_last)
        return y + self.y_adjust

    def relay_console_cursor (self, x, y):
        self.y_current = y
        y = self.y_adjustment (y)

    def relay_console_update (self, x, y, text):
        text_len = len(text)
        text = text.rstrip ()
        if not text:
            return
        if len(text) < text_len:
            text += ' '
        same_line = False
        if y < self.y_max:
            return
        elif y > self.y_max:
            self.x_max = 0
            self.y_max = y
        else:
            # here: update on same line
            same_line = True
            if x < self.x_max:
                cut = self.x_max - x
                text = text[cut:]
                if not text:
                    return
            elif x > self.x_max:
                pad = x - self.x_max
                text = ' ' * pad + text
            x = self.x_max
        self.x_max += len(text)

        self.y_current = y
        y = self.y_adjustment (y)
        self.relay (77, x, y, text)
        # TODO - y greater than what ?
        if self.y_current > 400:
            if not self.paused:
                self.paused = True
                self.pause ()
                win32event.SetEvent (self.event_paused)

    def relay (self, msg_type, x, y, text):
        if self.shmem_c2p is None:
            self.shmem_c2p = self._create_shmem ('c2p', mmap.ACCESS_WRITE)

        lst_event = [self.event_c2p_data_empty, self.event_c2p_data_ready]
        rc = win32event.WaitForMultipleObjects (lst_event, 0, win32event.INFINITE)
        event_num = rc - win32event.WAIT_OBJECT_0
        if event_num == 0:
            self.shmem_c2p_bytes_in_use = 0
        # write guaranteed to work if empty, otherwise failure possible
        new_bytes_in_use = shmem_write_text (self.shmem_c2p, self.shmem_c2p_bytes_in_use,
                'iii', (msg_type, x, y, ), text)
        if new_bytes_in_use == self.shmem_c2p_bytes_in_use:
            # write could not be completed - shmem full.  wait for empty
            win32event.SetEvent (self.event_c2p_data_ready)
            rc = win32event.WaitForSingleObject (self.event_c2p_data_empty, win32event.INFINITE)
            self.shmem_c2p_bytes_in_use = shmem_write_text (self.shmem_c2p, 0,
                    'iii', (msg_type, x, y, ), text)
        else:
            self.shmem_c2p_bytes_in_use = new_bytes_in_use
        win32event.SetEvent (self.event_c2p_data_ready)

#----------------------------------------------------------------------

_shmem_hdr_fmt = 'i'     # btyes_used
_shmem_hdr_len = struct.calcsize (_shmem_hdr_fmt)

def shmem_write_text (shmem, bytes_in_use, msg_hdr_fmt, msg_hdr_tpl, msg_text):
    '''Returns bytes_in_use.  If it is the same as what was passed in then
    write was not successful because of too much data.
    The first write can only be successful (if the message is too big it will be
    truncated.  Successive writes however can be unsuccessful'''
    bytes_in_use = max (bytes_in_use, _shmem_hdr_len)
    msg_hdr_fmt += 'i'  # int indicating length of text
    msg_hdr_len = struct.calcsize (msg_hdr_fmt)
    msg_hdr_tpl += (len(msg_text), )
    msg_len = msg_hdr_len + len(msg_text)
    shmem_data_size = shmem.size () - _shmem_hdr_len
    if msg_len > shmem_data_size:
        # this results in the text portion of the msg being truncated
        # client reader can detect truncation by seeing actual msg length
        # being less that length indicated in msg_hdr
        msg_len = shmem_data_size
    if msg_len + bytes_in_use > shmem.size():
        return bytes_in_use    # not enough space, need to wait
    shmem.seek (bytes_in_use)
    msg_hdr = struct.pack (msg_hdr_fmt, *msg_hdr_tpl)
    shmem.write (msg_hdr)
    # need to truncate text if it would overflow buffer
    bytes_remaining = shmem.size() - shmem.tell()
    shmem.write (msg_text[:bytes_remaining])
    # update the bytes in use
    bytes_in_use = shmem.tell ()
    shmem.seek (0)
    shmem.write (struct.pack (_shmem_hdr_fmt, bytes_in_use))
    return bytes_in_use

def shmem_read_text (shmem, msg_hdr_fmt):
    lst_msg = []
    msg_hdr_fmt += 'i'  # int indicating length of text
    msg_hdr_len = struct.calcsize (msg_hdr_fmt)
    shmem.seek (0)
    bytes_in_use = struct.unpack (_shmem_hdr_fmt, shmem.read (_shmem_hdr_len))[0]
    while shmem.tell() < bytes_in_use:
        msg_hdr = struct.unpack (msg_hdr_fmt, shmem.read (msg_hdr_len))
        msg_text = shmem.read (msg_hdr[-1])
        msg_tpl = msg_hdr + (msg_text, )
        lst_msg.append (msg_tpl)
    return lst_msg

def get_this_file ():
    try: fn = __file__
    except: fn = sys.argv[0]
    return os.path.abspath (fn)

def get_python_exe ():
    exe = os.path.basename(sys.executable).lower()
    if exe in ['python.exe', 'pythonw.exe']:
        return exe
    # when run under gvim the executable is gvim.exe
    python_exe = 'python.exe'
    key = "SOFTWARE\\Python\\PythonCore\\%s\\InstallPath" % sys.winver
    try:
        value = win32api.RegQueryValue (win32con.HKEY_LOCAL_MACHINE, key)
        return os.path.join (value, python_exe)
    except win32api.error:
        pass
    return python_exe

def make_input_key (c, control_key_state=None):
    input_key = win32console.PyINPUT_RECORDType (win32console.KEY_EVENT)
    try:
        input_key.Char = unicode(c)
    except:
        input_key.Char = unicode(' ')
    input_key.VirtualKeyCode = user32.VkKeyScanA(ord(c))
    input_key.KeyDown = True
    input_key.RepeatCount = 1
    if control_key_state:
        input_key.ControlKeyState = control_key_state
    return input_key

_input_key_return = win32console.PyINPUT_RECORDType (win32console.KEY_EVENT)
_input_key_return.Char = u'\r'
_input_key_return.VirtualKeyCode = win32con.VK_RETURN
_input_key_return.KeyDown = True
_input_key_return.RepeatCount = 1

_input_key_pause = win32console.PyINPUT_RECORDType (win32console.KEY_EVENT)
_input_key_pause.Char = unicode(chr(0))
_input_key_pause.VirtualKeyCode = win32con.VK_PAUSE
_input_key_pause.KeyDown = True
_input_key_pause.RepeatCount = 1

_input_key_escape = win32console.PyINPUT_RECORDType (win32console.KEY_EVENT)
_input_key_escape.Char = unicode(chr(27))
_input_key_escape.VirtualKeyCode = win32con.VK_ESCAPE
_input_key_escape.KeyDown = True
_input_key_escape.RepeatCount = 1

#----------------------------------------------------------------------

if __name__ == '__main__':
    if is_child ():
        _ConsoleChildProcess(parent_pid=sys.argv[2], lst_cmd_line=sys.argv[3:])
    else:
        print 'not expecting to be run directly ...'

