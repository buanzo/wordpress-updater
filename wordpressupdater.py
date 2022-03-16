#!/usr/bin/env python3
import os
import sys
import json
import shutil
import requests
import argparse
import subprocess
from pathlib import Path
from apacheconfig import make_loader
from pprint import pprint

__version__ = '0.5.16'


def printerr(x):
    print(x, file=sys.stderr)


def pprinterr(x):
    from pprint import pprint
    pprint(x, stream=sys.stderr)

class DO_WP_Maintain():
    def __init__(self,
                 configpaths=None,
                 explicit_path=False,
                 requiredtags=None,
                 allow_root=False,
                 verbose=False,
                 debug=False,
                 hume=False,
                 skip_plugins=None,
                 skip_themes=None,
                 exec_timeout=None,
                 path_to_wpcli=None):

        # Even higher priority
        self.hume = hume

        # Always priority:
        if allow_root is False and os.geteuid() == 0:
            msg = 'Running as root is not allowed. Check --help.'
            printerr(msg)
            if self.hume:
                self.Hume({'level': 'error',
                           'msg': msg,
                           'task': 'WPUPDATER'})
            sys.exit(1)

        # Internal config
        self.DOMETAURLJSON = 'http://169.254.169.254/metadata/v1.json'
        # Internal setup
        self.allow_root = allow_root
        self.configpaths = configpaths
        self.exec_timeout = exec_timeout
        self.verbose = verbose
        self.debug = debug
        # Other runtime checks:
        if path_to_wpcli is None:  # Path not provided, search in path
            self.path_to_wpcli = shutil.which('wp')
            if self.path_to_wpcli is None:
                msg = '''"wp" command does not seem to be in PATH.
Try --path-to-wpcli to set it manually, or install WP-CLI:
https://wp-cli.org/#installing'''
                printerr(msg)
                sys.exit(1)
        else:  # path provided, set it
            self.path_to_wpcli = path_to_wpcli

        # Now that we have a path_to_wpcli, test it:
        if not self.test_wpcli_works():
            msg = 'No executable for wp-cli or provided one is invalid.'
            printerr(msg)
            sys.exit(1)

        if self.hume:  # Test
            try:
                import hume
            except ModuleNotFoundError:
                printerr('''--hume specified but cannot load hume module.
You might need to install and configure humed. Check
https://github.com/buanzo/hume/wiki''')
                sys.exit(10)

        # Tag matching functionality only works in DigitalOcean Droplets:
        if requiredtags is not None:
            self.requiredtags = set(requiredtags)   # remove dupes
            self.metadata = self.get_do_metadata()  # MAY return None
            if not self.is_droplet():
                msg = "Required tags only work on DigitalOcean"
                if self.hume:
                    self.Hume({'level': 'error',
                               'msg': msg,
                               'task': 'WPUPDATER'})
                raise(RuntimeError(msg))
            if self.valid_droplet_tags() is False:
                msg = "Droplet lacks indicated tag requirements"
                if self.hume:
                    self.Hume({'level': 'error',
                               'msg': msg,
                               'task': 'WPUPDATER'})
                raise(RuntimeError(msg))

        # Skip_Themes check needs to go after populating self.wp_list
        self.skip_themes = []
        if skip_themes is not None:
            for item in skip_themes:
                # Follows same logic as skip_plugins, see below
                if self.valid_skip_theme_spec(item):
                    self.skip_themes.append(item)
                else:
                    msg = '"{}" is not a valid theme name. Skipping.'.format(item)
                    printerr(msg)
                    if self.hume:
                        self.Hume({'level': 'warning',
                                   'msg': msg,
                                   'task': 'WPUPDATER'})

        # Skip_Plugins check needs to go after populating self.wp_list
        self.skip_plugins = []
        if skip_plugins is not None:
            for item in skip_plugins:
                # skip plugins supports both plugin_name and PATH:plugin_name
                # This way we can skip a specific plugin in a specific PATH
                # or skip updating a specific plugin GLOBALLY.
                if self.valid_skip_plugin_spec(item):
                    self.skip_plugins.append(item)
                else:
                    msg = '"{}" is not a valid plugin name. Skipping.'.format(item)
                    printerr(msg)
                    if self.hume:
                        self.Hume({'level': 'warning',
                                   'msg': msg,
                                   'task': 'WPUPDATER'})

        if self.explicit_path is True:
            self.roots_list = [self.configpaths]
        else:
            self.roots_list = self.get_apache2_documentroots()
        if len(self.roots_list) == 0:
            msg = "No Apache2 DocumentRoots found. Check paths."
            if self.hume:
                self.Hume({'level': 'error',
                           'msg': msg,
                           'task': 'WPUPDATER'})
            raise(RuntimeError(msg))

        if self.verbose:
            printerr('DocumentRoots: {}'.format(' '.join(self.roots_list)))
        self.wp_list = self.get_wp_list()


    def is_droplet(self):
        if self.metadata is None:  # self.metadata is loaded on __init__
            return(False)
        if 'droplet_id' in self.metadata.keys():
            return(True)
        return(False)

    def get_wp_list(self):
        # TODO: take into account the security measure of moving
        # TODO: wp-config.php to the parent directory
        # See "Securing wp-config.php" in this article:
        # https://wordpress.org/support/article/hardening-wordpress/
        # There are pro and against voices on that...
        paths = self.roots_list
        wp_list = []
        for path in paths:
            if self.verbose:
                printerr('{}: Searching for wp-config.php files'.format(path))
            for item in Path(path).rglob('wp-config.php'):
                # Lets try to validate the location by getting wp data
                # using wp-cli
                potential = os.path.dirname(item)
                version = self._wp_get_version(path=potential)
                if self.verbose:
                    printerr('{}: Found in {}'.format(path, item))
                if version is None:  # no version? skip.
                    if self.verbose:
                        printerr('{}: no version. skipping.'.format(potential))
                    continue
                else:  # If we can get the version, get more data
                    blogname = self._wp_get_blogname(path=potential)
                    siteurl = self._wp_get_siteurl(path=potential)
                    wp_list.append({'path': potential,
                                    'version': version,
                                    'title': blogname,
                                    'siteurl': siteurl, })
        return(wp_list)

    def _run(self, cmd, timeout):  # cmd must be a []
        # Is one minute enough as a timeout?
        # This function returns a dictionary
        # status = exit status
        # stdout = utf8-decoded stdout
        # stderr = utf8-decoded stderr
        # Does NOT manage stdin
        if not isinstance(cmd, list):
            raise(ValueError("cmd is not a list"))
        result = subprocess.run(cmd,
                                timeout=timeout,  # 300s default
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        retObj = {}
        retObj['status'] = result.returncode
        retObj['stdout'] = result.stdout.decode('utf-8')
        retObj['stderr'] = result.stderr.decode('utf-8')
        return(retObj)

    def test_wpcli_works(self):
        r = False  # Return False by default
        v = ''
        cmd = [self.path_to_wpcli, 'cli', 'version']
        r = self._run(cmd,timeout=self.exec_timeout)
        try:  # Quick check. Get version as valid test.
            o = r['stdout']
            v = o.strip().split('WP-CLI ')[1]
        except Exception:
            pass
        if v.count('.') > 0:
            r = True  # only case r will be True
        return(r)

    def wp_run(self, path, args):
        cmd = [self.path_to_wpcli, '--no-color']
        if self.allow_root is True:  # __init__ checks EUID and --allow-root
            cmd.append('--allow-root')
        cmd.append('--path={}'.format(path))
        cmd.extend(args)
        return(self._run(cmd,timeout=self.exec_timeout))

    def _wp_get_version(self, path):
        args = ['core', 'version', ]
        version = self.wp_run(path=path, args=args)['stdout'].strip()
        if len(version) > 0:
            return(version)
        else:
            return(None)

    def _wp_is_multisite(self, path):
        args = ['core', 'is-installed', '--network']
        multisite = self.wp_run(path=path, args=args)['status']
        if multisite > 0:
            return(False)
        return(True)

    def update_core(self):
        args = ['core', 'update']
        for site in self.wp_list:
            path = site['path']
            if self.verbose:
                printerr('Updating Wordpress Core in {}'.format(path))
            r = self.wp_run(path=path, args=args)
            if r['status'] > 0:
                msg = 'Error updating core {}: {}'.format(path, r['stderr'])
                printerr(msg)
                if self.hume:
                    self.Hume({'level': 'warning',
                               'msg': msg,
                               'task': 'WPUPDATER'})

    def update_db(self):
        args = ['core', 'update-db']
        for site in self.wp_list:
            path = site['path']
            if self.verbose:
                printerr('Updating Wordpress Database in {}'.format(path))
            r = self.wp_run(path=path, args=args)
            if r['status'] > 0:
                msg = 'Error updating database {}: {}'.format(path,
                                                              r['stderr'])
                printerr(msg)
                if self.hume:
                    self.Hume({'level': 'warning',
                               'msg': msg,
                               'task': 'WPUPDATER'})

    def get_plugin_list(self,path):
        wpl = []
        for status in 'active','inactive':
            args = ['plugin', 'list','--status={}'.format(status), '--field=name']
            r = self.wp_run(path=path, args=args)
            _wpl = r['stdout'].split('\n')
            _wpl = [x for x in _wpl if x]
            wpl.extend(_wpl)
        return(wpl)
    
    def get_theme_list(self,path):
        wtl = []
        for status in 'active','inactive':
            args = ['theme', 'list','--status={}'.format(status), '--field=name']
            r = self.wp_run(path=path, args=args)
            _wtl = r['stdout'].split('\n')
            _wtl = [x for x in _wtl if x]
            wtl.extend(_wtl)
        return(wtl)

    def update_plugins(self):
        for site in self.wp_list:
            path = site['path']
            if self.verbose:
                printerr('Getting list of Wordpress Plugins in {}'.format(path))
            wpl = self.get_plugin_list(path=path)
            for pluginName in wpl:
                self.update_plugin(pluginName,path=path)

    def update_themes(self):
        for site in self.wp_list:
            path = site['path']
            if self.verbose:
                printerr('Getting list of Wordpress Themes in {}'.format(path))
            wtl = self.get_theme_list(path=path)
            for themeName in wtl:
                self.update_theme(themeName,path=path)

    def skip_theme_update(self, themeName, path):
        for item in self.skip_themes:
            if item.count(':') == 0:
                if themeName == item:
                    return(True)
            elif item.count(':') == 1:
                # path:themeName
                c = '{}:{}'.format(path, themeName)
                if c == item:
                    return(True)
        return(False)

    def skip_plugin_update(self, pluginName, path):
        for item in self.skip_plugins:
            if item.count(':') == 0:
                # assume it is a pluginName global skip spec
                if pluginName == item:
                    return(True)
            elif item.count(':') == 1:
                # path:pluginName
                c = '{}:{}'.format(path,pluginName)
                if c == item:
                    return(True)
        return(False)

    def update_plugin(self,pluginName,path):
        if self.skip_plugin_update(pluginName,path):
            if self.verbose:
                printerr('Skipping update of plugin "{}" in "{}"'.format(pluginName, path))
            return
        args = ['plugin', 'update', pluginName]
        if self.verbose:
            printerr('Updating Wordpress plugin {} in {}'.format(pluginName, path))
        r = self.wp_run(path=path, args=args)
        if r['status'] > 0:
            msg = 'Error updating plugin {} in {}: {}'.format(pluginName,
                                                              path,
                                                              r['stderr'])
            printerr(msg)
            if self.hume:
                self.Hume({'level': 'warning',
                           'msg': msg,
                           'task': 'WPUPDATER'})

    def update_theme(self, themeName, path):
        if self.skip_theme_update(themeName,path):
            if self.verbose:
                printerr('Skipping update of theme "{}" in "{}"'.format(themeName, path))
            return
        args = ['theme', 'update', themeName]
        if self.verbose:
            printerr('Updating Wordpress theme {} in {}'.format(themeName, path))
        r = self.wp_run(path=path, args=args)
        if r['status'] > 0:
            msg = 'Error updating theme {} in {}'.format(themeName, path, r['stderr'])
            printerr(msg)
            if self.hume:
                self.Hume({'level': 'warning',
                           'msg': msg,
                           'task': 'WPUPDATER'})

    def update_wpcli(self):
        args = ['cli', 'update', '--yes']
        r = self.wp_run(path='/tmp', args=args)
        if r['status'] > 0:
            msg = 'Error updating WP-CLI itself: {}'.format(r['stderr'])
            printerr(msg)
            if self.hume:
                self.Hume({'level': 'warning',
                           'msg': msg,
                           'task': 'WPUPDATER'})

    def optimize_database(self):
        args = ['db', 'optimize']
        for site in self.wp_list:
            path = site['path']
            if self.verbose:
                printerr('Optimizing database in {}'.format(path))
            r = self.wp_run(path=path, args=args)
            if r['status'] > 0:
                msg = 'Error whilst optimizing database {}: {}'.format(path,
                                                                       r['stderr'])
                printerr(msg)
                if self.hume:
                    self.Hume({'level': 'warning',
                               'msg': msg,
                               'task': 'WPUPDATER'})

    def delete_expired_transients(self):
        args = ['transient', 'delete', '--expired']
        for site in self.wp_list:
            path = site['path']
            if self.verbose:
                printerr('Deleting expired transients in {}'.format(path))
            r = self.wp_run(path=path, args=args)
            if r['status'] > 0:
                msg = 'Error deleting transients {}: {}'.format(path,
                                                                r['stderr'])
                printerr(msg)
                if self.hume:
                    self.Hume({'level': 'warning',
                               'msg': msg,
                               'task': 'WPUPDATER'})

    def _wp_get_blogname(self, path):
        args = ['option', 'get', 'blogname', ]
        blogname = self.wp_run(path=path, args=args)['stdout'].strip()
        return(blogname)

    def _wp_get_siteurl(self, path):
        args = ['option', 'get', 'siteurl', ]
        siteurl = self.wp_run(path=path, args=args)['stdout'].strip()
        return(siteurl)

    def run_custom_cmds(self, cmds):
        for cmd in cmds:
            args = cmd.split(' ')
            for site in self.wp_list:
                path = site['path']
                if self.verbose:
                    printerr('Running {} in {}'.format(cmd, path))
                r = self.wp_run(path=path, args=args)
                if r['status'] > 0:
                    msg = 'Error running "{}" in {}: {}'.format(cmd, path,
                                                                r['stderr'])
                    printerr(msg)
                    if self.hume:
                        self.Hume({'level': 'warning',
                                   'msg': msg,
                                   'task': 'WPUPDATER'})
                else:
                    if self.verbose:
                        printerr(r['stdout'])

    def get_do_metadata(self):
        try:
            j = requests.get(self.DOMETAURLJSON).json()
        except Exception as exc:
            printerr('Issue loding DO Metadata v1 JSON: {}'.format(exc))
            return(None)
        return(j)

    def valid_droplet_tags(self):
        if self.requiredtags.issubset(self.metadata["tags"]):
            return(True)
        return(False)

    def valid_skip_plugin_spec(self,item):
        # TODO: regex for wordpress plugin names
        if item.count(':') == 0:
            # FIX: some useful regex for plugin names
            # In any case we will compare against dynamic plugin list
            return(True)
        elif item.count(':') == 1:
            # FIX: validate path and plugin name
            # But... as above: we will compare against dynamic list
            return(True)
        else:
            return(False)
        return(False)

    def valid_skip_theme_spec(self,item):
        # Yes, this function is exactly like valid_skip_plugin_spec
        # I might deduplicate the code, but I need to analyze
        # And yes, this might be true for all of skip_theme/skip_plugin
        # functionality.
        # TODO: regex for wordpress plugin names
        if item.count(':') == 0:  # yes yes i know i can optimize this
            return(True)
        elif item.count(':') == 1:
            return(True)
        else:
            return(False)
        return(False)

    def _extract_documentroots(self,config):
        paths = []
        flatsplit = json.dumps(config).split(',')
        for item in flatsplit:
            item = item.strip()
            if item.count('documentroot') == 1:
                item = item.split(': ')[1].replace('"','').replace("'",'')
                paths.append(item)
                continue
        return(paths)

    def get_apache2_documentroots(self):
        documentroots = []
        # apacheconfig options
        options = {
            'includerelative': True,
            'lowercasenames': True
        }

        for configpath in self.configpaths:
            options['configroot'] = os.path.dirname(configpath)
            try:
                if self.verbose:
                    printerr('Processing {}...'.format(configpath))
                with make_loader(**options) as loader:
                    config = loader.load(configpath)
            except Exception as exc:
                msg = 'Issue loading Apache config {}: {}'.format(configpath,
                                                                  exc)
                printerr(msg)
                if self.hume:
                    self.Hume({'level': 'critical',
                               'msg': msg,
                               'task': 'WPUPDATER'})
            documentroots.extend(self._extract_documentroots(config))
        documentroots = list(set(documentroots))
        return(documentroots)

    def Hume(self, msg):
        try:
            import hume
        except ImportError:
            printerr('''wordpressupdater: hume is not available.
Check out https://www.github.com/buanzo/hume/wiki''')
        except Exception as exc:
            printerr('Error loading hume module:')
            printerr(exc)
            printerr('Continuing...')
            return(None)
        hume.Hume(msg).send()

def run():
    # TODO: ArgParse for droplet required tags
    parser = argparse.ArgumentParser(description='''Tool that implements wp-cli
maintenance tasks on servers that use Apache2, with a few useful features on
DigitalOcean droplets.
Future versions may support nginx/lighttpd.
Author: Buanzo - https://www.github.com/buanzo''')
    parser.add_argument('-t', '--tags',
                        type=lambda arg: arg.split(','),
                        action='append',
                        default=None,
                        dest='requiredtags',
                        help='''Comma-separated list of required droplet tags.
All tags must be assigned to droplet for maintenance to happen. May
be used multiple times.''')
    parser.add_argument('file',
                        nargs='+',
                        help='''Path to configuration files to extract
DocumentRoots from.''')
    parser.add_argument('--allow-root',
                        default=False,
                        action='store_true',
                        dest='allow_root',
                        help='Enables usage of this script as root. AVOID.')
    parser.add_argument('--list-only',
                        default=False,
                        action='store_true',
                        dest='list_only',
                        help='List Wordpress installations that were found.')
    parser.add_argument('-C', '--update-core',
                        default=False,
                        action='store_true',
                        dest='update_core',
                        help='Apply WP Core updates.')
    parser.add_argument('-D', '--update-db',
                        default=False,
                        action='store_true',
                        dest='update_db',
                        help='Apply WP Database updates.')
    parser.add_argument('-P', '--update-plugins',
                        default=False,
                        action='store_true',
                        dest='update_plugins',
                        help='Update all plugins.')
    parser.add_argument('-T', '--update-themes',
                        default=False,
                        action='store_true',
                        dest='update_themes',
                        help='Update all themes.')
    parser.add_argument('-A', '--update-all',
                        default=False,
                        action='store_true',
                        dest='update_all',
                        help='Updates core, plugins and themes.')
    parser.add_argument('-E', '--delete-expired-transients',
                        default=False,
                        action='store_true',
                        dest='delete_expired_transients',
                        help='Deletes expired transients.')
    parser.add_argument('-O', '--optimize-database',
                        default=False,
                        action='store_true',
                        dest='optimize_database',
                        help='Optimizes database.')
    parser.add_argument('--full',
                        default=False,
                        action='store_true',
                        dest='full',
                        help='Updates all, and deletes expired transients. Does NOT optimize DB.')
    parser.add_argument('--hume',
                        action='store_true',
                        dest='hume',
                        help='Emits hume messages on error only. Needs humed.')
    parser.add_argument('--version',
                        action='version',
                        version='WordpressUpdater {}'.format(str(__version__)))
    parser.add_argument('-v', '--verbose',
                        default=False,
                        action='store_true',
                        dest='verbose',
                        help='Be more verbose.')
    parser.add_argument('-d', '--debug',
                        default=False,
                        action='store_true',
                        dest='debug',
                        help='Enable debugging messages.')
    parser.add_argument('-W', '--skip-wpcli-update',
                        default=False,
                        action='store_true',
                        dest='skip_wpcli_update',
                        help='Do not update WP-CLI on startup')
    parser.add_argument('--path-to-wpcli',
                        dest='path_to_wpcli',
                        default=None,
                        metavar='PATH',
                        help='''Path to the wp binary. If not specified, it will be searched in PATH.''')
    parser.add_argument('--skip-plugin',
                        action='append',
                        dest='skip_plugins',
                        metavar='PLUGIN_NAME',
                        help='''Skip updating the indicated plugin. Can be specified multiple times.
Multiple values separated by commas are NOT allowed''')
    parser.add_argument('--skip-theme',
                        action='append',
                        dest='skip_themes',
                        metavar='THEME_NAME',
                        help='''Skip updating the indicated theme. Can be specified multiple times.
Multiple values separated by commas are NOT allowed''')
    parser.add_argument('--run',
                        action='append',
                        dest='custom_cmds',
                        metavar='"WPCLI_COMMAND"',
                        help='''Construct and run a wp-cli command on each wordpress instance.
Necessary arguments will be automatically added.
Example: --run="plugin install wp-fail2ban --activate"''')
    parser.add_argument('--exec-timeout',
                        dest='exec_timeout',
                        metavar='"SECONDS"',
                        default=300,
                        help='''Subprocess execution timeout. Defaults to 5m / 300s.''')
    parser.add_argument('--explicit-path',
                        default=False,
                        action='store_true',
                        dest='explicit_path',
                        help='Treat <file> argument as a Wordpress root dir, skip Apache Config')

    # Now, parse the args
    args = parser.parse_args()
    if args.requiredtags is not None:
        args.requiredtags = [item for sublist
                             in args.requiredtags
                             for item in sublist]
    else:
        args.requiredtags = None

    # IT HAS BEGUN!
    try:
        dowp = DO_WP_Maintain(requiredtags=args.requiredtags,
                              configpaths=args.file,
                              explicit_path=args.explicit_path,
                              allow_root=args.allow_root,
                              verbose=args.verbose,
                              debug=args.debug,
                              hume=args.hume,
                              skip_plugins=args.skip_plugins,
                              skip_themes=args.skip_themes,
                              path_to_wpcli=args.path_to_wpcli,
                              exec_timeout=args.exec_timeout)
    except Exception as exc:
        printerr(exc)
        sys.exit(1)

    if args.skip_wpcli_update is False:
        dowp.update_wpcli()

    if args.list_only is True:  # Just list
        for wp in dowp.wp_list:
            pprint(wp)  # TODO: pretty print
        sys.exit(0)

    if args.update_core or args.update_all or args.full:
        dowp.update_core()

    if args.update_db or args.update_all or args.full:
        dowp.update_db()

    if args.update_plugins or args.update_all or args.full:
        dowp.update_plugins()

    if args.update_themes or args.update_all or args.full:
        dowp.update_themes()

    if args.delete_expired_transients or args.full:
        dowp.delete_expired_transients()

    if args.optimize_database:
        dowp.optimize_database()

    if args.custom_cmds:
        dowp.run_custom_cmds(args.custom_cmds)


if __name__ == '__main__':
    run()
