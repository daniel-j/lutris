# -*- coding: utf-8 -*-
"""Runner for the Steam platform"""
import os
import time
import shlex
import subprocess
import json
import shutil

from gi.repository import GLib

from lutris import settings
from lutris.gui.dialogs import DownloadDialog
from lutris.runners import wine
from lutris.thread import LutrisThread, HEARTBEAT_DELAY
from lutris.util.process import Process
from lutris.util import system, selective_merge
from lutris.util.log import logger
from lutris.util.bnet import read_config
from lutris.util.wineregistry import WineRegistry

# Redefine wine installer tasks
delete_registry_key = wine.delete_registry_key
create_prefix = wine.create_prefix
wineexec = wine.wineexec
winetricks = wine.winetricks
winecfg = wine.winecfg
winepath = wine.winepath
winekill = wine.winekill

BNET_INSTALLER_URL = "https://www.battle.net/download/getInstallerForGame?os=win&version=LIVE&gameProgram=BATTLENET_APP"

gamelist = {
    # 'destiny2':             ('Destiny 2', None),
    # 'diablo3':              ('Diablo III', None, 'D3'),
    # 'd3cn':                 ('Diablo III CN', None),
    # 'wtcg':                 ('Hearthstone', None, 'WTCG'),
    # 'hs_beta':              ('Hearthstone Beta', None),
    # 'heroes':               ('Heroes of the Storm', None, 'Hero'),
    # 'heroes_ptr':           ('Heroes of the Storm PTR', None),
    # 'heroes_tournament':    ('Heroes of the Storm Tournament', None),
    'prometheus':           ('Overwatch', 'Overwatch.exe', 'Pro'),
    'prometheus_test':      ('Overwatch (PTR)', 'Overwatch.exe', 'Pro'),
    # 's1':                   ('Starcraft I', None),
    # 's2':                   ('Starcraft II', None, 'SC2'),
    # 'wow':                  ('World of Warcraft', None, 'WoW')
}


def get_bnet_installer_dest():
    return os.path.join(settings.TMP_PATH, "Blizzard-Setup.exe")


def is_running():
    pid = system.get_pid('Battle.net.exe$')
    if pid:
        # If process is defunct, don't consider it as running
        process = Process(pid)
        return process.state != 'Z'
    else:
        return False


def kill():
    system.kill_pid(system.get_pid('Battle.net.exe$'))
    system.kill_pid(system.get_pid('Agent.exe$'))


# pylint: disable=C0103
class winebattlenet(wine.wine):
    description = "Runs Blizzard games with Battle.net (Wine)"
    multiple_versions = False
    human_name = "Wine Battle.net"
    platforms = ['Windows']
    runnable_alone = True
    depends_on = wine.wine
    default_arch = 'win32'
    game_options = [
        {
            'option': 'gameid',
            'type': 'choice',
            'label': 'Game',
            'choices': [(gamelist[gameid][0], gameid) for gameid in gamelist.keys()]
        },
        {
            'option': 'prefix',
            'type': 'directory_chooser',
            'label': 'Prefix',
            'help': ("The prefix (also named \"bottle\") used by Wine.\n"
                     "It's a directory containing a set of files and "
                     "folders making up a confined Windows environment.")
        },
        {
            'option': 'arch',
            'type': 'choice',
            'label': 'Prefix architecture',
            'choices': [('Auto', 'auto'),
                        ('32-bit', 'win32'),
                        ('64-bit', 'win64')],
            'default': 'auto',
            'help': ("The architecture of the Windows environment.\n"
                     "32-bit is recommended unless running "
                     "a 64-bit only game.")
        },
    ]

    def __init__(self, config=None):
        super(winebattlenet, self).__init__(config)
        self.own_game_remove_method = "Remove game data (through Battle.net)"
        self.no_game_remove_warning = True
        self.runner_options.insert(0, {
            'option': 'quit_bnet_on_play',
            'label': "Stop Battle.net when a game is launched",
            'type': 'bool',
            'default': False,
            'help': ("Shut down Battle.net and kills Agent.exe when a game is launched.")
        })
        self.runner_options.insert(1, {
            'option': 'run_without_bnet',
            'type': 'string',
            'label': 'Run without Battle.net (if possible)',
            'type': 'bool',
            'default': False,
            'help': ("This attempts to launch the game directly, without Battle.net")
        })
        # self.runner_options.insert(2, {
        #     'option': 'autolaunch',
        #     'type': 'bool',
        #     'default': False,
        #     'label': 'Launch the game automatically'
        # })
        self.runner_options.insert(2, {
            'option': 'streaming',
            'type': 'bool',
            'default': False,
            'label': 'Enable streaming in Battle.net client'
        })
        self.runner_options.insert(2, {
            'option': 'hwaccel',
            'type': 'bool',
            'default': False,
            'label': 'Enable hardware acceleration in Battle.net client'
        })
        self.system_options_override.insert(0, {
            'option': 'exclude_processes',
            'default': 'Agent.exe SystemSurvey.exe "Battle.net Helper.exe"'
        })

    def __repr__(self):
        return "Battle.net runner (%s)" % self.config

    @property
    def gameid(self):
        return self.game_config.get('gameid') or ''

    def get_game_name(self, gameid):
        try:
            return gamelist[gameid][0]
        except KeyError as e:
            return 'Unknown Game'

    @property
    def prefix_path(self):
        _prefix = \
            self.game_config.get('prefix') or \
            self.get_or_create_default_prefix(
                arch=self.game_config.get('arch')
            )
        return os.path.expanduser(_prefix)

    @property
    def browse_dir(self):
        """Return the path to open with the Browse Files action."""
        if not self.is_installed():
            installed = self.install_dialog()
            if not installed:
                return False
        return self.game_path

    @property
    def game_path(self):
        if not self.gameid:
            return
        return self.get_game_path_from_gameid(self.gameid)

    @property
    def working_dir(self):
        """Return the working directory to use when running the game."""
        if self.runner_config['run_without_bnet'] is True:
            return self.game_path
        return os.path.expanduser("~/")

    @property
    def launch_args(self):
        args = [self.get_executable(), self.get_bnet_path()]

        return args

    def get_open_command(self, registry):
        """Return Battle.net's Open command, useful for locating Battle.net when it has
           been installed but not yet launched"""
        value = registry.query("Software/Classes/blizzard/Shell/Open/Command",
                               "default")
        if not value:
            return
        parts = value.split("\"")
        return parts[1].strip('\\')

    def get_bnet_games(self):
        """Return list of installed bnet games"""
        product_db = os.path.join(self.get_default_prefix(), 'drive_c/ProgramData/Battle.net/Agent/product.db')
        if not os.path.exists(product_db):
            return
        return read_config(product_db)

    def get_bnet_config(self):
        prefix = self.get_or_create_default_prefix()
        bnet_config_dir = os.path.join(prefix, 'drive_c/users', os.getlogin(), 'Application Data/Battle.net/')
        os.makedirs(bnet_config_dir, exist_ok=True)
        config_path = os.path.join(bnet_config_dir, 'Battle.net.config')
        config = {}
        if os.path.exists(config_path):
            with open(config_path, 'r') as bnet_config:
                config = json.loads(bnet_config.read())
        return config

    def set_bnet_config(self, config):
        prefix = self.get_or_create_default_prefix()
        bnet_config_dir = os.path.join(prefix, 'drive_c/users', os.getlogin(), 'Application Data/Battle.net/')
        os.makedirs(bnet_config_dir, exist_ok=True)
        config_path = os.path.join(bnet_config_dir, 'Battle.net.config')
        with open(config_path, 'w') as bnet_config:
            bnet_config.write(json.dumps(config, indent=4))

    @property
    def bnet_dir(self):
        """Return dir where Battle.net files lie"""
        bnet_path = self.get_bnet_path()
        if bnet_path:
            directory = os.path.dirname(bnet_path)
            if os.path.isdir(directory):
                return directory

    def get_bnet_path(self, prefix=None):
        """Return Battle.net exe's path"""

        candidates = [self.get_default_prefix()]
        for prefix in candidates:
            # Try the default install path
            bnet_path = os.path.join(
                prefix,
                "drive_c/Program Files/Battle.net/Battle.net Launcher.exe"
            )
            if os.path.exists(bnet_path):
                return bnet_path

            bnet_path = os.path.join(
                prefix,
                "drive_c/Program Files (x86)/Battle.net/Battle.net Launcher.exe"
            )
            if os.path.exists(bnet_path):
                return bnet_path

            # Try from the registry key
            user_reg = os.path.join(prefix, "user.reg")
            if not os.path.exists(user_reg):
                continue
            registry = WineRegistry(user_reg)
            bnet_path = self.get_open_command(registry)
            if not bnet_path:
                continue
            path = registry.get_unix_path(bnet_path)
            path = system.fix_path_case(path)
            if path:
                return path

    def install(self, version=None, downloader=None, callback=None):
        # if not system.check_required_libraries(['lib32-libldap', 'lib32-gnutls', 'lib32-libgpg-error']):
        #     if callback:
        #         callback()
        #     return False

        installer_path = get_bnet_installer_dest()

        def on_bnet_downloaded(*args):
            prefix = self.get_or_create_default_prefix()
            prefix64 = self.get_or_create_default_prefix('win64')
            logger.debug('Installing corefonts in Battle.net win32 wineprefix, this takes a while...')
            winetricks('eufonts fontsmooth=rgb', prefix=prefix, arch='win32', wine_path=self.get_executable(), blocking=True)
            # link some folders between the prefixes
            if prefix64 and prefix != prefix64:
                if not os.path.islink(os.path.join(prefix64, 'drive_c/users')):
                    shutil.rmtree(os.path.join(prefix64, 'drive_c/users'))
                    os.symlink(os.path.join(prefix, 'drive_c/users'), os.path.join(prefix64, 'drive_c/users'))
                os.makedirs(os.path.join(prefix, 'drive_c/ProgramData'), exist_ok=True)
                if not os.path.islink(os.path.join(prefix64, 'drive_c/ProgramData')):
                    shutil.rmtree(os.path.join(prefix64, 'drive_c/ProgramData'))
                    os.symlink(os.path.join(prefix, 'drive_c/ProgramData'), os.path.join(prefix64, 'drive_c/ProgramData'))
                logger.debug('Installing corefonts in Battle.net win64 wineprefix, this takes a while...')
                winetricks('eufonts fontsmooth=rgb', prefix=prefix64, arch='win64', wine_path=self.get_executable(), blocking=True)

            self.set_regedit_keys()

            default_config = {
                'Client': {
                    'HardwareAcceleration': 'false',
                    'Sound': {
                        'Enabled': 'false'
                    },
                    'Streaming': {
                        'StreamingEnabled': 'false'
                    }
                }
            }
            self.set_bnet_config(default_config)

            thread = wineexec(installer_path,
                              prefix=prefix,
                              wine_path=self.get_executable(),
                              working_dir="/tmp",
                              blocking=False,
                              exclude_processes=['Agent.exe', 'Battle.net.exe', 'Battle.net Helper.exe', 'SystemSurvey.exe'],
                              disable_runtime=False)

            def beat():
                if not thread.is_running:
                    on_bnet_installed()
                    return False
                return True

            def on_bnet_installed():
                self.shutdown()
                if callback:
                    callback()

            thread.set_stop_command(on_bnet_installed)
            GLib.timeout_add(HEARTBEAT_DELAY, beat)

        if downloader:
            downloader(BNET_INSTALLER_URL, installer_path, on_bnet_downloaded)
        else:
            dialog = DownloadDialog(BNET_INSTALLER_URL, installer_path)
            dialog.run()
            on_bnet_downloaded()

    def is_wine_installed(self, version=None, fallback=True):
        return super(winebattlenet, self).is_installed(version=version, fallback=fallback)

    def is_installed(self, version=None, fallback=True):
        """Checks if wine is installed and if the Battle.net executable is on the
           harddrive.
        """
        wine_installed = self.is_wine_installed(version, fallback)
        if not wine_installed:
            logger.warning('wine is not installed')
            return False
        bnet_path = self.get_bnet_path()
        if not bnet_path or not os.path.exists(self.get_default_prefix()):
            return False
        return os.path.exists(bnet_path)

    def get_gameid_list(self):
        """Return the list of gameids of all user's games"""
        bnet_config = self.get_bnet_games()
        if bnet_config:
            return bnet_config.keys()

    def get_game_path_from_gameid(self, gameid):
        """Return the game directory"""
        bnet_config = self.get_bnet_games()
        try:
            return bnet_config[gameid]['path']
        except KeyError as e:
            logger.warning("Data path for Battle.net game %s not found: %s", gameid, e)

    def create_prefix(self, prefix_dir, arch=None):
        logger.debug("Creating default winebattlenet prefix")
        if not arch:
            arch = self.default_arch
        wine_path = self.get_executable()

        if not os.path.exists(os.path.dirname(prefix_dir)):
            os.makedirs(os.path.dirname(prefix_dir))
        create_prefix(prefix_dir, arch=arch, wine_path=wine_path)

    def get_default_prefix(self, arch=None):
        """Return the default prefix' path."""
        if not arch:
            arch = self.default_arch
        path = 'prefix'
        if arch == 'win64':
            path += '64'
        return os.path.join(settings.RUNNER_DIR, 'winebattlenet', path)

    def get_or_create_default_prefix(self, arch=None):
        """Return the default prefix' path. Create it if it doesn't exist"""
        if not arch:
            arch = self.default_arch
        prefix = self.get_default_prefix(arch=arch)
        if not os.path.exists(prefix):
            self.create_prefix(prefix, arch=arch)
        return prefix

    def install_game(self, gameid, generate_acf=False):
        if not gameid:
            raise ValueError("Missing gameid in winebattlenet.install_game")
        command = self.launch_args + ["--install", "--game=%s" % gameid]
        subprocess.Popen(command, env=self.get_env())

    def prelaunch(self):
        super(winebattlenet, self).prelaunch()

        def check_shutdown(is_running, times=10):
            for x in range(1, times + 1):
                time.sleep(1)
                if not is_running():
                    return True
        # Stop existing winebattlenet to prevent Wine prefix/version problems
        if is_running():
            logger.info("Waiting for Battle.net to shutdown...")
            self.shutdown()
            if not check_shutdown(is_running):
                logger.info("Battle.net does not shut down, killing it...")
                kill()
                if not check_shutdown(is_running, 5):
                    logger.error("Failed to shut down Battle.net :(")
                    return False

        return True

    def get_run_data(self):
        return {'command': self.launch_args, 'env': self.get_env(os_env=False)}

    def play(self):
        self.game_launch_time = time.localtime()
        game_args = self.game_config.get('args') or ''

        launch_info = {}
        launch_info['env'] = self.get_env(os_env=False)

        if self.runner_config.get('x360ce-path'):
            self.setup_x360ce(self.runner_config['x360ce-path'])

        if self.runner_config['run_without_bnet'] is True:
            # Start without Battle.net
            config = self.get_bnet_games()
            game_dir = config[self.gameid]['path']
            game_dir = winepath(game_dir, prefix=self.prefix_path)
            if not os.path.isdir(game_dir):
                return {'error': 'DIR_NOT_FOUND', 'file': game_dir}
            game_path = os.path.join(game_dir, gamelist[self.gameid][1])
            if not os.path.exists(game_path):
                return {'error': 'FILE_NOT_FOUND', 'file': game_path}
            command = [self.get_executable()]
            runner_args = self.runner_config.get('args') or ''
            if runner_args:
                for arg in shlex.split(runner_args):
                    command.append(arg)
            command.append(game_path)
            if game_args:
                for arg in shlex.split(game_args):
                    command.append(arg)
        else:
            # Start through Battle.net
            config = self.get_bnet_config()
            selective_merge(config, {'Client': {
                'HardwareAcceleration': 'true' if self.runner_config.get('hwaccel') else 'false',
                'Streaming': {'StreamingEnabled': 'true' if self.runner_config.get('streaming') else 'false'}
            }})
            self.set_bnet_config(config)
            command = self.launch_args
            if self.runner_config.get('autolaunch'):
                command.append('--exec="launch_uid %s"' % self.gameid)
            elif not game_args:
                command.append('battlenet://%s' % gamelist[self.gameid][2])
            else:
                for arg in shlex.split(game_args):
                    command.append(arg)
        launch_info['command'] = command
        return launch_info

    def beat(self, thread):
        gameExe = gamelist[self.gameid][1]
        processes = thread.get_processes_list()
        names = [child.name for child in processes]

        if self.runner_config.get('quit_bnet_on_play') and gameExe:
            if gameExe in names and processes[names.index(gameExe)].state == 'S':
                for child in processes:
                    if child.name in ['Agent.exe', 'Battle.net.exe'] and child.state != 'Z':
                        logger.debug(gameExe + ' is running. Killing ' + str(child))
                        system.kill_pid(child.pid)

    def shutdown(self):
        """Shutdown Battle.net in a clean way."""
        logger.debug("Stopping all winebattlenet processes")
        super(winebattlenet, self).stop()

    def stop(self):
        self.shutdown()

    def remove_game_data(self, gameid=None, **kwargs):
        if not self.is_installed():
            installed = self.install_dialog()
            if not installed:
                return False
        gameid = gameid if gameid else self.gameid

        env = self.get_env(os_env=False)
        command = [self.get_executable(), os.path.join(self.get_default_prefix(), 'drive_c/ProgramData/Battle.net/Agent/Blizzard Uninstaller.exe'), '--uid=%s' % gameid, '--displayname=%s' % self.get_game_name(gameid)]
        self.prelaunch()
        thread = LutrisThread(command, runner=self, env=env, watch=False)
        thread.start()
