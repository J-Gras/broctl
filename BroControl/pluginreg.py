#
# Registry managing plugins.

import sys
import os
import logging

from BroControl import config
from BroControl import node
from BroControl import plugin

# Note, when changing this, also adapt doc string for Plugin.__init__.
_CurrentAPIVersion = 2
_SupportedAPIVersions = [1, _CurrentAPIVersion]

class PluginRegistry:
    def __init__(self):
        self._plugins = []
        self._dirs = []
        self._cmds = {}

    def _activeplugins(self):
        return filter(lambda p: p.activated, self._plugins)

    def addDir(self, dir):
        """Adds a directory to search for plugins."""
        if dir not in self._dirs:
            self._dirs += [dir]

    def loadPlugins(self, cmdout, executor):
        """Loads all plugins found in any of the added directories."""
        self._loadPlugins(cmdout)

        for p in self._plugins:
            p.executor = executor

    def initPluginOptions(self):
        """Initialize options for all loaded plugins."""
        for p in self._plugins:
            p._registerOptions()

    def initPlugins(self, cmdout):
        """Initialize all loaded plugins."""
        for p in self._plugins:
            p.activated = False

            try:
                init = p.init()
            except Exception as err:
                cmdout.warn("Plugin '%s' not activated because its init() method raised exception: %s" % (p.name(), err))
                continue

            if not init:
                logging.debug("Plugin '%s' not activated because its init() returned False", p.name())
                continue

            p.activated = True

    def initPluginCmds(self):
        """Initialize commands provided by all activated plugins."""
        self._cmds = {}
        for p in self._activeplugins():
            for (cmd, args, descr) in p.commands():
                self._cmds["%s.%s" % (p.prefix(), cmd)] = (p, args, descr)

    def finishPlugins(self):
        """Shuts all plugins down."""
        for p in self._activeplugins():
            p.done()

    def hostStatusChanged(self, host, status):
        """Calls all plugins Plugin.hostStatusChanged_ methods; see there for
        parameter semantics."""
        for p in self._activeplugins():
            p.hostStatusChanged(host, status)

    def broProcessDied(self, node):
        """Calls all plugins Plugin.broProcessDied_ methods; see there for
        parameter semantics."""
        for p in self._activeplugins():
            p.broProcessDied(node)

    def cmdPreWithNodes(self, cmd, nodes, *args):
        """Executes the ``cmd_<XXX>_pre`` function for a command taking a list
        of nodes as its first argument. All other arguments are passed on.
        Returns the filtered node list as returned by the chain of all
        plugins."""

        method = "cmd_%s_pre" % cmd

        for p in self._activeplugins():
            func = getattr(p, method)
            new_nodes = func(nodes, *args)
            if new_nodes != None:
                nodes = new_nodes

        return nodes

    def cmdPre(self, cmd, *args):
        """Executes the ``cmd_<XXX>_pre`` function for a command *not* taking
        a list of nodes as its first argument. All arguments are passed on.
        Returns True if no plugins returned False.
        """
        method = "cmd_%s_pre" % cmd
        result = True

        for p in self._activeplugins():
            func = getattr(p, method)
            if func(*args) == False:
                result = False

        return result

    def cmdHook(self, cmd, hook):
        """Executes the ``cmd_<XXX>_hook`` function for a command taking a string
        identifying the hook as its first argument.
        """
        method = "cmd_%s_hook" % cmd

        for p in self._activeplugins():
            func = getattr(p, method)
            func(hook)

    def cmdPostWithNodes(self, cmd, nodes, *args):
        """Executes the ``cmd_<XXX>_post`` function for a command taking a list
        of nodes as its first argument. All other arguments are passed on.
        """
        method = "cmd_%s_post" % cmd

        for p in self._activeplugins():
            func = getattr(p, method)
            func(nodes, *args)

    def cmdPostWithResults(self, cmd, results, *args):
        """Executes the ``cmd_<XXX>_post`` function for a command taking a
        list of tuples ``(node, bool)`` as its first argument. All other
        arguments are passed on.
        """
        method = "cmd_%s_post" % cmd

        for p in self._activeplugins():
            func = getattr(p, method)
            func(results, *args)

    def cmdPost(self, cmd, *args):
        """Executes the ``cmd_<XXX>_post`` function for a command *not* taking
        a list of nodes as its first argument. All arguments are passed on.
        """
        method = "cmd_%s_post" % cmd

        for p in self._activeplugins():
            func = getattr(p, method)
            func(*args)

    def runCustomCommand(self, cmd, args, cmdout):
        """Runs a custom command *cmd* with string *args* as argument. Returns
        False if no such command is known."""
        try:
            (myplugin, usage, descr) = self._cmds[cmd]
        except LookupError:
            return False

        prefix = myplugin.prefix()

        if cmd.startswith("%s." % prefix):
            cmd = cmd[len(prefix)+1:]

        myplugin.cmd_custom(cmd, args, cmdout)
        return True

    def allCustomCommands(self):
        """Returns a list of string tuples *(cmd, descr)* listing all commands
        defined by any plugin."""

        cmds = []

        for cmd in sorted(self._cmds.keys()):
            (myplugin, args, descr) = self._cmds[cmd]
            cmds += [(cmd, args, descr)]

        return cmds

    def addNodeKeys(self):
        """Adds all plugins' node keys to the list of supported keys in
        ``Node``."""

        for p in self._plugins:
            for key in p.nodeKeys():
                key = "%s_%s" % (p.prefix(), key)
                logging.debug("adding node key %s for plugin %s", key, p.name())
                node.Node.addKey(key)

    def _loadPlugins(self, cmdout):
        for path in self._dirs:
            for root, dirs, files in os.walk(os.path.abspath(path)):
                for name in files:
                    if name.endswith(".py") and not name.startswith("__"):
                        self._importPlugin(os.path.join(root, name[:-3]), cmdout)

    def _importPlugin(self, path, cmdout):
        sys.path = [os.path.dirname(path)] + sys.path

        try:
            module = __import__(os.path.basename(path))
        except Exception as e:
            cmdout.warn("cannot import plugin %s: %s" % (path, e))
            return
        finally:
            sys.path = sys.path[1:]

        found = False

        for cls in module.__dict__.values():
            try:
                is_plugin = issubclass(cls, plugin.Plugin)
            except TypeError:
                # cls is not a class.
                continue

            if is_plugin:
                found = True

                try:
                    p = cls()
                except Exception as e:
                    cmdout.warn("plugin class %s __init__ failed: %s" % (cls.__name__, e))
                    break

                # verify that the plugin overrides all required methods
                try:
                    logging.debug("Found plugin %s from %s (version %d, prefix %s)",
                               p.name(), module.__file__, p.pluginVersion(), p.prefix())
                except NotImplementedError:
                    cmdout.warn("failed to load plugin at %s because it doesn't override required methods" % path)
                    continue

                if p.apiVersion() not in _SupportedAPIVersions:
                    cmdout.warn("failed to load plugin %s due to incompatible API version (uses %d, but current is %s)"
                                  % (p.name(), p.apiVersion(), _CurrentAPIVersion))
                    continue

                if not p.prefix():
                    cmdout.warn("failed to load plugin %s because prefix is empty" % p.name())

                if "." in p.prefix() or " " in p.prefix():
                    cmdout.warn("failed to load plugin %s because prefix contains dots or spaces" % p.name())

                pluginprefix = p.prefix().lower()
                sameprefix = False
                for i in self._plugins:
                    if pluginprefix == i.prefix().lower():
                        sameprefix = True
                        cmdout.warn("failed to load plugin %s due to another plugin having the same plugin prefix" % p.name())
                        break

                if not sameprefix:
                    self._plugins += [p]

        if not found:
            cmdout.warn("no plugin found in %s" % module.__file__)

