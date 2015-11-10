# -*- coding:Utf-8 -*-
"""Generic runner."""
import os
import platform

from gi.repository import Gtk

from lutris import pga, settings, runtime
from lutris.config import LutrisConfig
from lutris.gui import dialogs
from lutris.thread import LutrisThread
from lutris.util.cache import lru_cache
from lutris.util.extract import extract_archive
from lutris.util.log import logger
from lutris.util import system
from lutris.util.http import Request


def get_arch():
    """TODO Deprecate get_arch"""
    machine = platform.machine()
    if '64' in machine:
        return 'x64'
    elif '86' in machine:
        return 'i386'


def get_arch_for_api():
    """Return the architecture returning values compatible with the reponses
    from the API
    """
    machine = platform.machine()
    if '64' in machine:
        return 'x86_64'
    elif '86' in machine:
        return 'i386'


class Runner(object):
    """Generic runner (base class for other runners)."""

    multiple_versions = False
    tarballs = None
    platform = NotImplemented
    runnable_alone = False
    game_options = []
    runner_options = []
    system_options_override = []
    context_menu_entries = []

    def __init__(self, config=None):
        """Initialize runner."""
        self.arch = get_arch()
        self.logger = logger
        self.config = config
        self.game_data = {}
        if config:
            self.game_data = pga.get_game_by_field(
                self.config.game_config_id, 'configpath'
            )

    @property
    def description(self):
        """Return the class' docstring as the description."""
        return self.__doc__

    @description.setter
    def description(self, value):
        """Leave the ability to override the docstring."""
        self.__doc__ = value

    @property
    def name(self):
        return self.__class__.__name__

    @property
    @lru_cache(maxsize=50)  # /!\ Caution
    def default_config(self):
        return LutrisConfig(runner_slug=self.name)

    @property
    def game_config(self):
        """Return the cascaded game config as a dict."""
        return self.config.game_config if self.config else {}

    @property
    def runner_config(self):
        """Return the cascaded runner config as a dict."""
        if self.config:
            return self.config.runner_config
        return self.default_config.runner_config

    @property
    def system_config(self):
        """Return the cascaded system config as a dict."""
        if self.config:
            return self.config.system_config
        return self.default_config.system_config

    @property
    def default_path(self):
        """Return the default path where games are installed."""
        return self.system_config.get('game_path')

    @property
    def browse_dir(self):
        """Return the path to open with the Browse Files action."""
        for key in self.game_config:
            if key in ['exe', 'main_file', 'rom', 'disk', 'iso']:
                path = os.path.dirname(self.game_config.get(key))
                if not os.path.isabs(path):
                    path = os.path.join(self.game_path, path)
                return path

        if self.game_data.get('directory'):
            return self.game_data.get('directory')

    @property
    def game_path(self):
        """Return the directory where the game is installed."""
        if self.game_data.get('directory'):
            return self.game_data.get('directory')
        return self.system_config.get('game_path')

    @property
    def working_dir(self):
        """Return the working directory to use when running the game."""
        return os.path.expanduser("~/")

    @property
    def machine(self):
        self.logger.error("runner.machine accessed, please use platform")
        return self.platform

    def get_executable(self):
        raise NotImplementedError("Runners must implement get_executable")

    def play(self):
        """Dummy method, must be implemented by derived runnners."""
        raise NotImplementedError("Implement the play method in your runner")

    def get_run_data(self):
        """Return dict with command (exe & args list) and env vars (dict).

        Reimplement in derived runner if need be."""
        exe = self.get_executable()
        return {'command': [exe], 'env': {}}

    def run(self, *args):
        """Run the runner alone."""
        if not self.runnable_alone:
            return
        if not self.is_installed():
            installed = self.install_dialog()
            if not installed:
                return

        if self.use_runtime():
            if runtime.is_outdated() or runtime.is_updating():
                result = dialogs.RuntimeUpdateDialog().run()
                if not result == Gtk.ResponseType.OK:
                    return

        command_data = self.get_run_data()
        command = command_data.get('command')
        env = (command_data.get('env') or {}).copy()
        if self.use_runtime():
            env.update(runtime.get_env())

        thread = LutrisThread(command, runner=self, env=env, watch=False)
        thread.start()

    def use_runtime(self, system_config=None):
        disable_runtime = self.system_config.get('disable_runtime')
        env_runtime = os.getenv('LUTRIS_RUNTIME')
        if env_runtime and env_runtime.lower() in ('0', 'off'):
            disable_runtime = True
        return not disable_runtime

    def install_dialog(self):
        """Ask the user if she wants to install the runner.

        Return success of runner installation.
        """
        dialog = dialogs.QuestionDialog({
            'question': ("The required runner is not installed.\n"
                         "Do you wish to install it now?"),
            'title': "Required runner unavailable"
        })
        if Gtk.ResponseType.YES == dialog.result:
            return self.install()
        return False

    def is_installed(self):
        """Return True if runner is installed else False."""
        executable = self.get_executable()
        if executable and os.path.exists(executable):
            return True

    def get_runner_url(self):
        runner_api_url = 'https://lutris.net/api/runners/{}'.format(self.name)
        request = Request(runner_api_url)
        response = request.get()
        response_content = response.json
        if response_content:
            versions = response_content.get('versions') or []
            versions = [
                version for version in versions
                if version['architecture'] == get_arch_for_api()
            ]
            if len(versions) == 1:
                return versions[0]['url']
            elif len(versions) > 1:
                default_version = [
                    version for version in versions
                    if version['default'] is True
                ]
                if default_version:
                    return default_version[0]

    def install(self):
        """Install runner using package management systems."""
        if self.tarballs:
            logger.debug('Port runner %s to use the REST API' % self.name)
            tarball = self.tarballs.get(self.arch)
            opts = {}
        else:
            tarball = self.get_runner_url()
            opts = {
                'source_url': ''
            }
        if tarball:
            is_extracted = self.download_and_extract(tarball, **opts)
            return is_extracted
        else:
            dialogs.ErrorDialog(
                'This runner is not available for your platform'
            )
            return False

    def download_and_extract(self, tarball, dest=settings.RUNNER_DIR, **opts):
        merge_single = opts.get('merge_single', False)
        source_url = opts.get('source_url', settings.RUNNERS_URL)
        if source_url:
            # Legacy system for runners using self.tarballs
            tarball_filename = tarball
        else:
            # New system, tarball is an URL
            tarball_filename = os.path.basename(tarball)
        runner_archive = os.path.join(settings.CACHE_DIR, tarball_filename)
        dialog = dialogs.DownloadDialog(source_url + tarball, runner_archive)
        dialog.run()
        if not os.path.exists(runner_archive):
            logger.error("Can't find %s, aborting install", runner_archive)
            return False
        extract_archive(runner_archive, dest, merge_single=merge_single)
        os.remove(runner_archive)
        return True

    def remove_game_data(self, game_path=None):
        system.remove_folder(game_path)
