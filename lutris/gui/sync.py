from gi.repository import Gtk, GLib, Gio, GObject
from lutris.gui.widgets.utils import get_runner_icon
from lutris.gui.dialogs import NoticeDialog
from lutris.services import get_services
from lutris.settings import read_setting, write_setting
from lutris.util.jobs import AsyncCall


class ServiceSyncRow(Gtk.Box):

    def __init__(self, service, dialog):
        super().__init__()
        self.set_spacing(20)

        self.identifier = service.__name__.split('.')[-1]
        self.service = service
        self.dialog = dialog
        self.name = service.NAME
        self.sources = service.SOURCES

        icon = get_runner_icon(self.identifier)
        self.pack_start(icon, False, False, 0)

        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        label = Gtk.Label(xalign=0, yalign=1)
        label.set_markup("<b>{}</b>".format(self.name))
        label_box.pack_start(label, True, True, 0)
        label_sources = Gtk.Label(xalign=0, yalign=0)
        label_sources.set_markup('<i>{}</i>'.format(', '.join(self.sources)))
        label_box.pack_start(label_sources, True, True, 0)
        self.pack_start(label_box, True, True, 0)

        self.actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.pack_start(self.actions, False, False, 0)

        self.update()

    def update(self):
        self.actions.foreach(lambda child: child.destroy())

        if hasattr(self.service, "sync_with_lutris"):
            sync_switch = Gtk.Switch()
            sync_switch.set_tooltip_text("Sync when Lutris starts")
            sync_switch.props.valign = Gtk.Align.CENTER
            sync_switch.connect('notify::active', self.on_switch_changed)
            if read_setting('sync_at_startup', self.identifier) == 'True':
                sync_switch.set_state(True)
            self.actions.pack_start(sync_switch, True, True, 0)

            sync_button = Gtk.Button("Sync")
            sync_button.set_tooltip_text("Sync now")
            sync_button.connect('clicked', lambda w: GLib.idle_add(self.service.sync_with_lutris))
            self.actions.pack_start(sync_button, False, False, 0)

        if hasattr(self.service, "connect"):
            if self.service.is_connected():
                disconnect_button = Gtk.Button("Disconnect")
                disconnect_button.connect('clicked', lambda w: GLib.idle_add(self.service.disconnect, self.dialog))
                self.actions.pack_start(disconnect_button, False, False, 0)
            else:
                connect_button = Gtk.Button("Connect")
                connect_button.set_tooltip_text("Connect to %s" % self.name)
                connect_button.connect('clicked', lambda w: GLib.idle_add(self.service.connect, self.dialog))
                self.actions.pack_start(connect_button, False, False, 0)
                if hasattr(self.service, "sync_with_lutris"):
                    sync_button.destroy()

        self.actions.show_all()

    # def on_sync_button_clicked(self, button, sync_method):
    #     AsyncCall(sync_method, callback=self.on_service_synced)

    # def on_service_synced(self, caller, data):
    #     parent = self.get_toplevel()
    #     if not isinstance(parent, Gtk.Window):
    #         # The sync dialog may have closed
    #         parent = Gio.Application.get_default().props.active_window
    #     NoticeDialog("Games synced", parent=parent)

    def on_switch_changed(self, switch, data):
        state = switch.get_active()
        write_setting('sync_at_startup', state, self.identifier)


class SyncServiceDialog(Gtk.Dialog):

    __gsignals__ = {
        'update-service': (GObject.SIGNAL_RUN_FIRST, None, (str,))
    }

    def __init__(self, parent=None):
        super().__init__(title="Import games", parent=parent, use_header_bar=1)
        self.connect("delete-event", lambda *x: self.destroy())
        self.connect('update-service', self.update_service)
        self.set_border_width(10)
        self.set_size_request(512, 0)

        box_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.get_content_area().add(box_outer)

        description_label = Gtk.Label()
        description_label.set_markup("You can import games from local and online game sources, \n"
                                     "you can also choose to sync everytime Lutris starts")
        box_outer.pack_start(description_label, False, False, 5)

        separator = Gtk.Separator()
        box_outer.pack_start(separator, False, False, 0)

        self.rows = {}

        for service in get_services():
            sync_row = ServiceSyncRow(service, self)
            self.rows[sync_row.identifier] = sync_row
            box_outer.pack_start(sync_row, False, True, 0)
        box_outer.show_all()

    def update_service(self, dialog, serviceName):
        self.rows[serviceName].update()
