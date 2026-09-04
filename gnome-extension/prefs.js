import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class AorusctlPrefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();

        const page = new Adw.PreferencesPage({title: 'aorusctl'});
        window.add(page);

        const panel = new Adw.PreferencesGroup({
            title: 'Top bar',
            description: 'What the panel shows, left to right.',
        });
        page.add(panel);

        const sw = (title, subtitle, key) => {
            const row = new Adw.SwitchRow({title, subtitle});
            panel.add(row);
            settings.bind(key, row, 'active', Gio.SettingsBindFlags.DEFAULT);
        };
        sw('Icon', null, 'show-icon');
        sw('CPU temperature', null, 'show-cpu');
        sw('GPU temperature', null, 'show-gpu');
        sw('Fan speed', null, 'show-fan');
        sw('Fan mode', null, 'show-mode');

        const unit = new Adw.ComboRow({
            title: 'Fan readout',
            subtitle: 'RPM of the CPU fan, or duty as a percentage',
            model: Gtk.StringList.new(['rpm', 'duty']),
        });
        panel.add(unit);
        const units = ['rpm', 'duty'];
        unit.selected = Math.max(0, units.indexOf(settings.get_string('fan-unit')));
        unit.connect('notify::selected', () =>
            settings.set_string('fan-unit', units[unit.selected]));

        const conn = new Adw.PreferencesGroup({
            title: 'Dashboard',
            description: 'The extension reads from the aorusctl web service, which '
                + 'already runs as root, so it needs no privileges itself. Without it '
                + 'the extension falls back to reading sysfs and the controls are '
                + 'disabled.',
        });
        page.add(conn);

        const url = new Adw.EntryRow({title: 'Address'});
        url.text = settings.get_string('api-url');
        url.connect('changed', () => settings.set_string('api-url', url.text));
        conn.add(url);

        const interval = new Adw.SpinRow({
            title: 'Refresh interval',
            subtitle: 'Seconds between updates',
            adjustment: new Gtk.Adjustment({
                lower: 1, upper: 60, step_increment: 1, page_increment: 5,
            }),
        });
        conn.add(interval);
        settings.bind('refresh-interval', interval, 'value', Gio.SettingsBindFlags.DEFAULT);
    }
}
