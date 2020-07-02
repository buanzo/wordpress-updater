#!/usr/bin/env python3
import os
import sys
import requests
import argparse
import subprocess
from pathlib import Path
from apacheconfig import make_loader
from pprint import pprint

__version__ = 0.5


def printerr(x):
    print(x, file=sys.stderr)


def pprinterr(x):
    from pprint import pprint
    pprint(x, stream=sys.stderr)


class DO_WP_Maintain():
    def __init__(self,
                 configpaths=None,
                 requiredtags=None,
                 allow_root=False,
                 verbose=False,
                 hume=False):
        # Always priority:
        if allow_root is False and os.geteuid() == 0:
            printerr('Running as root is not allowed. Check --help.')
            sys.exit(1)

        # Internal config
        self.DOMETAURLJSON = 'http://169.254.169.254/metadata/v1.json'
        # Internal setup
        self.allow_root = allow_root
        self.configpaths = configpaths
        self.verbose = verbose
        self.hume = hume

        # Tag matching functionality only works in DigitalOcean Droplets:
        if requiredtags is not None:
            self.requiredtags = set(requiredtags)   # remove dupes
            self.metadata = self.get_do_metadata()  # MAY return None
            if not self.is_droplet():
                raise(RuntimeError("Required tags only work on DigitalOcean"))
            if self.valid_droplet_tags() is False:
                raise(RuntimeError("Droplet lacks indicated tag requirements"))

        # Other runtime checks:
        if self.hume:  # Test
            try:
                import hume
            except ModuleNotFoundError:
                printerr('''--hume specified but cannot load hume module.
You might need to install and configure humed. Check
https://github.com/buanzo/hume/wiki''')
                sys.exit(10)

        self.roots_list = self.get_apache2_documentroots()
        if len(self.roots_list) == 0:
            raise(RuntimeError("No Apache2 DocumentRoots found. Check paths."))

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

    def _run(self, cmd, timeout=60):  # cmd must be a []
        # Is one minute enough as a timeout?
        # This function returns a dictionary
        # status = exit status
        # stdout = utf8-decoded stdout
        # stderr = utf8-decoded stderr
        # Does NOT manage stdin
        if not isinstance(cmd, list):
            raise(ValueError("cmd is not a list"))
        result = subprocess.run(cmd,
                                timeout=timeout,  # 10s default
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        retObj = {}
        retObj['status'] = result.returncode
        retObj['stdout'] = result.stdout.decode('utf-8')
        retObj['stderr'] = result.stderr.decode('utf-8')
        return(retObj)

    def wp_run(self, path, args):
        cmd = ['/usr/bin/wp', '--no-color']
        if self.allow_root is True:  # __init__ checks EUID and --allow-root
            cmd.append('--allow-root')
        cmd.append('--path={}'.format(path))
        cmd.extend(args)
        return(self._run(cmd))

    def _wp_get_version(self, path):
        args = ['core', 'version', ]
        version = self.wp_run(path=path, args=args)['stdout'].strip()
        if len(version) > 0:
            return(version)
        else:
            return(None)

    def update_core(self):
        args = ['core', 'update']
        for site in self.wp_list:
            path = site['path']
            if self.verbose:
                printerr('Updating Wordpress Core in {}'.format(path))
            r = self.wp_run(path=path, args=args)
            pprint(r)
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
            pprint(r)
            if r['status'] > 0:
                msg = 'Error updating database {}: {}'.format(path,
                                                              r['stderr'])
                printerr(msg)
                if self.hume:
                    self.Hume({'level': 'warning',
                               'msg': msg,
                               'task': 'WPUPDATER'})

    def update_plugins(self):
        args = ['plugin', 'update', '--all']
        for site in self.wp_list:
            path = site['path']
            if self.verbose:
                printerr('Updating All Wordpress Plugins in {}'.format(path))
            r = self.wp_run(path=path, args=args)
            if r['status'] > 0:
                msg = 'Error updating plugins {}: {}'.format(path, r['stderr'])
                printerr(msg)
                if self.hume:
                    self.Hume({'level': 'warning',
                               'msg': msg,
                               'task': 'WPUPDATER'})

    def update_themes(self):
        args = ['theme', 'update', '--all']
        for site in self.wp_list:
            path = site['path']
            if self.verbose:
                printerr('Updating All Wordpress Themes in {}'.format(path))
            r = self.wp_run(path=path, args=args)
            if r['status'] > 0:
                msg = 'Error updating themes {}: {}'.format(path, r['stderr'])
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

    def _extract_documentroots(self, myDict, someList=None):
        # Credits to https://www.reddit.com/user/pushme2/
        # https://www.reddit.com/r/learnpython/comments/25im14/python_3_searching_recursively_through_a/
        if someList is None:
            myList = []
        else:
            myList = someList
        for key, value in myDict.items():
            if isinstance(value, dict):
                self._extract_documentroots(value, myList)
            else:
                if key == 'documentroot':
                    myList.append(value)
        return(myList)

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
                        help='Updates core, plugins and themes.')
    parser.add_argument('--full',
                        default=False,
                        action='store_true',
                        dest='full',
                        help='Updates all, and deletes expired transients.')
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
                              allow_root=args.allow_root,
                              verbose=args.verbose,
                              hume=args.hume)
    except Exception as exc:
        printerr(exc)
        sys.exit(1)

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


if __name__ == '__main__':
    run()
