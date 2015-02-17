#!/usr/bin/env python
__author__ = 'Matias Carrasco Kind'
__version__= '1.0.0'
#  TODO:
# upload table from fits
# clean up, comments
# readline bug (GNU vs libedit)

import warnings
warnings.filterwarnings("ignore")
import cmd
import cx_Oracle
import sys
import os
import re
import dircache
import threading
import time
import getpass
try:
    from termcolor import colored
except:
    def colored(line,color) : return line
import pandas as pd
import datetime
import pyfits as pf
import argparse
import config as config_mod
import types

#FILES
ea_path=os.path.join(os.environ["HOME"], ".easyacess/")
if not os.path.exists(ea_path):os.makedirs(ea_path)
history_file = os.path.join(os.environ["HOME"], ".easyacess/history")
if not os.path.exists(history_file): os.system('echo $null >> '+history_file)
config_file = os.path.join(os.environ["HOME"], ".easyacess/config.ini")
if not os.path.exists(config_file): os.system('echo $null >> '+config_file)
desfile = os.getenv("DES_SERVICES")
if not desfile : desfile = os.path.join(os.getenv("HOME"),".desservices.ini")



or_n = cx_Oracle.NUMBER
or_s = cx_Oracle.STRING
or_f = cx_Oracle.NATIVE_FLOAT
or_o = cx_Oracle.OBJECT

options_prefetch = ['show', 'set', 'default']
options_edit = ['show', 'set_editor']
options_out = ['csv', 'tab', 'fits', 'h5']
options_def = ['Coma separated value', 'space separated value', 'Fits format', 'HDF5 format']
type_dict = {'float64': 'D', 'int64': 'K', 'float32': 'E', 'int32': 'J', 'object': '200A', 'int8': 'I'}


def _complete_path(line):
    line = line.split()
    if len(line) < 2:
        filename = ''
        path = './'
    else:
        path = line[1]
        if '/' in path:
            i = path.rfind('/')
            filename = path[i + 1:]
            path = path[:i]
        else:
            filename = path
            path = './'
    ls = dircache.listdir(path)
    ls = ls[:]
    dircache.annotate(path, ls)
    if filename == '':
        return ls
    else:
        return [f for f in ls if f.startswith(filename)]


def read_buf(fbuf):
    """
    Read SQL files, sql statement should end with ; if parsing to a file to write
    """
    try:
        with open(fbuf) as f:
            content = f.read()
    except:
        print '\n' + 'Fail to load the file "{:}"'.format(fbuf)
        return ""
    list = [item for item in content.split('\n')]
    newquery = ''
    for line in list:
        if line[0:2] == '--': continue
        newquery += ' ' + line.split('--')[0]
    #newquery = newquery.split(';')[0]
    return newquery


def change_type(info):
    if info[1] == or_n:
        if info[5] == 0 and info[4] >= 10:
            return "int64"
        elif info[5] == 0 and info[4] >= 3:
            return "int32"
        elif info[5] == 0 and info[4] >= 1:
            return "int8"
        elif info[5] > 0 and info[5] <= 5:
            return "float32"
        else:
            return "float64"
    elif info[1] == or_f:
        if info[3] == 4:
            return "float32"
        else:
            return "float64"
    else:
        return ""


def write_to_fits(df, fitsfile, mode='w', listN=[], listT=[]):
    if mode == 'w':
        C = pf.ColDefs([])
        for col in df:
            type_df = df[col].dtype.name
            if col in listN:
                fmt = listT[listN.index(col)]
            else:
                fmt = type_dict[type_df]
            CC = pf.Column(name=col, format=fmt, array=df[col].values)
            C.add_col(CC)
        SS = pf.BinTableHDU.from_columns(C)
        SS.writeto(fitsfile, clobber=True)
    if mode == 'a':
        Htemp = pf.open(fitsfile)
        nrows1 = Htemp[1].data.shape[0]
        ntot = nrows1 + len(df)
        SS = pf.BinTableHDU.from_columns(Htemp[1].columns, nrows=ntot)
        for colname in Htemp[1].columns.names:
            SS.data[colname][nrows1:] = df[colname].values
        SS.writeto(fitsfile, clobber=True)


class easy_or(cmd.Cmd, object):
    """cx_oracle interpreter for DESDM"""
    intro = colored("\nThe DESDM Database shell.  Type help or ? to list commands.\n", "cyan")

    def __init__(self, conf , desconf, db,interactive=True):
        cmd.Cmd.__init__(self)
        self.writeconfig = False
        self.config=conf
        self.desconfig = desconf
        self.editor = os.getenv('EDITOR', self.config.get('easyaccess','editor'))
        self.timeout=self.config.getint('easyaccess','timeout')
        self.prefetch = self.config.getint('easyaccess','prefetch')
        self.dbname = db
        self.savePrompt = colored('_________', 'cyan') + '\nDESDB ~> '
        self.prompt = self.savePrompt
        self.buff = None
        self.interactive = interactive
        self.undoc_header = None
        self.doc_header = ' *General Commands* (type help <command>):'
        self.docdb_header = '\n*DB Commands* (type help <command>):'
        #connect to db  
        self.user = self.desconfig.get('db-'+self.dbname,'user')
        self.dbhost = self.desconfig.get('db-'+self.dbname,'server')
        self.port = self.desconfig.get('db-'+self.dbname,'port')
        self.password = self.desconfig.get('db-'+self.dbname,'passwd')
        kwargs = {'host': self.dbhost, 'port': self.port, 'service_name': self.dbname}
        dsn = cx_Oracle.makedsn(**kwargs)
        print 'Connecting to DB...'
        self.con = cx_Oracle.connect(self.user, self.password, dsn=dsn)
        self.cur = self.con.cursor()
        self.cur.arraysize = self.prefetch


    ### OVERRIDE CMD METHODS

    def cmdloop(self, intro=None):
        """Repeatedly issue a prompt, accept input, parse an initial prefix
        off the received input, and dispatch to action methods, passing them
        the remainder of the line as argument.

        """
        self.preloop()
        if self.use_rawinput and self.completekey:
            try:
                import readline
                self.old_completer = readline.get_completer()
                readline.set_completer(self.complete)
                #readline.parse_and_bind(self.completekey+": complete")
                if 'libedit' in readline.__doc__:
                    # readline linked to BSD libedit
                    if self.completekey == 'tab':
                        key = '^I'
                    else:
                        key = self.completekey
                    readline.parse_and_bind("bind %s rl_complete"%(key,))
                else:
                    # readline linked to the real readline
                    readline.parse_and_bind(self.completekey+": complete")
            except ImportError:
                pass
        try:
            if intro is not None:
                self.intro = intro
            if self.intro:
                self.stdout.write(str(self.intro)+"\n")
            stop = None
            while not stop:
                if self.cmdqueue:
                    line = self.cmdqueue.pop(0)
                else:
                    if self.use_rawinput:
                        try:
                            line = raw_input(self.prompt)
                        except EOFError:
                            line = 'EOF'
                    else:
                        self.stdout.write(self.prompt)
                        self.stdout.flush()
                        line = self.stdin.readline()
                        if not len(line):
                            line = 'EOF'
                        else:
                            line = line.rstrip('\r\n')
                line = self.precmd(line)
                stop = self.onecmd(line)
                stop = self.postcmd(stop, line)
            self.postloop()
        finally:
            if self.use_rawinput and self.completekey:
                try:
                    import readline
                    readline.set_completer(self.old_completer)
                except ImportError:
                    pass

    def do_help(self, arg):
        'List available commands with "help" or detailed help with "help cmd".'
        if arg:
            # XXX check arg syntax
            try:
                func = getattr(self, 'help_' + arg)
            except AttributeError:
                try:
                    doc=getattr(self, 'do_' + arg).__doc__
                    if doc:
                        doc=str(doc)
                        if doc.find('DB:')> -1: doc=doc.replace('DB:','')
                        self.stdout.write("%s\n"%str(doc))
                        return
                except AttributeError:
                    pass
                self.stdout.write("%s\n"%str(self.nohelp % (arg,)))
                return
            func()
        else:
            names = self.get_names()
            cmds_doc = []
            cmds_undoc = []
            cmds_db = []
            help = {}
            for name in names:
                if name[:5] == 'help_':
                    help[name[5:]]=1
            names.sort()
            # There can be duplicates if routines overridden
            prevname = ''
            for name in names:
                if name[:3] == 'do_':
                    if name == prevname:
                        continue
                    prevname = name
                    cmd=name[3:]
                    if cmd in help:
                        cmds_doc.append(cmd)
                        del help[cmd]
                    elif getattr(self, name).__doc__:
                        doc = getattr(self, name).__doc__
                        if doc.find('DB:') > -1: cmds_db.append(cmd)
                        else: cmds_doc.append(cmd)
                    else:
                        cmds_undoc.append(cmd)
            self.stdout.write("%s\n"%str(self.doc_leader))
            self.print_topics(self.doc_header,   cmds_doc,   15,80)
            self.print_topics(self.docdb_header,   cmds_db,   15,80)
            self.print_topics(self.misc_header,  help.keys(),15,80)
            self.print_topics(self.undoc_header, cmds_undoc, 15,80)

            print "\n* To run queries just add ; at the end of query"
            print "* To write to a file after ; append > filename"
            print "* To see supported output files format "



    def print_topics(self, header, cmds, cmdlen, maxcol):
        if header is not None:
            if cmds:
                self.stdout.write("%s\n" % str(header))
                if self.ruler:
                    self.stdout.write("%s\n" % str(self.ruler * len(header)))
                self.columnize(cmds, maxcol - 1)
                self.stdout.write("\n")


    def preloop(self):
        """
        Initialization before prompting user for commands.
        Despite the claims in the Cmd documentation, Cmd.preloop() is not a stub.
        """
        cmd.Cmd.preloop(self)  # # sets up command completion
        create_metadata=False
        check='select count(table_name) from user_tables where table_name = \'FGOTTENMETADATA\''
        self.cur.execute(check)
        if self.cur.fetchall()[0][0] == 0: create_metadata = True
        else:
            query_time = "select created from dba_objects where object_name = \'FGOTTENMETADATA\' and owner =\'%s\'  " % (self.user.upper())
            qt = self.cur.execute(query_time)
            last = qt.fetchall()
            now = datetime.datetime.now()
            diff = abs(now - last[0][0]).seconds / (3600.)
            if diff >= 24: create_metadata = True
        if create_metadata:
            query_2 = """create table fgottenmetadata  as  select * from table (fgetmetadata)"""
            self.cur.execute(query_2)

        print 'Loading metadata into cache...'
        self.cache_table_names = self.get_tables_names()
        self.cache_usernames = self.get_userlist()
        self.cache_column_names = self.get_columnlist()
        #history
        ht=open(history_file,'r')
        Allq=ht.readlines()
        ht.close()
        self._hist = []
        for lines in Allq : self._hist.append(lines.strip())
        self._locals = {}  # # Initialize execution namespace for user
        self._globals = {}


    def precmd(self, line):
        """ This method is called after the line has been input but before
             it has been interpreted. If you want to modifdy the input line
             before execution (for example, variable substitution) do it here.
         """

        # handle line continuations -- line terminated with \
        # beware of null lines.
        line = ' '.join(line.split())
        self.buff = line
        while line and line[-1] == "\\":
            self.buff = self.buff[:-1]
            line = line[:-1]  # strip terminal \
            temp = raw_input('...')
            self.buff += '\n' + temp
            line += temp

        #self.prompt = self.savePrompt

        if not line: return ""  # empty line no need to go further
        if line[0] == "@":
            if len(line) >= 1:
                fbuf = line[1:].split()[0]
                line = read_buf(fbuf)
                self.buff = line
                print
                print line
            else:
                print '@ must be followed by a filename'
                return ""

        # support model_query Get
        #self.prompt = self.savePrompt

        self._hist += [line.strip()]
        return line

    def emptyline(self):
        pass

    def default(self, line):
        fend = line.find(';')
        if fend > -1:
            #with open('easy.buf', 'w') as filebuf:
            #filebuf.write(self.buff)
            query = line[:fend]
            if line[fend:].find('>') > -1:
                try:
                    fileout = line[fend:].split('>')[1].strip().split()[0]
                    fileformat = fileout.split('.')[-1]
                    if fileformat in options_out:
                        print '\nFetching data and saving it to %s ...' % fileout + '\n'
                        self.query_and_save(query, fileout, mode=fileformat)
                    else:
                        print colored('\nFile format not valid.\n', 'red')
                        print 'Supported formats:\n'
                        for jj, ff in enumerate(options_out): print '%5s  %s' % (ff, options_def[jj])
                except:
                    print colored('\nMust indicate output file\n', "red")
                    print 'Format:\n'
                    print 'select ... from ... where ... ; > example.csv \n'
            else:
                self.query_and_print(query)

        else:
            print
            print 'Invalid command or missing ; at the end of query.'
            print 'Type help or ? to list commands'
            print

    def completedefault(self, text, line, begidx, lastidx):
        qstop = line.find(';')
        if qstop > -1:
            if line[qstop:].find('>') > -1:
                line = line[qstop+1:]
                return _complete_path(line)
        if line[0]=='@':
            line='@ '+line[1:]
            return _complete_path(line)
        if line.upper().find('SELECT') > -1:
            #return self._complete_colnames(text)
            if line.upper().find('FROM') == -1:
                return self._complete_colnames(text)
            elif line.upper().find('FROM') > -1 and line.upper().find('WHERE') == -1:
                return self._complete_tables(text)
            else:
                return self._complete_colnames(text)
        else:
            return self._complete_tables(text)



    ### QUERY METHODS

    def query_and_print(self, query, print_time=True, err_arg='No rows selected', suc_arg='Done!'):
        self.cur.arraysize = self.prefetch
        tt = threading.Timer(self.timeout,self.con.cancel)
        tt.start()
        t1 = time.time()
        try:
            self.cur.execute(query)
            if self.cur.description != None:
                header = [columns[0] for columns in self.cur.description]
                htypes = [columns[1] for columns in self.cur.description]
                info = [rec[1:6] for rec in self.cur.description]
                data = pd.DataFrame(self.cur.fetchall())
                t2 = time.time()
                tt.cancel()
                print
                if print_time: print colored('%d rows in %.2f seconds' % (len(data), (t2 - t1)), "green")
                if print_time: print
                if len(data) == 0:
                    fline = '   '
                    for col in header: fline += '%s  ' % col
                    print fline
                    print colored(err_arg, "red")
                else:
                    data.columns = header
                    data.index += 1
                    print data
            else:
                t2 = time.time()
                tt.cancel()
                print colored(suc_arg, "green")
                self.con.commit()
            print
        except:
            t2 = time.time()
            (type, value, traceback) = sys.exc_info()
            print
            print colored(type, "red")
            print colored(value, "red")
            print
            if t2-t1 > self.timeout :
                print '\nQuery is taking too long for printing on screen'
                print 'Try to output the results to a file'
                print 'Using > FILENAME after query, ex: select from ... ; > test.csv'
                print 'To see a list of compatible format\n'


    def query_and_save(self, query, fileout, mode='csv', print_time=True):
        self.cur.arraysize = self.prefetch
        t1 = time.time()
        try:
            self.cur.execute(query)
            if self.cur.description != None:
                header = [columns[0] for columns in self.cur.description]
                htypes = [columns[1] for columns in self.cur.description]
                info = [rec[0:6] for rec in self.cur.description]
                first = True
                mode_write = 'w'
                header_out = True
                com_it = 0
                while True:
                    data = pd.DataFrame(self.cur.fetchmany())
                    com_it += 1
                    if first:
                        list_names = []
                        list_type = []
                        for inf in info:
                            if inf[1] == or_s:
                                list_names.append(inf[0])
                                list_type.append(str(inf[3]) + 'A')
                    if not data.empty:
                        data.columns = header
                        for jj, col in enumerate(data):
                            nt = change_type(info[jj])
                            if nt != "": data[col] = data[col].astype(nt)
                        if mode == 'csv': data.to_csv(fileout, index=False, float_format='%.6f', sep=',',
                                                      mode=mode_write, header=header_out)
                        if mode == 'tab': data.to_csv(fileout, index=False, float_format='%.6f', sep=' ',
                                                      mode=mode_write, header=header_out)
                        if mode == 'h5':  data.to_hdf(fileout, 'data', mode=mode_write, index=False,
                                                      header=header_out)  #, complevel=9,complib='bzip2'
                        if mode == 'fits': write_to_fits(data, fileout, mode=mode_write, listN=list_names,
                                                         listT=list_type)
                        if first:
                            mode_write = 'a'
                            header_out = False
                            first = False
                    else:
                        break
                t2 = time.time()
                elapsed = '%.1f seconds' % (t2 - t1)
                print
                if print_time: print colored('\n Written %d rows to %s in %.2f seconds and %d trips' % (
                    self.cur.rowcount, fileout, (t2 - t1), com_it - 1), "green")
                if print_time: print
            else:
                pass
            print
        except:
            (type, value, traceback) = sys.exc_info()
            print
            print colored(type, "red")
            print colored(value, "red")
            print


    def query_results(self, query):
        self.cur.execute(query)
        data = self.cur.fetchall()
        return data

    def get_tables_names(self):
        query = """
        select distinct table_name from fgottenmetadata
        union select distinct t1.owner || '.' || t1.table_name from all_tab_cols t1,
        des_users t2 where upper(t1.owner)=upper(t2.username) and t1.owner not in ('DES_ADMIN')"""
        #where owner not in ('XDB','SYSTEM','SYS', 'DES_ADMIN', 'EXFSYS','')
        temp = self.cur.execute(query)
        tnames = pd.DataFrame(temp.fetchall())
        table_list = tnames.values.flatten().tolist()
        return table_list

    def get_tables_names_user(self, user):
        query = "select distinct table_name from all_tables where owner=\'%s\' order by table_name" % user.upper()
        temp = self.cur.execute(query)
        tnames = pd.DataFrame(temp.fetchall())
        if len(tnames) > 0:
            print '\nTables from %s' % user.upper()
            print tnames
            #Add tname to cache (no longer needed)
            #table_list=tnames.values.flatten().tolist()
            #for table in table_list:
            #    tn=user.upper()+'.'+table.upper()
            #    try : self.cache_table_names.index(tn)
            #    except: self.cache_table_names.append(tn)
            #self.cache_table_names.sort()
        else:
            print 'User %s has no tables' % user.upper()

    def get_userlist(self):
        query = 'select distinct username from des_users order by username'
        temp = self.cur.execute(query)
        tnames = pd.DataFrame(temp.fetchall())
        user_list = tnames.values.flatten().tolist()
        return user_list

    def _complete_tables(self, text):
        options_tables = self.cache_table_names
        if text:
            return [option for option in options_tables if option.startswith(text.upper())]
        else:
            return options_tables

    def _complete_colnames(self, text):
        options_colnames = self.cache_column_names
        if text:
            return [option for option in options_colnames if option.startswith(text.upper())]
        else:
            return options_colnames

    def get_columnlist(self):
        query = """SELECT distinct column_name from fgottenmetadata  order by column_name"""
        temp = self.cur.execute(query)
        cnames = pd.DataFrame(temp.fetchall())
        col_list = cnames.values.flatten().tolist()
        return col_list


    ## DO METHODS
    def do_prefetch(self, line):
        """
        Shows, sets or sets to default the number of prefetch rows from Oracle
        The default is 10000, increasing this number uses more memory but return
        data faster. Decreasing this number reduce memory but increases
        communication trips with database thus slowing the process.

        Usage:
           - prefetch show         : Shows current value
           - prefetch set <number> : Sets the prefetch to <number>
           - prefetch default      : Sets value to 10000
        """
        line = "".join(line.split())
        if line.find('show') > -1:
            print '\nPrefetch value = {:}\n'.format(self.prefetch)
        elif line.find('set') > -1:
            val = line.split('set')[-1]
            if val != '':
                self.prefetch = int(val)
                self.config.set('easyaccess','prefetch',int(val))
                self.writeconfig=True
                print '\nPrefetch value set to  {:}\n'.format(self.prefetch)
        elif line.find('default') > -1:
            self.prefetch = 10000
            self.config.set('easyaccess','prefetch',10000)
            self.writeconfig=True
            print '\nPrefetch value set to default (10000) \n'
        else:
            print '\nPrefetch value = {:}\n'.format(self.prefetch)

    def complete_prefetch(self, text, line, start_index, end_index):
        if text:
            return [option for option in options_prefetch if option.startswith(text)]
        else:
            return options_prefetch


    def do_history(self, arg):
        """
        Print the history buffer to the screen, oldest to most recent.
        IF argument n is present print the most recent N items.

        Usage: history [n]
        """
        if readline_present:
            nall  =readline.get_current_history_length()
            firstprint = 0
            if arg.strip() : firstprint = max(nall - int(arg), 0)
            for index in xrange (firstprint, nall) :
                print index, readline.get_history_item(index)


    def do_shell(self, line):
        """
        Execute shell commands, ex. shell pwd
        You can also use !<command> like !ls, or !pwd to access the shell

        Uses autocompletion after first command
        """
        os.system(line)


    def complete_shell(self, text, line, start_idx, end_idx):
        if line:
            line = ' '.join(line.split()[1:])
            return _complete_path(line)


    def do_edit(self, line):
        """
        Opens a buffer file to edit a sql statement and then it reads it
        and executes the statement. By default it will show the current
        statement in buffer (or empty)

        Usage:
            - edit   : opens the editor (default from $EDITOR or nano)
            - edit set_editor <editor> : sets editor to <editor>, ex: edit set_editor vi
        """

        line = "".join(line.split())
        if line.find('show') > -1:
            print '\nEditor  = {:}\n'.format(self.editor)
        elif line.find('set_editor') > -1:
            val = line.split('set_editor')[-1]
            if val != '':
                self.editor = val
                self.config.set('easyaccess','editor',val)
                self.writeconfig=True
        else:
            os.system(self.editor + ' easy.buf')
            if os.path.exists('easy.buf'):
                newquery = read_buf('easy.buf')
                if newquery=="": return
                print
                print newquery
                print
                if (raw_input('submit query? (Y/N): ') in ['Y', 'y', 'yes']):
                    self.default(newquery)


    def complete_edit(self, text, line, start_index, end_index):
        if text:
            return [option for option in options_edit if option.startswith(text)]
        else:
            return options_edit

    def do_loadsql(self, line):
        """
        Loads a sql file with a query and ask whether it should be run
        There is a shortcut using @, ex : @test.sql

        Usage: loadsql <filename>   (use autocompletion)
        """
        newq = read_buf(line)
        if newq=="": return
        if self.interactive:
            print
            print newq
            print
            if (raw_input('submit query? (Y/N): ') in ['Y', 'y', 'yes']): self.default(newq)
        else: self.default(newq)


    def complete_loadsql(self, text, line, start_idx, end_idx):
        return _complete_path(line)

    def do_exit(self, line):
        """
        Exits the program
        """
        try:
            os.system('rm -f easy.buf')
        except:
            pass
        try:
            cur.close()
        except:
            pass
        self.con.commit()
        self.con.close()
        if readline_present :
            readline.write_history_file (history_file)
        if self.writeconfig:
            config_mod.write_config(config_file,self.config)
        sys.exit(0)

    def do_clear(self, line):
        """
        Clear screen
        """
        # TODO: platform dependent
        #tmp = sp.call('clear', shell=True)
        tmp=os.system(['clear','cls'][os.name == 'nt'])


    #DO METHODS FOR DB

    def do_set_password(self, arg):
        """
        DB:Set a new password on this and all other DES instances (DESSCI, DESOPER)

        Usage: set_password
        """
        print
        pw1 = getpass.getpass(prompt='Enter new password:')
        if re.search('\W', pw1):
            print colored("\nPassword contains whitespace, not set\n", "red")
            return
        if not pw1:
            print colored("\nPassword cannot be blank\n", "red")
            return
        pw2 = getpass.getpass(prompt='Re-Enter new password:')
        print
        if pw1 != pw2:
            print colored("Passwords don't match, not set\n", "red")
            return

        query = """alter user %s identified by "%s"  """ % (self.user, pw1)
        confirm = 'Password changed in %s' % self.dbname.upper()
        self.query_and_print(query, print_time=False, suc_arg=confirm)

        dbases = ['DESSCI', 'DESOPER']
        for db in dbases:
            if db == self.dbname.upper(): continue
            kwargs = {'host': self.dbhost, 'port': self.port, 'service_name': db}
            dsn = cx_Oracle.makedsn(**kwargs)
            temp_con = cx_Oracle.connect(self.user, self.password, dsn=dsn)
            temp_cur = temp_con.cursor()
            try:
                temp_cur.execute(query)
                confirm = 'Password changed in %s\n' % db.upper()
                print colored(confirm, "green")
                temp_con.commit()
                temp_cur.close()
                temp_con.close()
                self.desconfig.set('db-dessci','passwd',pw1)
                self.desconfig.set('db-desoper','passwd',pw1)
                config_mod.write_desconfig(desfile, self.desconfig)
            except:
                confirm = 'Password could not changed in %s\n' % db.upper()
                print colored(confirm, "red")
                print sys.exc_info()


    def do_refresh_metadata_cache(self, arg):
        """DB:Refreshes meta data cache for auto-completion of table names and column names """

        # Meta data access: With the two linked databases, accessing the
        # "truth" via fgetmetadata has become maddenly slow.
        # what it returns is a function of each users's permissions, and their
        # "mydb". so yet another level of caching is needed. Ta loads a table
        # called fgottenmetadata in the user's mydb. It refreshes on command
        # or on timeout (checked at startup).

        #get last update
        verb = True
        if arg == 'quiet': verb = False
        query_time = "select created from dba_objects where object_name = \'FGOTTENMETADATA\' and owner =\'%s\'  " % (
            self.user.upper())
        try:
            qt = self.cur.execute(query_time)
            last = qt.fetchall()
            now = datetime.datetime.now()
            diff = abs(now - last[0][0]).seconds / 3600.
            if verb: print 'Updated %.2f hours ago' % diff
        except:
            pass
        try:
            query = "DROP TABLE FGOTTENMETADATA"
            self.cur.execute(query)
        except:
            pass
        try:
            if verb:print '\nRe-creating metadata table ...'
            query_2 = """create table fgottenmetadata  as  select * from table (fgetmetadata)"""
            message = 'FGOTTENMETADATA table Created!'
            if not verb :  message=""
            self.query_and_print(query_2, print_time=False, suc_arg= message)
            if verb: print 'Loading metadata into cache...'
            self.cache_table_names = self.get_tables_names()
            self.cache_usernames = self.get_userlist()
            self.cache_column_names = self.get_columnlist()
        except:
            if verb: print colored("There was an error when refreshing the cache", "red")


    def do_show_db(self, arg):
        """
        DB:Shows database connection information
        """
        print
        print "user: %s, host:%s, db:%s" % (self.user, self.dbhost, self.dbname)
        print "Personal links:"
        query = """
           select owner, db_link, username, host, created from all_db_links where OWNER = '%s'
        """ % (self.user.upper())
        self.query_and_print(query, print_time=False)

    def do_whoami(self, arg):
        """
        DB:Print information about the user's details.

        Usage: whoami
        """
        sql_getUserDetails = "select * from des_users where username = '" + self.user + "'"
        self.query_and_print(sql_getUserDetails, print_time=False)

    def do_myquota(self, arg):
        """
        DB:Print information about quota status.

        Usage: myquota
        """
        sql_getquota = "select TABLESPACE_NAME,  \
        MBYTES_USED/1024 as GBYTES_USED, MBYTES_LEFT/1024 as GBYTES_LEFT from myquota"
        self.query_and_print(sql_getquota, print_time=False)

    def do_mytables(self, arg):
        """
        DB:Lists  table you have made in your 'mydb'

        Usage: mytables
        """
        query = "SELECT table_name FROM user_tables"
        self.query_and_print(query, print_time=False)

    def do_find_user(self, line):
        """
        DB:Finds users given 1 criteria (either first name or last name)

        Usage: 
            - find_user Doe     # Finds all users with Doe as their names
            - find_user John%   # Finds all users with John IN their names (John, Johnson, etc...)
            - find_user P%      # Finds all users with first or lastname starting with P

        """
        if line == "": return
        line = " ".join(line.split())
        keys = line.split()
        query = 'select * from des_users where '
        if len(keys) >= 1:
            query += 'upper(firstname) like upper(\'' + keys[0] + '\') or upper(lastname) like upper(\'' + keys[
                0] + '\')'
        self.query_and_print(query, print_time=True)

    def complete_find_user(self, text, line, start_index, end_index):
        options_users = self.cache_usernames
        if text:
            return [option for option in options_users if option.startswith(text.lower())]
        else:
            return options_users


    def do_user_tables(self, arg):
        """
        DB:List tables from given user

        Usage: user_tables <username>
        """
        return self.get_tables_names_user(arg)

    def complete_user_tables(self, text, line, start_index, end_index):
        options_users = self.cache_usernames
        if text:
            return [option for option in options_users if option.startswith(text.lower())]
        else:
            return options_users

    def do_describe_table(self, arg):
        """
        DB:This tool is useful in noting the lack of documentation for the
        columns. If you don't know the full table name you can use tab
        completion on the table name. Tables of ususal interest to
        scientists are described

        Usage: describe_table <table_name>
        Describes the columns in <table-name> as
          column_name, oracle_Type, date_length, comments


        """
        tablename = arg.upper()
        schema = self.user.upper()  #default --- Mine
        link = ""  #default no link
        if "." in tablename: (schema, tablename) = tablename.split(".")
        if "@" in tablename: (tablename, link) = tablename.split("@")
        table = tablename

        #
        # loop until we find a fundamental definition OR determine there is
        # no reachable fundamental definition, floow links and resolving
        # schema names. Rely on how the DES database is constructed we log
        # into our own schema, and rely on synonyms for a "simple" view of
        # common schema.
        #
        while (1):
            #check for fundamental definition  e.g. schema.table@link
            q = """
            select * from all_tab_columns%s
               where OWNER = '%s' and
               TABLE_NAME = '%s'
               """ % ("@" + link if link else "", schema, table)
            if len(self.query_results(q)) != 0:
                #found real definition go get meta-data
                break

            # check if we are indirect by  synonym of mine
            q = """select TABLE_OWNER, TABLE_NAME, DB_LINK from USER_SYNONYMS%s
                            where SYNONYM_NAME= '%s'
            """ % ("@" + link if link else "", table)
            ans = self.query_results(q)
            if len(ans) == 1:
                #resolved one step closer to fundamental definition
                (schema, table, link) = ans[0]
                continue

            #check if we are indirect by a public synonym
            q = """select TABLE_OWNER, TABLE_NAME, DB_LINK from ALL_SYNONYMS%s
                             where SYNONYM_NAME = '%s' AND OWNER = 'PUBLIC'
            """ % ("@" + link if link else "", table)
            ans = self.query_results(q)
            if len(ans) == 1:
                #resolved one step closer to fundamental definition
                (schema, table, link) = ans[0]
                continue

            #failed to find the reference count on the query below to give a null result
            break  # no such table accessible by user

        # schema, table and link are now valid.
        link = "@" + link if link else ""
        q = """
        select
          atc.column_name, atc.data_type,
          atc.data_length || ',' || atc.data_precision || ',' || atc.data_scale DATA_FORMAT, acc.comments
          From all_tab_cols%s atc , all_col_comments%s acc
           where atc.owner = '%s' and atc.table_name = '%s' and
           acc.owner = '%s' and acc.table_name='%s' and acc.column_name = atc.column_name
           order by atc.column_id
           """ % (link, link, schema, table, schema, table)
        self.query_and_print(q, print_time=False, err_arg='Table does not exist or it is not accessible by user')
        return

    def complete_describe_table(self, text, line, start_index, end_index):
        return self._complete_tables(text)

    def do_find_tables(self, arg):
        """
        DB:Lists tables and views matching an oracle pattern  e.g %SVA%,
        
        Usage : find_tables PATTERN
        """
        query = "SELECT distinct table_name from fgottenmetadata  WHERE upper(table_name) LIKE '%s' " % (arg.upper())
        self.query_and_print(query)

    def complete_find_tables(self, text, line, start_index, end_index):
        return self._complete_tables(text)


    def do_find_tables_with_column(self, arg):
        """                                                                                
        DB:Finds tables having a column name matching column-name-string
        
        Usage: find_tables_with_column  <column-name-substring>                                                                 
        Example: find_tables_with_column %MAG%  # hunt for columns with MAG 
        """
        #query  = "SELECT TABLE_NAME, COLUMN_NAME FROM fgottenmetadata WHERE COLUMN_NAME LIKE '%%%s%%' " % (arg.upper())
        query = """
           SELECT 
               table_name, column_name 
           FROM 
                fgottenmetadata 
           WHERE 
             column_name LIKE '%s'  
           UNION
           SELECT LOWER(owner) || '.' || table_name, column_name 
            FROM 
                all_tab_cols
            WHERE 
                 column_name LIKE '%s'
             AND
                 owner NOT LIKE '%%SYS'
             AND 
                 owner not in ('XDB','SYSTEM')
           """ % (arg.upper(), arg.upper())

        self.query_and_print(query)
        return

    def complete_find_tables_with_column(self, text, line, begidx, lastidx):
        return self._complete_colnames(text)

    def do_show_index(self, arg):
        """
        DB:Describes the indices  in <table-name> as
          column_name, oracle_Type, date_length, comments

         Usage: describe_index <table_name>
        """

        # Parse tablename for simple name or owner.tablename.
        # If owner present, then add owner where clause.
        arg = arg.upper().strip()
        if not arg:
            print "table name required"
            return
        tablename = arg
        query_template = """select
             a.table_name, a.column_name, b.index_type, b.index_name, b.ityp_name from
             all_ind_columns a, all_indexes b
             where
             a.table_name LIKE '%s' and a.table_name like b.table_name
             """
        query = query_template % (tablename)
        nresults = self.query_and_print(query)
        return

    def complete_show_index(self, text, line, begidx, lastidx):
        return self._complete_tables(text)


    def do_load_table(self, line):
        """
        DB:Loads a table from a file (csv or fits) taking name from filename and columns from header

        Usage: load_table <filename>
        Ex: example.csv has the following content
             RA,DEC,MAG
             1.23,0.13,23
             0.13,0.01,22

        This command will create a table named EXAMPLE with 3 columns RA,DEC and MAG and values taken from file

        Note: - For csv or tab files, first line must have the column names (without # or any other comment) and same format
        as data (using ',' or space)
              - For fits file header must have columns names and data types
              - For filenames use <table_name>.csv or <table_name>.fits do not use extra points
        """
        if line == "":
            print '\nMust include table filename!\n'
            return
        if line.find('.') == -1:
            print colored('\nError in filename\n', "red")
            return
        else:
            line = "".join(line.split())
            if line.find('/') > -1:
                filename = line.split('/')[-1]
            else:
                filename = line
            alls = filename.split('.')
            if len(alls) > 2:
                print '\nDo not use extra . in filename\n'
                return
            else:
                table = alls[0]
                format = alls[1]
                if format == 'csv':
                    try:
                        DF = pd.read_csv(line, sep=',')
                    except:
                        print colored('\nProblems reading %s\n' % line, "red")
                        return

                    #check table first
                    self.cur.execute(
                        'select count(table_name) from user_tables where table_name = \'%s\'' % table.upper())
                    if self.cur.fetchall()[0][0] == 1:
                        print '\n Table already exists! Change name of file or drop table ' \
                              '\n with:  DROP TABLE %s\n ' % table.upper()
                    qtable = 'create table %s ( ' % table
                    for col in DF:
                        if DF[col].dtype.name == 'object':
                            qtable += col + ' ' + 'VARCHAR2(' + str(max(DF[col].str.len())) + '),'
                        elif DF[col].dtype.name.find('int') > -1:
                            qtable += col + ' INT,'
                        elif DF[col].dtype.name.find('float') > -1:
                            qtable += col + ' BINARY_DOUBLE,'
                        else:
                            qtable += col + ' NUMBER,'
                    qtable = qtable[:-1] + ')'
                    try:
                        self.cur.execute(qtable)
                        self.con.commit()
                    except:
                        (type, value, traceback) = sys.exc_info()
                        print
                        print colored(type, "red")
                        print colored(value, "red")
                        print
                        del DF
                        return

                    cols = ','.join(DF.columns.values.tolist())
                    vals = ',:'.join(DF.columns.values.tolist())
                    vals = ':' + vals
                    qinsert = 'insert into %s (%s) values (%s)' % (table.upper(), cols, vals)
                    try:
                        t1 = time.time()
                        self.cur.executemany(qinsert, DF.values.tolist())
                        t2 = time.time()
                        self.con.commit()
                        print colored(
                            '\n  Table %s created successfully with %d rows and %d columns in %.2f seconds' % (
                                table.upper(), len(DF), len(DF.columns), t2 - t1), "green")
                        del DF
                    except:
                        (type, value, traceback) = sys.exc_info()
                        print
                        print colored(type, "red")
                        print colored(value, "red")
                        print
                        return
                    return
                else:
                    print '\n Format not recognized, use csv or fits as extensions\n'
                    return


    def complete_load_table(self, text, line, start_idx, end_idx):
        return _complete_path(line)


    #UNDOCCUMENTED DO METHODS

    def do_EOF(self, line):
        # exit program on ^D
        self.do_exit(line)

    def do_quit(self, line):
        self.do_exit(line)


    def do_clean_history(self,line):
        if readline_present: readline.clear_history()



##################################################
def to_pandas(cur):
    """
    Returns a pandas DataFrame from a executed query 
    """
    if cur.description != None:
        data=pd.DataFrame(cur.fetchall(), columns=[rec[0] for rec in cur.description])
    else: data=""
    return data
class connectDB():
    def  __init__(self):
        conf=config_mod.get_config(config_file)
        pd.set_option('display.max_rows', conf.getint('display','max_rows'))
        pd.set_option('display.width', conf.getint('display','width'))
        pd.set_option('display.max_columns', conf.getint('display','max_columns'))
        desconf=config_mod.get_desconfig(desfile)
        db=conf.get('easyaccess','database')
        self.prefetch = conf.getint('easyaccess','prefetch')
        self.dbname = db
        #connect to db  
        self.user = desconf.get('db-'+self.dbname,'user')
        self.dbhost = desconf.get('db-'+self.dbname,'server')
        self.port = desconf.get('db-'+self.dbname,'port')
        self.password = desconf.get('db-'+self.dbname,'passwd')
        kwargs = {'host': self.dbhost, 'port': self.port, 'service_name': self.dbname}
        dsn = cx_Oracle.makedsn(**kwargs)
        print 'Connecting to DB...'
        self.con = cx_Oracle.connect(self.user, self.password, dsn=dsn)
    def ping(self):
        try:
            self.con.ping()
            print 'Still connected to DB'
        except:
            print 'Connection with DB lost'
    def cursor(self):
        cursor = self.con.cursor()
        cursor.arraysize = self.prefetch
        return cursor
    def close(self):
        self.con.close()

##################################################

class MyParser(argparse.ArgumentParser):
    def error(self, message):
        print '\n*****************'
        sys.stderr.write('error: %s \n' % message)
        print '*****************\n'
        self.print_help()
        sys.exit(2)



if __name__ == '__main__':

    conf=config_mod.get_config(config_file)
    #PANDAS DISPLAY SET UP
    pd.set_option('display.max_rows', conf.getint('display','max_rows'))
    pd.set_option('display.width', conf.getint('display','width'))
    pd.set_option('display.max_columns', conf.getint('display','max_columns'))

    try:
        import readline
        #save = sys.stdout
        #sys.stdout = open("/dev/null","w")
        readline.read_history_file(history_file)
        #sys.stdout = save
        readline_present = True
        readline.set_history_length(conf.getint('easyaccess','histcache'))
    except:
        print sys.exc_info()
        readline_present = False

    parser = MyParser(description='Easy Access', version="version: %s" % __version__ )
    parser.add_argument("-c", "--command",  dest='command', help="Execute command and exit")
    parser.add_argument("-l", "--loadsql", dest='loadsql',help="Load a sql command, execute it and exit")
    parser.add_argument("-lt", "--loadtable", dest='loadtable',help="Load a sql command, execute it and exit")
    parser.add_argument("-s", "--db", dest='db',help="bypass database name, [dessci, desoper or destest]")
    args = parser.parse_args()

    desconf=config_mod.get_desconfig(desfile)

    if args.db is not None:
        db=args.db
    else:
        db=conf.get('easyaccess','database')

    if args.command is not None:
        cmdinterp = easy_or(conf,desconf,db,interactive=False)
        cmdinterp.onecmd(args.command)
        sys.exit(0)
    elif args.loadsql is not None:
        cmdinterp = easy_or(conf,desconf, db,interactive=False)
        linein="loadsql "+  args.loadsql
        cmdinterp.onecmd(linein)
        sys.exit(0)
    elif args.loadtable is not None:
        cmdinterp = easy_or(conf,desconf, db,interactive=False)
        linein="load_table "+  args.loadtable
        cmdinterp.onecmd(linein)
        sys.exit(0)
    else:
        os.system(['clear','cls'][os.name == 'nt'])
        easy_or(conf,desconf,db).cmdloop()


