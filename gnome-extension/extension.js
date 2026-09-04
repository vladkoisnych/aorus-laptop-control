/* aorusctl GNOME Shell extension
 *
 * Reads from the aorusctl web service on loopback, which already runs as root
 * under systemd, so nothing here needs privileges of its own. When that service
 * is not running it falls back to reading sysfs directly, which still gives
 * temperatures and fan readings but no controls.
 */

import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Soup from 'gi://Soup';
import Clutter from 'gi://Clutter';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const AORUS = '/sys/devices/platform/aorus_laptop';
const FAN_MODES = ['normal', 'silent', 'gaming', 'custom', 'auto', 'fixed'];
const QUICK_MODES = ['normal', 'silent', 'gaming'];

function readFile(path) {
    try {
        const [ok, bytes] = GLib.file_get_contents(path);
        if (!ok)
            return null;
        return new TextDecoder().decode(bytes).trim();
    } catch (e) {
        return null;
    }
}

function readInt(path) {
    const v = readFile(path);
    if (v === null)
        return null;
    const n = parseInt(v.split(/\s+/)[0], 10);
    return Number.isNaN(n) ? null : n;
}

function firstGlob(dir, predicate) {
    try {
        const d = Gio.File.new_for_path(dir);
        const en = d.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
        let info;
        while ((info = en.next_file(null)) !== null) {
            const p = `${dir}/${info.get_name()}`;
            if (predicate(p))
                return p;
        }
    } catch (e) {
        // sysfs layout differs, nothing to do
    }
    return null;
}

/* Enough of a reading to be useful with no dashboard running. */
function readSysfs() {
    const out = {cpu: {}, gpu: {}, fan: {}, source: 'sysfs'};

    const coretemp = firstGlob('/sys/class/hwmon',
        p => readFile(`${p}/name`) === 'coretemp');
    if (coretemp) {
        for (let i = 1; i <= 24; i++) {
            if (readFile(`${coretemp}/temp${i}_label`)?.includes('Package')) {
                const t = readInt(`${coretemp}/temp${i}_input`);
                if (t !== null)
                    out.cpu.package_c = t / 1000;
                break;
            }
        }
    }

    const mode = readInt(`${AORUS}/fan_mode`);
    if (mode !== null)
        out.fan.mode = FAN_MODES[mode] ?? String(mode);

    const hw = firstGlob(`${AORUS}/hwmon`, p => readInt(`${p}/fan1_input`) !== null);
    if (hw) {
        const c = readInt(`${hw}/fan1_input`);
        const g = readInt(`${hw}/fan2_input`);
        out.fan.rpm = {};
        if (c) out.fan.rpm.cpu = c;
        if (g) out.fan.rpm.gpu = g;
        const gt = readInt(`${hw}/temp2_input`);
        if (gt) out.gpu.temp_c = gt / 1000;
        if (out.cpu.package_c === undefined) {
            const ct = readInt(`${hw}/temp1_input`);
            if (ct) out.cpu.package_c = ct / 1000;
        }
    }
    const pwm = readInt(`${AORUS}/fan_pwm`);
    if (pwm !== null)
        out.fan.pwm_pct = Math.round(pwm * 100 / 229);
    return out;
}

const Indicator = GObject.registerClass(
class Indicator extends PanelMenu.Button {
    _init(ext) {
        super._init(0.0, 'aorusctl');
        this._ext = ext;
        this._settings = ext.getSettings();
        this._session = new Soup.Session({timeout: 4});
        this._data = null;
        this._live = false;
        this._rows = {};

        this._box = new St.BoxLayout({style_class: 'aorusctl-panel'});
        this._icon = new St.Icon({
            gicon: Gio.icon_new_for_string(`${ext.path}/icons/aorusctl-symbolic.svg`),
            style_class: 'system-status-icon aorusctl-icon',
            icon_size: 16,
        });
        this._box.add_child(this._icon);
        this._label = new St.Label({
            text: '…',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'aorusctl-panel-label',
        });
        this._box.add_child(this._label);
        this.add_child(this._box);

        this._buildMenu();
        this._applyTheme();
        this._applyPanelPrefs();

        this._ifaceSettings = new Gio.Settings({schema: 'org.gnome.desktop.interface'});
        this._themeId = this._ifaceSettings.connect('changed::color-scheme',
            () => this._applyTheme());
        this._settingsId = this._settings.connect('changed', (_s, key) => {
            if (key === 'refresh-interval')
                this._restartTimer();
            else
                this._applyPanelPrefs();
        });

        this._refresh();
        this._restartTimer();
    }

    /* ---------- theme ---------- */

    _isDark() {
        const iface = this._ifaceSettings
            ?? new Gio.Settings({schema: 'org.gnome.desktop.interface'});
        const scheme = iface.get_string('color-scheme');
        if (scheme === 'prefer-dark')
            return true;
        if (scheme === 'prefer-light')
            return false;
        // 'default' is GNOME's light style. Some distributions still ship a dark
        // GTK theme alongside it, so use that as a second opinion.
        let gtk = '';
        try {
            gtk = iface.get_string('gtk-theme');
        } catch (e) {
            gtk = '';
        }
        return /dark/i.test(gtk);
    }

    _applyTheme() {
        const light = !this._isDark();
        for (const w of [this, this._content]) {
            if (!w)
                continue;
            if (light)
                w.add_style_class_name('aorusctl-light');
            else
                w.remove_style_class_name('aorusctl-light');
        }
        this._paintTemps();
    }

    _applyPanelPrefs() {
        this._icon.visible = this._settings.get_boolean('show-icon');
        this._render();
    }

    /* ---------- menu ---------- */

    _row(parent, key, label) {
        const row = new St.BoxLayout({style_class: 'aorusctl-row'});
        const k = new St.Label({text: label, style_class: 'aorusctl-key'});
        k.opacity = 150;
        row.add_child(k);
        const v = new St.Label({text: '--', style_class: 'aorusctl-val', x_expand: true});
        v.clutter_text.set_x_align(Clutter.ActorAlign.END);
        row.add_child(v);
        parent.add_child(row);
        this._rows[key] = v;
        return v;
    }

    _heading(parent, text) {
        const h = new St.Label({text, style_class: 'aorusctl-heading'});
        h.opacity = 135;
        parent.add_child(h);
    }

    _buildMenu() {
        this._content = new St.BoxLayout({
            vertical: true,
            style_class: 'aorusctl-menu',
        });

        this._heading(this._content, 'cpu');
        this._row(this._content, 'cpu_temp', 'temperature');
        this._row(this._content, 'cpu_power', 'package power');
        this._row(this._content, 'cpu_freq', 'frequency');

        this._heading(this._content, 'gpu');
        this._row(this._content, 'gpu_temp', 'temperature');
        this._row(this._content, 'gpu_power', 'power draw');
        this._row(this._content, 'gpu_util', 'utilisation');

        this._heading(this._content, 'fans');
        this._row(this._content, 'fan_mode', 'mode');
        this._row(this._content, 'fan_duty', 'duty');
        this._row(this._content, 'fan_cpu', 'cpu fan');
        this._row(this._content, 'fan_gpu', 'gpu fan');

        this._modeBox = new St.BoxLayout({style_class: 'aorusctl-modes'});
        this._modeButtons = {};
        for (const m of QUICK_MODES) {
            const b = new St.Button({
                label: m,
                style_class: 'aorusctl-btn',
                can_focus: true,
            });
            b.connect('clicked', () => this._setMode(m));
            this._modeBox.add_child(b);
            this._modeButtons[m] = b;
        }
        this._content.add_child(this._modeBox);

        this._status = new St.Label({text: '', style_class: 'aorusctl-status'});
        this._status.opacity = 165;
        this._status.clutter_text.line_wrap = true;
        this._content.add_child(this._status);

        const item = new PopupMenu.PopupBaseMenuItem({
            reactive: false,
            can_focus: false,
            style_class: 'aorusctl-item',
        });
        item.add_child(this._content);
        this.menu.addMenuItem(item);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const open = new PopupMenu.PopupMenuItem('Open dashboard');
        open.connect('activate', () => {
            Gio.AppInfo.launch_default_for_uri(this._settings.get_string('api-url'), null);
            this.menu.close(true);
        });
        this.menu.addMenuItem(open);

        const prefs = new PopupMenu.PopupMenuItem('Settings');
        prefs.connect('activate', () => {
            this._ext.openPreferences();
            this.menu.close(true);
        });
        this.menu.addMenuItem(prefs);
    }

    /* ---------- data ---------- */

    _restartTimer() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }
        const secs = Math.max(1, this._settings.get_int('refresh-interval'));
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, secs, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _refresh() {
        const url = `${this._settings.get_string('api-url')}/api/status`;
        let msg;
        try {
            msg = Soup.Message.new('GET', url);
        } catch (e) {
            this._fallback();
            return;
        }
        if (!msg) {
            this._fallback();
            return;
        }
        this._session.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null, (session, res) => {
            try {
                const bytes = session.send_and_read_finish(res);
                if (msg.get_status() !== Soup.Status.OK)
                    throw new Error(`HTTP ${msg.get_status()}`);
                this._data = JSON.parse(new TextDecoder().decode(bytes.get_data()));
                this._live = true;
            } catch (e) {
                this._fallback();
                return;
            }
            this._render();
        });
    }

    _fallback() {
        this._data = readSysfs();
        this._live = false;
        this._render();
    }

    _post(action, value) {
        const url = `${this._settings.get_string('api-url')}/api/action`;
        const msg = Soup.Message.new('POST', url);
        if (!msg)
            return;
        const body = JSON.stringify({action, value});
        msg.set_request_body_from_bytes('application/json',
            new GLib.Bytes(new TextEncoder().encode(body)));
        this._session.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null, (session, res) => {
            let text = '';
            try {
                const bytes = session.send_and_read_finish(res);
                const j = JSON.parse(new TextDecoder().decode(bytes.get_data()));
                text = j.message || '';
                if (!j.ok)
                    text = `failed: ${text}`;
            } catch (e) {
                text = `could not reach the dashboard: ${e.message}`;
            }
            this._status.text = text.split('\n')[0];
            this._refresh();
        });
    }

    _setMode(mode) {
        if (!this._live) {
            this._status.text = 'start aorusctl-web.service to change anything from here';
            return;
        }
        this._status.text = `setting ${mode}…`;
        this._post('fan_mode', mode);
    }

    /* ---------- rendering ---------- */

    _tempClass(t) {
        if (t === null || t === undefined)
            return 'aorusctl-dim';
        if (t >= 88)
            return 'aorusctl-hot';
        if (t >= 75)
            return 'aorusctl-warn';
        return 'aorusctl-ok';
    }

    _paintTemps() {
        if (!this._data)
            return;
        const t = this._data.cpu?.package_c ?? null;
        const cls = this._tempClass(t);
        for (const c of ['aorusctl-ok', 'aorusctl-warn', 'aorusctl-hot', 'aorusctl-dim'])
            this._label.remove_style_class_name(c);
        this._label.add_style_class_name(cls);
    }

    _render() {
        const d = this._data;
        if (!d) {
            this._label.text = '…';
            return;
        }
        const n = (v, digits = 0) =>
            (v === null || v === undefined) ? null : Number(v).toFixed(digits);

        const cpuT = d.cpu?.package_c ?? null;
        const gpuT = d.gpu?.temp_c ?? null;
        const rpm = d.fan?.rpm?.cpu ?? null;
        const duty = d.fan?.pwm_pct ?? null;
        const mode = d.fan?.mode ?? null;

        /* panel */
        const parts = [];
        if (this._settings.get_boolean('show-cpu') && cpuT !== null)
            parts.push(`${n(cpuT)}°`);
        if (this._settings.get_boolean('show-gpu') && gpuT !== null)
            parts.push(`${n(gpuT)}°`);
        if (this._settings.get_boolean('show-fan')) {
            if (this._settings.get_string('fan-unit') === 'duty' && duty !== null)
                parts.push(`${duty}%`);
            else if (rpm !== null)
                parts.push(rpm >= 1000 ? `${(rpm / 1000).toFixed(1)}k` : `${rpm}`);
        }
        if (this._settings.get_boolean('show-mode') && mode)
            parts.push(mode);
        this._label.text = parts.length ? parts.join('  ') : 'no data';
        this._paintTemps();

        /* popup */
        const set = (k, v) => {
            if (this._rows[k])
                this._rows[k].text = v ?? '--';
        };
        set('cpu_temp', cpuT !== null ? `${n(cpuT, 1)} °C` : null);
        set('cpu_power', d.cpu?.power_w != null ? `${n(d.cpu.power_w, 1)} W` : null);
        set('cpu_freq', d.cpu?.freq_mhz_avg != null ? `${n(d.cpu.freq_mhz_avg)} MHz` : null);
        set('gpu_temp', gpuT !== null ? `${n(gpuT)} °C` : null);
        set('gpu_power', d.gpu?.power_w != null ? `${n(d.gpu.power_w, 1)} W` : null);
        set('gpu_util', d.gpu?.util_pct != null ? `${n(d.gpu.util_pct)} %` : null);
        set('fan_mode', mode);
        set('fan_duty', duty !== null ? `${duty} %` : null);
        set('fan_cpu', d.fan?.rpm?.cpu != null ? `${d.fan.rpm.cpu} rpm` : null);
        set('fan_gpu', d.fan?.rpm?.gpu != null ? `${d.fan.rpm.gpu} rpm` : null);

        for (const m of QUICK_MODES) {
            const b = this._modeButtons[m];
            if (m === mode)
                b.add_style_class_name('aorusctl-btn-on');
            else
                b.remove_style_class_name('aorusctl-btn-on');
            b.reactive = this._live;
            b.opacity = this._live ? (m === mode ? 255 : 190) : 100;
        }
        if (!this._live && !this._status.text.startsWith('failed'))
            this._status.text = 'reading sysfs directly. Start aorusctl-web.service for '
                + 'GPU readings and controls.';
        else if (this._live && this._status.text.startsWith('reading sysfs'))
            this._status.text = '';
    }

    destroy() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }
        if (this._settingsId) {
            this._settings.disconnect(this._settingsId);
            this._settingsId = null;
        }
        if (this._themeId) {
            this._ifaceSettings.disconnect(this._themeId);
            this._themeId = null;
        }
        this._session?.abort();
        this._session = null;
        super.destroy();
    }
});

export default class AorusctlExtension extends Extension {
    enable() {
        this._indicator = new Indicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
