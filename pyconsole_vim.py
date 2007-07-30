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

import pyconsole

class VimConsole (pyconsole.ConsoleProcess):
    def __init__ (self, cmd_line):
        self.vim = self.get_vim ()
        self.vim_buffer = self.vim.current.buffer
        self.vim_offset = len(self.vim_buffer)
        pyconsole.ConsoleProcess.__init__ (self, cmd_line,
            console_update_many=self.console_update_many)

    def get_vim (self):
        import vim
        return vim

    def console_update_one (self, x, y, text):
        y += self.vim_offset
        if y > len(self.vim_buffer):
            padding = y - len(self.vim_buffer)
            self.vim_buffer.append ([''] * padding)
        if y == len(self.vim_buffer):
            self.vim_buffer.append (text)
        else:
            line = line_replace(self.vim_buffer[y], x, text)
            if self.vim_buffer[y] != line:
                self.vim_buffer[y] = line

    def console_update_many (self, lst_msg):
        for msg_type, x, y, text_len, text in lst_msg:
            self.console_update_one (x, y, text)
        row = len(self.vim_buffer)
        col = len(self.vim_buffer[row-1])
        window = self.get_window ()
        if not window:
            return
        window.cursor = (row, col)
        self.vim.command ('redraw')
        self.vim.command ('let g:console_process_row=%s' % (row, ))
        self.row_last = row
        self.col_last = col

    def get_window (self):
        '''find first window containing buffer'''
        for window in self.vim.windows:
            if window.buffer == self.vim_buffer:
                return window
        return None

    def exec_line (self):
        '''execute the current line'''
        text = self.user_input ()
        command = remove_backpaces (text)
        self.write ('%s\n' % (command, ))

    def exec_part (self):
        '''execute partial line - for tab completions'''
        text = self.user_input ()
        command = remove_backpaces (text)
        self.write ('%s\t' % (command, ))

    def user_input (self):
        window = self.get_window ()
        if not window:
            return
        row, col = window.cursor
        col += 1
        line = self.vim_buffer [row - 1]
        if row == self.row_last:
            if col <= self.col_last:
                return ''
            return line [self.col_last:col]
        return line [:col]

#----------------------------------------------------------------------

def line_replace (line, x, text):
    if x == 0 and len(line) == 0:
        return text
    part2 = ''
    if x > len(line):
        padding = x - len(line)
        part1 = line + ' ' * padding
    elif x == len(line):
        part1 = line
    else:
        part1 = line[:x]
        part2 = line[x+len(text):]
    return '%s%s%s' % (part1, text, part2, )

def remove_backpaces (s):
    s = s.replace ('\x80kb', '\b')  # window bs comes in funny
    try:
        s.index ('\b')
    except ValueError:
        return s
    lst_c = []
    for c in s:
        if c == '\b':
            if len(lst_c):
                del lst_c[-1]
        else:
            lst_c.append (c)
    return ''.join (lst_c)

