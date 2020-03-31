"""
packz.py
----------

Run a Python script with a trace that records every file accessed,
and then bundle those requirements into a single package such as
needed for cloud functions.
"""
import os
import sys

import shutil

import collections
import subprocess

from fnmatch import fnmatch
from pkgutil import iter_modules, find_loader


def expand(path):
    """
    Expand paths to remove symlinks and user symbols.

    Parameters
    -----------
    path : str
      Location of a file

    Returns
    ------------
    expanded : str
      Absolute file location with all shorthand expanded
    """
    return os.path.realpath(os.path.expanduser(path))


def get_installed():
    """
    Get the name and location on disk of every importable module.

    Returns
    ------------
    paths : dict
      Module name are keys, file path are values
    """
    paths = {}
    # which python modules are installed
    for m in iter_modules():
        name = m.name
        try:
            # don't use an import as it is slow and often
            # crashes on random pypi stuff
            file_name = expand(find_loader(name).get_filename())
            if file_name.endswith('__init__.py'):
                # if it's an init we want the directory
                paths[name] = os.path.split(file_name)[0]
            elif file_name.endswith('.py'):
                # otherwise we just want the single file
                paths[name] = file_name
        except BaseException:
            continue

    return paths


def stdlib(installed):
    """
    Given a dict with installed modules, find which of them
    are in the Python standard library

    Parameters
    ------------
    installed : dict
      Module name to path on disk

    Returns
    ------------
    std : set
      Names of modules in the standard library
    """
    # the root directory for collections.py
    std_root = os.path.split(installed['collections'])[0]
    # the subdir for site packages
    std_site = os.path.join(std_root, 'site-packages')
    std = set([m for m, p in installed.items()
               if p.startswith(std_root) and
               not p.startswith(std_site)])
    return std


class PackRunner(object):
    """
    Record data from running a Python script
    """

    def __init__(self, mod_blacklist=None, file_blacklist=None):
        """
        Initialize a runner.

        Parameters
        -------------
        mod_blacklist : None or (n,) str
          Names of modules to skip in packaging operations
        file_blacklist : None or (n,) str
          Filename patterns to skip: i.e. "*assimp*"
        """
        # save our blacklist
        self.mod_blacklist = mod_blacklist
        self.file_blacklist = file_blacklist

        # which modules are installed and importable
        self.installed = get_installed()
        # which of those modules are in the standard library
        self.stdlib = stdlib(self.installed)

    def start(self):
        """
        Start recording every file accessed.
        """
        self._lsof_start = self.lsof()
        self._files = []
        self._trace = lambda f, c, e: self._files.append(f.f_code.co_filename)
        sys.settrace(self._trace)

    def stop(self):
        """
        Stop recording and save data.
        """
        # stop recording
        sys.settrace(None)
        # record everything open by us (includes .so compiled modules)
        self._lsof_stop = self.lsof()

    def lsof(self):
        """
        Get a list of every file being accessed by Python.

        Returns
        ----------
        files : (n,) str
          List of every file being accessed by this process
        """
        # what is our process ID
        pid = str(os.getpid())
        # magical "which files are open by us" command
        cmd = f'lsof -F n -p {pid} | grep ^n/ | cut -c2- '
        # split into a clean list of filenames
        opened = set(
            subprocess.check_output(
                cmd, shell=True).decode('utf-8').split())
        return opened

    def which_module(self, file_name):
        """
        Which Python module does a file belong to?

        Parameters
        -------------
        file_name : str
          The name of a file

        Returns
        ------------
        mod : str or None
          The name of a module
        path : str or None
          The path for the root of the module
        """
        # use lame-search
        starts = {p: m for m, p in self.installed.items()
                  if file_name.startswith(p)}
        if len(starts) > 0:
            # take the longest string
            start = max(starts.keys())
            # get the module name
            mod = starts[start]
            return mod, start
        return None, None

    def path_map(self, file_name, no_mod='lib'):
        """
        Figure out where to copy a file to in our new tree.

        Parameters
        -----------
        file_name : str
          Source file
        no_mod : str
          Directory name for files not part of a module

        Returns
        ------------
        copy_to :
        1) which module was this file included in
        2) where should this be copied to in our pack-and-go archive
        """

        # check the file blacklist to see if we can exit early
        if self.file_blacklist is not None:
            tail = os.path.split(file_name)[1]
            if any(fnmatch(tail, p) for p in self.file_blacklist):
                return None
        # get the module name and root path for this file
        mod, start = self.which_module(file_name)

        if mod is not None:
            # if module is in standard library exit
            if mod in self.stdlib:
                return None
            # if module is in blacklist exit
            if self.mod_blacklist is not None and mod in self.mod_blacklist:
                return None
            # get the leading part of the path
            head = os.path.split(start)[0]
            # clip off the leading part
            copy_to = file_name[len(head) + 1:]
            # add the file to a running size total
            self._totals[mod] += os.path.getsize(file_name)
        else:
            # otherwise copy the file to a no module directory
            copy_to = os.path.join(no_mod, os.path.split(file_name)[1])
        return copy_to

    def copy_list(self):
        """
        Get a list of source files and destinations.

        Returns
        ----------
        copies : (n,) list
          Contains pairs of (source, destination) file locations
        """
        # initialize a size total list
        self._totals = collections.defaultdict(int)
        # get all the python files that were run
        used = [expand(f) for f in set(self._files) if os.path.isfile(f)]
        # get all the other files that were imported during the run
        opened = [expand(f) for f in
                  self._lsof_stop.difference(self._lsof_start)
                  if os.path.isfile(f)]
        # get the mapped destination for the file
        copies = []
        copies.extend((f, self.path_map(f)) for f in opened)
        copies.extend((f, self.path_map(f)) for f in used)
        copies = [c for c in copies if c[1] is not None]

        return copies

    def copy(self, build_path):
        """
        Actually copy the files into
        """
        copies = self.copy_list()
        size = sum(os.path.getsize(f[0]) for f in copies)

        print('total package looks like: {}mb'.format(size / 1e6))

        build = expand(build_path)
        for i, (src, dst) in enumerate(copies):
            print(f'copying {i}/{len(copies)}: {dst}')
            dst_full = os.path.join(build, dst)
            parent = os.path.split(dst_full)[0]
            # if parent directory doesn't exist, create it
            if not os.path.isdir(parent):
                os.makedirs(parent)
            if os.path.isdir(src):
                # if source is a directory copy the tree
                shutil.copytree(src, dst_full)
            else:
                # if source is a file copy it
                shutil.copyfile(src, dst_full)


if __name__ == '__main__':

    runner = PackRunner(
        mod_blacklist=['fcl'],
        file_blacklist=['*assimp*'])

    runner.start()

    import app
    r = app.do()

    runner.stop()

    runner.copy(build_path='~/packz_build')
