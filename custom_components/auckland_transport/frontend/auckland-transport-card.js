/*
  Auckland Transport Card
  Type: custom:auckland-transport-card

  Works with both integration versions:

  * v0.3 and newer publish `schema_version: 2` plus a compact `departures` array
    on the stop sensor. That path gives platforms, official route colours, live
    occupancy, cancellations and the reason/alternative for a disruption.
  * v0.2 published flat `departure_1_*` attributes. When no `schema_version` is
    present the card falls back to reading those, so upgrading the card alone
    still works.

  Every configuration option from v0.2 is still honoured.
*/

/* global customElements, HTMLElement, document, window, CustomEvent */

const CARD_VERSION = 'v0.3.0';

// ---------------------------------------------------------------------------
// Short attribute keys used by the v0.3 integration (see attributes.py)
// ---------------------------------------------------------------------------
const K = {
  route: 'r',
  routeId: 'ri',
  destination: 'd',
  scheduled: 's',
  expected: 'e',
  delay: 'dl',
  minutes: 'm',
  platform: 'p',
  status: 'st',
  realtime: 'rt',
  color: 'c',
  textColor: 'tc',
  mode: 'mo',
  occupancy: 'oc',
  vehicle: 'v',
  tripId: 't',
  boarding: 'b',
  reason: 'why',
  alternative: 'alt',
  alertEffect: 'ae',
  alertSeverity: 'as',
};

const MODE_ICONS = {
  train: 'mdi:train',
  bus: 'mdi:bus',
  ferry: 'mdi:ferry',
  tram: 'mdi:tram',
  school_bus: 'mdi:bus-school',
  unknown: 'mdi:transit-connection-variant',
};

const OCCUPANCY = [
  { icon: 'mdi:account-outline', label: 'Empty' },
  { icon: 'mdi:account', label: 'Many seats' },
  { icon: 'mdi:account-multiple', label: 'Few seats' },
  { icon: 'mdi:account-group', label: 'Standing only' },
  { icon: 'mdi:account-group', label: 'Very full' },
  { icon: 'mdi:account-alert', label: 'Full' },
  { icon: 'mdi:account-off', label: 'Not boarding' },
];

const STATUS_LABELS = {
  on_time: 'On time',
  late: 'Late',
  early: 'Early',
  scheduled: 'Scheduled',
  cancelled: 'Cancelled',
  skipped: 'Stop skipped',
  arriving: 'Due',
  departing: 'Now',
};

const EFFECT_LABELS = {
  NO_SERVICE: 'No service',
  STOP_MOVED: 'Stop moved',
  MODIFIED_SERVICE: 'Modified service',
  DETOUR: 'Detour',
  ACCESSIBILITY_ISSUE: 'Access issue',
  SIGNIFICANT_DELAYS: 'Delays',
  OTHER_EFFECT: 'Notice',
};

const EFFECT_ICONS = {
  NO_SERVICE: 'mdi:close-octagon',
  STOP_MOVED: 'mdi:map-marker-off',
  MODIFIED_SERVICE: 'mdi:swap-horizontal',
  DETOUR: 'mdi:directions-fork',
  ACCESSIBILITY_ISSUE: 'mdi:wheelchair-accessibility',
  SIGNIFICANT_DELAYS: 'mdi:clock-alert-outline',
  OTHER_EFFECT: 'mdi:information-outline',
};

const DEFAULTS = {
  // General
  title: undefined,
  max_rows: undefined,
  filter: undefined,
  layout: 'rows', // 'rows' = v0.3 board, 'table' = the v0.2 look
  // Header
  header_logo: true,
  header_logo_size: 40,
  header_icon_show: true,
  header_icon: undefined,
  header_icon_color: undefined,
  header_icon_size: 28,
  // Columns / fields
  show_route: true,
  show_headsign: true,
  show_times: true,
  show_delay: true,
  show_license_plate: false,
  show_platform: true,
  show_occupancy: true,
  show_countdown: true,
  show_realtime_indicator: true,
  time_format: '24',
  // Alerts
  show_service_alerts: true,
  max_alerts: 3,
  alert_details: true,
  // Map
  show_map: false,
  map_zoom: 14,
  map_marker_type: 'icon',
  map_marker_icon: undefined,
  map_marker_name: undefined,
  // Footer
  show_footer_remaining: false,
  show_footer_filter: false,
  show_footer_updated: true,
};

const esc = (value) =>
  String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

const pad2 = (value) => String(value).padStart(2, '0');

function normaliseColor(value) {
  if (!value) return undefined;
  const text = String(value).trim();
  if (!text) return undefined;
  return /^[0-9a-f]{6}$/i.test(text) ? `#${text}` : text;
}

// ---------------------------------------------------------------------------
// Reading a departure board out of an entity, whichever integration wrote it
// ---------------------------------------------------------------------------

/** Parse "HH:MM" or "HH:MM:SS" into minutes past midnight, or null. */
function timeToMinutes(text) {
  if (!text) return null;
  const parts = String(text).split(':');
  if (parts.length < 2) return null;
  const hours = Number(parts[0]);
  const minutes = Number(parts[1]);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  return hours * 60 + minutes;
}

/** Minutes from now until a wall-clock time, tolerating a midnight rollover. */
function minutesUntilClockTime(text, now = new Date()) {
  const target = timeToMinutes(text);
  if (target === null) return null;
  const current = now.getHours() * 60 + now.getMinutes();
  let delta = target - current;
  // A time more than 12 hours in the past is really tomorrow's service.
  if (delta < -720) delta += 1440;
  if (delta > 720) delta -= 1440;
  return delta;
}

function statusFromDelay(delaySeconds, minutes) {
  if (minutes !== null && minutes <= 0) return 'departing';
  if (minutes !== null && minutes <= 1) return 'arriving';
  if (delaySeconds === undefined || delaySeconds === null) return 'scheduled';
  if (delaySeconds > 60) return 'late';
  if (delaySeconds < -60) return 'early';
  return 'on_time';
}

/**
 * Read the v0.3 compact `departures` array.
 *
 * The countdown is derived from the wall-clock departure time rather than the
 * minute count the integration stored, so it stays correct between polls and
 * cannot claim every service is leaving "now" if the state goes stale.
 */
function readModernBoard(attrs, ageMinutes) {
  return (attrs.departures || []).map((row) => {
    const live = minutesUntilClockTime(row[K.expected] || row[K.scheduled]);
    const stored = Number(row[K.minutes]);
    const minutes =
      live !== null ? live : Number.isFinite(stored) ? stored - ageMinutes : null;
    return {
      route: row[K.route] || '',
      routeId: row[K.routeId] || '',
      destination: row[K.destination] || '',
      scheduled: row[K.scheduled] || null,
      expected: row[K.expected] || row[K.scheduled] || null,
      delaySeconds: Number.isFinite(row[K.delay]) ? row[K.delay] : undefined,
      minutes,
      platform: row[K.platform] || null,
      status: row[K.status] || 'scheduled',
      realtime: row[K.realtime] === true,
      color: normaliseColor(row[K.color]),
      textColor: normaliseColor(row[K.textColor]),
      mode: row[K.mode] || attrs.transport_type || 'unknown',
      occupancy: Number.isFinite(row[K.occupancy]) ? row[K.occupancy] : null,
      vehicle: row[K.vehicle] || null,
      tripId: row[K.tripId] || null,
      boarding: row[K.boarding] !== false,
      reason: row[K.reason] || null,
      alternative: row[K.alternative] || null,
      alertEffect: row[K.alertEffect] || null,
      alertSeverity: row[K.alertSeverity] || null,
    };
  });
}

/** Read the flat `departure_N_*` attributes written by v0.2. */
function readLegacyBoard(attrs) {
  const rows = [];
  for (let index = 1; ; index += 1) {
    const prefix = `departure_${index}`;
    const scheduled = attrs[`${prefix}_scheduled_time`];
    const expected = attrs[`${prefix}_actual_time`];
    const destination = attrs[`${prefix}_headsign`];
    const route = attrs[`${prefix}_route`];
    if (!scheduled && !expected && !destination && !route) break;

    const delayRaw = attrs[`${prefix}_delay_in_seconds`];
    const delaySeconds = typeof delayRaw === 'number' ? delayRaw : undefined;
    const minutes = minutesUntilClockTime(expected || scheduled);
    rows.push({
      route: route || '',
      routeId: route || '',
      destination: destination || '',
      scheduled: scheduled || null,
      expected: expected || scheduled || null,
      delaySeconds,
      minutes,
      platform: null,
      status: statusFromDelay(delaySeconds, minutes),
      realtime: delaySeconds !== undefined,
      color: undefined,
      textColor: undefined,
      mode: attrs.transport_type || 'unknown',
      occupancy: null,
      vehicle: attrs[`${prefix}_license_plate`] || null,
      tripId: attrs[`${prefix}_trip_id`] || null,
      boarding: true,
      reason: null,
      alternative: null,
      alertEffect: null,
      alertSeverity: null,
    });
  }
  return rows;
}

/** Read the v0.3 `alerts` array, falling back to v0.2's `alert_N_*` keys. */
function readAlerts(attrs) {
  if (Array.isArray(attrs.alerts)) {
    return attrs.alerts.map((alert) => ({
      header: alert.header || '',
      description: alert.description || '',
      detail: alert.detail || '',
      cause: alert.cause || '',
      effect: alert.effect || '',
      severity: alert.severity || 'INFO',
      routes: Array.isArray(alert.routes) ? alert.routes : [],
    }));
  }

  const count = Number(attrs.service_alerts_count) || 0;
  const alerts = [];
  for (let index = 1; index <= count; index += 1) {
    const prefix = `alert_${index}`;
    if (!attrs[`${prefix}_header`] && !attrs[`${prefix}_description`]) continue;
    alerts.push({
      header: attrs[`${prefix}_header`] || '',
      description: attrs[`${prefix}_description`] || '',
      detail: attrs[`${prefix}_resolved_by`] || '',
      cause: attrs[`${prefix}_cause`] || '',
      effect: attrs[`${prefix}_effect`] || '',
      severity: attrs[`${prefix}_status`] || 'INFO',
      routes: String(attrs[`${prefix}_affected_routes`] || '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
    });
  }
  return alerts;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------
const CARD_STYLES = `
  :host { display: block; }
  ha-card { overflow: hidden; }
  .wrap { padding: 0 16px 12px; }
  .header {
    display: flex; align-items: center; gap: 10px;
    padding: 14px 0 6px;
  }
  .header img { object-fit: contain; flex: 0 0 auto; }
  .titles { flex: 1; min-width: 0; }
  .title {
    font-size: var(--ha-card-header-font-size, 20px);
    font-weight: 500; line-height: 1.2;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .subtitle {
    font-size: 12px; color: var(--secondary-text-color);
    margin-top: 2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .legacy-hint {
    display: flex; gap: 6px; align-items: center;
    margin: 4px 0 8px; padding: 6px 8px;
    font-size: 12px; border-radius: 6px;
    background: var(--secondary-background-color);
    color: var(--secondary-text-color);
  }

  /* Alerts */
  .alerts { display: flex; flex-direction: column; gap: 6px; margin: 4px 0 10px; }
  .alert {
    border-radius: 8px; overflow: hidden;
    border-left: 4px solid var(--warning-color, #ffa726);
    background: var(--secondary-background-color);
  }
  .alert.severe { border-left-color: var(--error-color, #db4437); }
  .alert summary {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 10px; cursor: pointer; list-style: none;
    font-size: 13px; font-weight: 500;
  }
  .alert summary::-webkit-details-marker { display: none; }
  .alert summary .grow {
    flex: 1; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .alert[open] summary .grow { white-space: normal; }
  .alert .body {
    padding: 0 10px 10px 10px; font-size: 12px; line-height: 1.45;
    color: var(--secondary-text-color); white-space: pre-line;
  }
  .alert .tag {
    font-size: 10px; font-weight: 600; letter-spacing: .4px;
    text-transform: uppercase; padding: 2px 6px; border-radius: 4px;
    background: var(--warning-color, #ffa726); color: #000; flex: 0 0 auto;
  }
  .alert.severe .tag { background: var(--error-color, #db4437); color: #fff; }

  /* Board */
  .board { display: flex; flex-direction: column; }
  .row {
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: center; gap: 10px;
    padding: 9px 0;
    border-top: 1px solid var(--divider-color);
  }
  .row:first-child { border-top: none; }
  .row.cancelled { opacity: .72; }
  .badge {
    min-width: 44px; max-width: 74px; padding: 4px 7px;
    border-radius: 6px; text-align: center;
    font-size: 13px; font-weight: 700; line-height: 1.15;
    background: var(--primary-color); color: var(--text-primary-color, #fff);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .mid { min-width: 0; }
  .dest {
    font-size: 14px; font-weight: 500;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .row.cancelled .dest { text-decoration: line-through; }
  .meta {
    display: flex; align-items: center; flex-wrap: wrap; gap: 4px 8px;
    margin-top: 3px; font-size: 12px; color: var(--secondary-text-color);
  }
  .meta .chip {
    display: inline-flex; align-items: center; gap: 3px;
    padding: 1px 5px; border-radius: 4px;
    background: var(--secondary-background-color);
  }
  .meta ha-icon { --mdc-icon-size: 13px; width: 13px; height: 13px; }
  .live {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--success-color, #43a047); flex: 0 0 auto;
    animation: at-pulse 2s ease-in-out infinite;
  }
  @keyframes at-pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
  .right { text-align: right; white-space: nowrap; }
  .count { font-size: 17px; font-weight: 600; line-height: 1.1; }
  .count .unit { font-size: 11px; font-weight: 400; opacity: .75; margin-left: 1px; }
  .times { font-size: 11px; color: var(--secondary-text-color); margin-top: 2px; }
  .times s { opacity: .7; }
  .on-time { color: var(--success-color, #43a047); }
  .late { color: var(--error-color, #db4437); }
  .early { color: var(--warning-color, #ffa726); }
  .cancelled-text { color: var(--error-color, #db4437); font-weight: 600; }
  .why {
    grid-column: 1 / -1; margin: 4px 0 0;
    font-size: 12px; line-height: 1.4;
    display: flex; gap: 6px; align-items: flex-start;
    color: var(--error-color, #db4437);
  }
  .why ha-icon { --mdc-icon-size: 15px; width: 15px; height: 15px; flex: 0 0 auto; }
  .why .alt { color: var(--secondary-text-color); display: block; margin-top: 2px; }

  /* Table layout (v0.2 look) */
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; padding: 10px 8px; font-weight: 600; }
  td { padding: 8px; }
  tbody tr { border-top: 1px solid var(--divider-color); }
  tr.cancelled td { text-decoration: line-through; opacity: .72; }

  /* Map */
  .map {
    margin: 4px 0 10px; border-radius: 8px; overflow: hidden;
    border: 1px solid var(--divider-color);
  }
  .map .info {
    padding: 7px 10px; font-size: 12px;
    background: var(--secondary-background-color);
    color: var(--secondary-text-color);
  }
  .map .info.warn { color: var(--warning-color, #ffa726); }

  .empty { padding: 16px 0; color: var(--secondary-text-color); }
  .footer {
    display: flex; flex-wrap: wrap; gap: 4px 12px;
    justify-content: space-between;
    margin-top: 10px; padding-top: 8px;
    border-top: 1px solid var(--divider-color);
    font-size: 11px; color: var(--secondary-text-color);
  }
`;

// ---------------------------------------------------------------------------
// The card
// ---------------------------------------------------------------------------
class AucklandTransportCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = undefined;
    this._hass = undefined;
    this._built = false;
    this._tick = undefined;
    this._mapCard = undefined;
    this._mapPending = false;
    this._mapKey = undefined;
  }

  static getConfigElement() {
    return document.createElement('auckland-transport-card-editor');
  }

  static getStubConfig(hass, entities) {
    const isBoard = (id) =>
      id.startsWith('sensor.auckland_transport') &&
      !/_vehicle_location$|_departure_\d+$|_alerts$|_service_alerts$|_minutes|_departure_count$|_departures$|_disruption$/.test(
        id,
      );
    let entity = (entities || []).find(isBoard);
    if (!entity && hass) entity = Object.keys(hass.states).find(isBoard);
    return { entity: entity || '', show_service_alerts: true };
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error('An "entity" is required');
    this._config = { ...DEFAULTS, ...config };
    // v0.2 used max_alerts implicitly; keep numbers sane.
    this._config.max_alerts = Math.max(0, Number(this._config.max_alerts) || 0);
    this._mapKey = undefined;
    this._built = false;
    if (this.shadowRoot) this.shadowRoot.innerHTML = '';
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    // Keep the countdown honest between coordinator updates.
    this._tick = window.setInterval(() => this._render(), 20000);
    this._render();
  }

  disconnectedCallback() {
    if (this._tick) window.clearInterval(this._tick);
    this._tick = undefined;
  }

  getCardSize() {
    const rows = this._board ? this._board.departures.length : 3;
    return Math.min(2 + rows, 12);
  }

  // -- data ---------------------------------------------------------------
  _stateObj() {
    if (!this._hass || !this._config) return undefined;
    return this._hass.states[this._config.entity];
  }

  /** Find the vehicle-location companion of the configured board entity. */
  _vehicleState() {
    const state = this._stateObj();
    if (!state || !this._hass) return undefined;
    const direct = this._hass.states[`${this._config.entity}_vehicle_location`];
    if (direct) return direct;
    // Fall back to matching on the device, which covers renamed entities.
    const deviceId = state.attributes.device_id;
    if (!deviceId) return undefined;
    return Object.values(this._hass.states).find(
      (candidate) =>
        candidate.entity_id.endsWith('_vehicle_location') &&
        candidate.attributes.stop_code === state.attributes.stop_code,
    );
  }

  _readBoard() {
    const state = this._stateObj();
    if (!state) return undefined;
    const attrs = state.attributes || {};
    const modern = Number(attrs.schema_version) >= 2 && Array.isArray(attrs.departures);

    // Compensate for the time since the integration last refreshed so the
    // countdown stays accurate between polls.
    const updated = state.last_updated ? new Date(state.last_updated) : new Date();
    const ageMinutes = Math.max(0, Math.floor((Date.now() - updated.getTime()) / 60000));

    let departures = modern ? readModernBoard(attrs, ageMinutes) : readLegacyBoard(attrs);
    departures = this._applyFilter(departures);

    return {
      modern,
      attrs,
      state,
      stopName: attrs.stop_name || '',
      stopCode: attrs.stop_code || '',
      mode: attrs.transport_type || 'unknown',
      alerts: readAlerts(attrs),
      departures,
      totalMatching: departures.length,
      remaining: Number.isFinite(attrs.remaining_departures_for_today)
        ? attrs.remaining_departures_for_today
        : null,
      updated,
      ageMinutes,
      // Well past any sane polling interval, so warn instead of showing the
      // numbers as if they were live.
      stale: ageMinutes >= 10,
      realtimeAvailable: attrs.realtime_available !== false,
    };
  }

  _applyFilter(rows) {
    const raw = (this._config.filter || '').toString().trim();
    if (!raw) return rows;

    const filters = raw
      .split(';')
      .map((item) => item.trim())
      .filter(Boolean)
      .map((pattern) => {
        let include = true;
        let body = pattern;
        if (body.startsWith('!')) {
          include = false;
          body = body.slice(1);
        }
        let test;
        if (body.startsWith('/') && body.lastIndexOf('/') > 0) {
          const last = body.lastIndexOf('/');
          try {
            const re = new RegExp(body.slice(1, last), body.slice(last + 1) || 'i');
            test = (value) => re.test(value || '');
          } catch (err) {
            const needle = body.toLowerCase();
            test = (value) => (value || '').toLowerCase().includes(needle);
          }
        } else {
          const needle = body.toLowerCase();
          test = (value) => (value || '').toLowerCase().includes(needle);
        }
        return { include, test };
      });

    const includes = filters.filter((item) => item.include);
    const excludes = filters.filter((item) => !item.include);
    return rows.filter((row) => {
      const fields = [row.route, row.routeId, row.destination];
      const included =
        includes.length === 0 || includes.some((f) => fields.some((v) => f.test(v)));
      const excluded = excludes.some((f) => fields.some((v) => f.test(v)));
      return included && !excluded;
    });
  }

  _limit(rows) {
    const max = Number(this._config.max_rows);
    return max > 0 ? rows.slice(0, max) : rows;
  }

  // -- formatting ---------------------------------------------------------
  _formatTime(text) {
    if (!text) return '—';
    const total = timeToMinutes(text);
    if (total === null) return String(text);
    const hours = Math.floor(total / 60) % 24;
    const minutes = total % 60;
    if (this._config.time_format === '12') {
      const suffix = hours >= 12 ? 'PM' : 'AM';
      const display = hours % 12 === 0 ? 12 : hours % 12;
      return `${display}:${pad2(minutes)} ${suffix}`;
    }
    return `${pad2(hours)}:${pad2(minutes)}`;
  }

  _formatDelay(seconds) {
    if (seconds === undefined || seconds === null || !Number.isFinite(seconds)) return '';
    if (Math.abs(seconds) < 60) return 'On time';
    const minutes = Math.round(Math.abs(seconds) / 60);
    return `${seconds > 0 ? '+' : '-'}${minutes} min`;
  }

  _formatCountdown(minutes) {
    if (minutes === null || !Number.isFinite(minutes)) return { value: '—', unit: '' };
    if (minutes <= 0) return { value: 'Now', unit: '' };
    if (minutes < 60) return { value: String(minutes), unit: 'min' };
    const hours = Math.floor(minutes / 60);
    return { value: `${hours}h ${minutes % 60}`, unit: 'min' };
  }

  _delayClass(row) {
    if (row.status === 'cancelled' || row.status === 'skipped') return 'cancelled-text';
    if (row.status === 'late') return 'late';
    if (row.status === 'early') return 'early';
    if (row.status === 'scheduled') return '';
    return 'on-time';
  }

  // -- rendering ----------------------------------------------------------
  _build() {
    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLES}</style>
      <ha-card>
        <div class="wrap">
          <div class="header" id="header"></div>
          <div id="hint"></div>
          <div id="alerts"></div>
          <div id="map"></div>
          <div id="board"></div>
          <div id="footer"></div>
        </div>
      </ha-card>
    `;
    this._built = true;
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._built) this._build();

    const root = this.shadowRoot;
    const board = this._readBoard();
    this._board = board;

    if (!board) {
      root.getElementById('header').innerHTML = this._headerHtml(undefined);
      root.getElementById('hint').innerHTML = '';
      root.getElementById('alerts').innerHTML = '';
      root.getElementById('board').innerHTML =
        `<div class="empty">Entity <code>${esc(this._config.entity)}</code> not found.</div>`;
      root.getElementById('footer').innerHTML = '';
      return;
    }

    root.getElementById('header').innerHTML = this._headerHtml(board);
    root.getElementById('hint').innerHTML = this._hintHtml(board);
    root.getElementById('alerts').innerHTML = this._alertsHtml(board);
    root.getElementById('board').innerHTML =
      this._config.layout === 'table' ? this._tableHtml(board) : this._rowsHtml(board);
    root.getElementById('footer').innerHTML = this._footerHtml(board);
    this._syncMap(board);
  }

  _headerHtml(board) {
    const cfg = this._config;
    const stopName = board ? board.stopName : '';
    const stopCode = board ? board.stopCode : '';
    const title = cfg.title ?? (stopName || 'Auckland Transport');

    const parts = [];
    if (cfg.header_logo) {
      const size = Number(cfg.header_logo_size) || 40;
      parts.push(
        `<img src="https://raw.githubusercontent.com/SeitzDaniel/brands/master/custom_integrations/auckland_transport/icon.png"
              alt="Auckland Transport" width="${size}" height="${size}" style="width:${size}px;height:${size}px">`,
      );
    }

    const subtitleBits = [];
    if (stopCode) subtitleBits.push(`Stop ${esc(stopCode)}`);
    if (board && board.departures.length) {
      subtitleBits.push(
        `${board.departures.length} departure${board.departures.length === 1 ? '' : 's'}`,
      );
    }
    if (board && !board.realtimeAvailable) subtitleBits.push('timetable only');
    if (board && board.stale) subtitleBits.push(`not updated for ${board.ageMinutes} min`);

    parts.push(`
      <div class="titles">
        <div class="title">${esc(title)}</div>
        ${subtitleBits.length ? `<div class="subtitle">${subtitleBits.join(' · ')}</div>` : ''}
      </div>
    `);

    if (cfg.header_icon_show) {
      const icon = cfg.header_icon || MODE_ICONS[board ? board.mode : 'unknown'] || MODE_ICONS.unknown;
      const size = Number(cfg.header_icon_size) || 28;
      const color = cfg.header_icon_color || 'var(--primary-text-color)';
      parts.push(
        `<ha-icon icon="${esc(icon)}" style="--mdc-icon-size:${size}px;width:${size}px;height:${size}px;color:${esc(color)};flex:0 0 auto"></ha-icon>`,
      );
    }
    return parts.join('');
  }

  _hintHtml(board) {
    if (board.modern || board.departures.length === 0) return '';
    return `
      <div class="legacy-hint">
        <ha-icon icon="mdi:information-outline"></ha-icon>
        <span>Reading the older attribute format. Update the Auckland Transport
        integration to 0.3 or newer for platforms, live occupancy and cancellation
        details.</span>
      </div>
    `;
  }

  _alertsHtml(board) {
    if (!this._config.show_service_alerts || !board.alerts.length) return '';
    const limit = this._config.max_alerts || board.alerts.length;
    const items = board.alerts.slice(0, limit).map((alert) => {
      const severe = String(alert.severity).toUpperCase() === 'SEVERE';
      const icon = EFFECT_ICONS[alert.effect] || 'mdi:alert-outline';
      const tag = EFFECT_LABELS[alert.effect] || alert.effect || 'Notice';
      const body = [];
      if (this._config.alert_details) {
        if (alert.detail) body.push(alert.detail);
        if (alert.description) body.push(alert.description);
        if (alert.routes.length) body.push(`Routes: ${alert.routes.join(', ')}`);
      }
      return `
        <details class="alert${severe ? ' severe' : ''}">
          <summary>
            <ha-icon icon="${esc(icon)}"></ha-icon>
            <span class="grow">${esc(alert.header || tag)}</span>
            <span class="tag">${esc(tag)}</span>
          </summary>
          ${body.length ? `<div class="body">${esc(body.join('\n\n'))}</div>` : ''}
        </details>
      `;
    });
    return `<div class="alerts">${items.join('')}</div>`;
  }

  _rowsHtml(board) {
    const rows = this._limit(board.departures);
    if (!rows.length) return this._emptyHtml(board);
    const cfg = this._config;

    return `<div class="board">${rows
      .map((row) => {
        const disrupted = row.status === 'cancelled' || row.status === 'skipped';
        const badgeStyle = row.color
          ? `background:${esc(row.color)};color:${esc(row.textColor || '#fff')}`
          : '';
        const countdown = this._formatCountdown(row.minutes);

        const meta = [];
        if (cfg.show_realtime_indicator && row.realtime) {
          meta.push('<span class="live" title="Live"></span>');
        }
        if (cfg.show_platform && row.platform) {
          meta.push(
            `<span class="chip"><ha-icon icon="mdi:sign-direction"></ha-icon>${esc(
              /^\d+$/.test(row.platform) ? `Plat ${row.platform}` : row.platform,
            )}</span>`,
          );
        }
        if (cfg.show_occupancy && row.occupancy !== null && OCCUPANCY[row.occupancy]) {
          const occ = OCCUPANCY[row.occupancy];
          meta.push(
            `<span class="chip" title="${esc(occ.label)}"><ha-icon icon="${esc(
              occ.icon,
            )}"></ha-icon>${esc(occ.label)}</span>`,
          );
        }
        if (!row.boarding) {
          meta.push('<span class="chip" title="Terminates here">Arrival only</span>');
        }
        if (row.alertEffect && !disrupted) {
          meta.push(
            `<span class="chip"><ha-icon icon="${esc(
              EFFECT_ICONS[row.alertEffect] || 'mdi:alert-outline',
            )}"></ha-icon>${esc(EFFECT_LABELS[row.alertEffect] || row.alertEffect)}</span>`,
          );
        }
        if (cfg.show_license_plate && row.vehicle) {
          // A number plate on buses and ferries, a set number on trains.
          meta.push(
            `<span class="chip" title="Vehicle"><ha-icon icon="${
              row.mode === 'train' ? 'mdi:train-variant' : 'mdi:card-text-outline'
            }"></ha-icon>${esc(row.vehicle)}</span>`,
          );
        }

        const times = [];
        if (cfg.show_times) {
          const changed = row.scheduled && row.expected && row.scheduled !== row.expected;
          times.push(
            changed
              ? `<s>${esc(this._formatTime(row.scheduled))}</s> ${esc(
                  this._formatTime(row.expected),
                )}`
              : esc(this._formatTime(row.expected || row.scheduled)),
          );
        }
        if (cfg.show_delay) {
          const label = disrupted
            ? STATUS_LABELS[row.status]
            : this._formatDelay(row.delaySeconds) || STATUS_LABELS[row.status] || '';
          if (label) times.push(`<span class="${this._delayClass(row)}">${esc(label)}</span>`);
        }

        const why = [];
        if (disrupted && (row.reason || row.alternative)) {
          why.push(`
            <div class="why">
              <ha-icon icon="mdi:alert-circle-outline"></ha-icon>
              <span>${esc(row.reason || STATUS_LABELS[row.status])}
                ${row.alternative ? `<span class="alt">↳ ${esc(row.alternative)}</span>` : ''}
              </span>
            </div>
          `);
        }

        return `
          <div class="row${disrupted ? ' cancelled' : ''}">
            ${
              cfg.show_route
                ? `<div class="badge" style="${badgeStyle}" title="${esc(row.routeId || row.route)}">${esc(
                    row.route || '—',
                  )}</div>`
                : '<div></div>'
            }
            <div class="mid">
              ${cfg.show_headsign ? `<div class="dest">${esc(row.destination || '—')}</div>` : ''}
              ${meta.length ? `<div class="meta">${meta.join('')}</div>` : ''}
            </div>
            <div class="right">
              ${
                cfg.show_countdown
                  ? `<div class="count ${this._delayClass(row)}">${esc(countdown.value)}${
                      countdown.unit ? `<span class="unit">${esc(countdown.unit)}</span>` : ''
                    }</div>`
                  : ''
              }
              ${times.length ? `<div class="times">${times.join(' · ')}</div>` : ''}
            </div>
            ${why.join('')}
          </div>
        `;
      })
      .join('')}</div>`;
  }

  _tableHtml(board) {
    const rows = this._limit(board.departures);
    if (!rows.length) return this._emptyHtml(board);
    const cfg = this._config;

    const headers = [];
    if (cfg.show_route) headers.push('Route');
    if (cfg.show_headsign) headers.push('Destination');
    if (cfg.show_platform) headers.push('Plat');
    if (cfg.show_times) headers.push('Scheduled', 'Expected');
    if (cfg.show_countdown) headers.push('In');
    if (cfg.show_delay) headers.push('Delay');
    if (cfg.show_license_plate) headers.push('Vehicle');

    const body = rows
      .map((row) => {
        const disrupted = row.status === 'cancelled' || row.status === 'skipped';
        const cells = [];
        if (cfg.show_route) cells.push(esc(row.route || '—'));
        if (cfg.show_headsign) cells.push(esc(row.destination || '—'));
        if (cfg.show_platform) cells.push(esc(row.platform || '—'));
        if (cfg.show_times) {
          cells.push(esc(this._formatTime(row.scheduled)));
          cells.push(esc(this._formatTime(row.expected)));
        }
        if (cfg.show_countdown) {
          const countdown = this._formatCountdown(row.minutes);
          cells.push(esc(`${countdown.value}${countdown.unit ? ` ${countdown.unit}` : ''}`));
        }
        if (cfg.show_delay) {
          const label = disrupted
            ? STATUS_LABELS[row.status]
            : this._formatDelay(row.delaySeconds) || STATUS_LABELS[row.status] || '';
          cells.push(`<span class="${this._delayClass(row)}">${esc(label)}</span>`);
        }
        if (cfg.show_license_plate) cells.push(esc(row.vehicle || '—'));
        return `<tr class="${disrupted ? 'cancelled' : ''}">${cells
          .map((cell) => `<td>${cell}</td>`)
          .join('')}</tr>`;
      })
      .join('');

    return `<table>
      <thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  }

  _emptyHtml(board) {
    const filtered = this._config.filter && board.remaining;
    return `<div class="empty">${
      filtered
        ? 'No departures match the current filter.'
        : 'No upcoming departures.'
    }</div>`;
  }

  _footerHtml(board) {
    const cfg = this._config;
    const left = [];
    const right = [];

    if (cfg.show_footer_remaining) {
      const count = cfg.filter ? board.totalMatching : board.remaining ?? board.totalMatching;
      left.push(`${count} remaining today`);
    }
    if (cfg.show_footer_filter && cfg.filter) left.push(`Filter: ${esc(cfg.filter)}`);
    if (cfg.show_footer_updated) {
      const seconds = Math.round((Date.now() - board.updated.getTime()) / 1000);
      right.push(
        seconds < 90 ? `Updated ${seconds}s ago` : `Updated ${Math.round(seconds / 60)}m ago`,
      );
    }

    if (!left.length && !right.length) return '';
    return `<div class="footer"><div>${left.join(' · ')}</div><div>${right.join(
      ' · ',
    )}</div></div>`;
  }

  // -- map ----------------------------------------------------------------
  /**
   * Keep the map card alive across renders. Recreating it on every coordinator
   * update makes the tiles flicker and resets the user's pan and zoom.
   */
  _syncMap(board) {
    const host = this.shadowRoot.getElementById('map');
    if (!this._config.show_map) {
      if (this._mapKey !== undefined) {
        host.innerHTML = '';
        this._mapCard = undefined;
        this._mapPending = false;
        this._mapKey = undefined;
      }
      return;
    }

    const vehicle = this._vehicleState();
    const first = this._limit(board.departures)[0];
    const tracking =
      vehicle &&
      vehicle.attributes.latitude !== undefined &&
      vehicle.attributes.longitude !== undefined;
    const sameTrip = tracking && first && vehicle.attributes.trip_id === first.tripId;

    let entityId;
    let info;
    let warn = false;
    if (tracking && (sameTrip || !first)) {
      entityId = vehicle.entity_id;
      const bits = [];
      if (vehicle.attributes.route) bits.push(`Route ${vehicle.attributes.route}`);
      if (vehicle.attributes.headsign) bits.push(vehicle.attributes.headsign);
      if (vehicle.attributes.license_plate) bits.push(vehicle.attributes.license_plate);
      if (vehicle.attributes.occupancy) bits.push(String(vehicle.attributes.occupancy).replace(/_/g, ' '));
      info = bits.length ? `Tracking ${bits.join(' · ')}` : 'Tracking vehicle';
    } else if (tracking) {
      entityId = vehicle.entity_id;
      info = `Live vehicle is on a different trip than ${first.route} to ${first.destination}`;
      warn = true;
    } else {
      entityId = this._config.entity;
      info = first
        ? `No live vehicle for ${first.route} to ${first.destination} yet — showing the stop`
        : 'Showing the stop';
      warn = true;
    }

    const entityConfig =
      this._config.map_marker_type === 'name'
        ? {
            entity: entityId,
            label_mode: 'name',
            ...(this._config.map_marker_name ? { name: this._config.map_marker_name } : {}),
          }
        : { entity: entityId, label_mode: 'icon' };

    const mapConfig = {
      type: 'map',
      entities: [entityConfig],
      default_zoom: Number(this._config.map_zoom) || 14,
      aspect_ratio: '16:9',
      theme_mode: 'auto',
    };

    // Optional icon override, applied to a shallow copy of hass so the real
    // entity registry is untouched.
    let hass = this._hass;
    if (this._config.map_marker_icon && this._config.map_marker_type !== 'name') {
      const existing = hass.states[entityId];
      if (existing) {
        hass = {
          ...hass,
          states: {
            ...hass.states,
            [entityId]: {
              ...existing,
              attributes: { ...existing.attributes, icon: this._config.map_marker_icon },
            },
          },
        };
      }
    }

    const key = JSON.stringify(mapConfig);
    if (this._mapKey !== key) {
      this._mapKey = key;
      this._mapCard = undefined;
      this._mapPending = false;
      host.innerHTML = `<div class="map"><div class="slot"></div><div class="info${
        warn ? ' warn' : ''
      }">${esc(info)}</div></div>`;
    }

    if (this._mapCard) {
      this._mapCard.hass = hass;
    } else if (!this._mapPending) {
      // Guard against starting a second creation while the first is in flight.
      this._mapPending = true;
      this._createMapCard(mapConfig, hass, host);
    }

    const infoEl = host.querySelector('.info');
    if (infoEl) {
      infoEl.textContent = info;
      infoEl.className = `info${warn ? ' warn' : ''}`;
    }
  }

  /**
   * Build the map card via loadCardHelpers.
   *
   * `customElements.whenDefined('hui-map-card')` never resolves unless something
   * else on the dashboard has already pulled the map card in, so the helper is
   * used to force it to load.
   */
  async _createMapCard(mapConfig, hass, host) {
    const slot = host.querySelector('.slot');
    try {
      let card;
      if (window.loadCardHelpers) {
        const helpers = await window.loadCardHelpers();
        card = helpers.createCardElement(mapConfig);
      } else {
        await customElements.whenDefined('hui-map-card');
        card = document.createElement('hui-map-card');
        card.setConfig(mapConfig);
      }
      card.hass = hass;
      this._mapCard = card;
      if (slot) slot.appendChild(card);
    } catch (err) {
      if (slot) slot.innerHTML = '<div class="info warn">Map card unavailable.</div>';
    } finally {
      this._mapPending = false;
    }
  }
}

customElements.define('auckland-transport-card', AucklandTransportCard);

// ---------------------------------------------------------------------------
// Visual editor
// ---------------------------------------------------------------------------
const EDITOR_SCHEMA = [
  {
    name: '',
    type: 'expandable',
    title: 'Entity',
    expanded: true,
    schema: [
      {
        name: 'entity',
        required: true,
        selector: { entity: { domain: 'sensor', integration: 'auckland_transport' } },
      },
      { name: 'title', selector: { text: {} } },
      {
        name: 'layout',
        selector: {
          select: {
            mode: 'dropdown',
            options: [
              { value: 'rows', label: 'Departure board (recommended)' },
              { value: 'table', label: 'Table (classic)' },
            ],
          },
        },
      },
      { name: 'max_rows', selector: { number: { min: 1, max: 40, mode: 'box' } } },
      { name: 'filter', selector: { text: {} } },
    ],
  },
  {
    name: '',
    type: 'expandable',
    title: 'Departure details',
    schema: [
      { name: 'show_route', selector: { boolean: {} } },
      { name: 'show_headsign', selector: { boolean: {} } },
      { name: 'show_platform', selector: { boolean: {} } },
      { name: 'show_countdown', selector: { boolean: {} } },
      { name: 'show_times', selector: { boolean: {} } },
      { name: 'show_delay', selector: { boolean: {} } },
      { name: 'show_occupancy', selector: { boolean: {} } },
      { name: 'show_realtime_indicator', selector: { boolean: {} } },
      { name: 'show_license_plate', selector: { boolean: {} } },
      {
        name: 'time_format',
        selector: {
          select: {
            mode: 'dropdown',
            options: [
              { value: '24', label: '24 hour' },
              { value: '12', label: '12 hour' },
            ],
          },
        },
      },
    ],
  },
  {
    name: '',
    type: 'expandable',
    title: 'Service alerts',
    schema: [
      { name: 'show_service_alerts', selector: { boolean: {} } },
      { name: 'alert_details', selector: { boolean: {} } },
      { name: 'max_alerts', selector: { number: { min: 0, max: 10, mode: 'box' } } },
    ],
  },
  {
    name: '',
    type: 'expandable',
    title: 'Header',
    schema: [
      { name: 'header_logo', selector: { boolean: {} } },
      { name: 'header_logo_size', selector: { number: { min: 16, max: 96, mode: 'box' } } },
      { name: 'header_icon_show', selector: { boolean: {} } },
      { name: 'header_icon', selector: { icon: {} } },
      { name: 'header_icon_size', selector: { number: { min: 12, max: 72, mode: 'box' } } },
      { name: 'header_icon_color', selector: { text: {} } },
    ],
  },
  {
    name: '',
    type: 'expandable',
    title: 'Map',
    schema: [
      { name: 'show_map', selector: { boolean: {} } },
      { name: 'map_zoom', selector: { number: { min: 1, max: 20, mode: 'slider' } } },
      {
        name: 'map_marker_type',
        selector: {
          select: {
            mode: 'dropdown',
            options: [
              { value: 'icon', label: 'Icon' },
              { value: 'name', label: 'Name' },
            ],
          },
        },
      },
      { name: 'map_marker_icon', selector: { icon: {} } },
      { name: 'map_marker_name', selector: { text: {} } },
    ],
  },
  {
    name: '',
    type: 'expandable',
    title: 'Footer',
    schema: [
      { name: 'show_footer_updated', selector: { boolean: {} } },
      { name: 'show_footer_remaining', selector: { boolean: {} } },
      { name: 'show_footer_filter', selector: { boolean: {} } },
    ],
  },
];

const EDITOR_LABELS = {
  entity: 'Stop sensor',
  title: 'Title (defaults to the stop name)',
  layout: 'Layout',
  max_rows: 'Maximum departures shown',
  filter: 'Filter by route or destination',
  show_route: 'Route badge',
  show_headsign: 'Destination',
  show_platform: 'Platform / bay',
  show_countdown: 'Countdown',
  show_times: 'Scheduled and expected times',
  show_delay: 'Delay',
  show_occupancy: 'How busy the vehicle is',
  show_realtime_indicator: 'Live tracking dot',
  show_license_plate: 'Vehicle number (plate, or train set number)',
  time_format: 'Time format',
  show_service_alerts: 'Show service alerts',
  alert_details: 'Include the full alert text',
  max_alerts: 'Maximum alerts shown',
  header_logo: 'Show the AT logo',
  header_logo_size: 'Logo size (px)',
  header_icon_show: 'Show a mode icon',
  header_icon: 'Icon override',
  header_icon_size: 'Icon size (px)',
  header_icon_color: 'Icon colour',
  show_map: 'Show the vehicle on a map',
  map_zoom: 'Map zoom',
  map_marker_type: 'Marker style',
  map_marker_icon: 'Marker icon',
  map_marker_name: 'Marker label',
  show_footer_updated: 'Last updated',
  show_footer_remaining: 'Remaining departures today',
  show_footer_filter: 'Active filter',
};

const EDITOR_HELPERS = {
  filter:
    'Semicolon separated. "70; 30" keeps only those routes, "!Britomart" hides a destination, "/^NX/" is a regular expression.',
  max_rows: 'Leave empty to show everything the integration provides.',
  header_icon_color: 'Any CSS colour, for example var(--primary-color).',
};

class AucklandTransportCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._form = undefined;
  }

  setConfig(config) {
    this._config = { ...config };
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  _update() {
    if (!this._form) {
      this._form = document.createElement('ha-form');
      this._form.computeLabel = (schema) =>
        EDITOR_LABELS[schema.name] || schema.title || schema.name;
      this._form.computeHelper = (schema) => EDITOR_HELPERS[schema.name] || '';
      this._form.addEventListener('value-changed', (event) => {
        event.stopPropagation();
        const next = { ...event.detail.value };
        // Drop empty strings so the card falls back to its defaults.
        Object.keys(next).forEach((key) => {
          if (next[key] === '' || next[key] === undefined) delete next[key];
        });
        this._config = next;
        this.dispatchEvent(
          new CustomEvent('config-changed', {
            detail: { config: next },
            bubbles: true,
            composed: true,
          }),
        );
      });
      this.shadowRoot.appendChild(this._form);
    }

    this._form.hass = this._hass;
    this._form.schema = EDITOR_SCHEMA;
    this._form.data = { ...DEFAULTS, ...this._config };
  }
}

customElements.define('auckland-transport-card-editor', AucklandTransportCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'auckland-transport-card',
  name: 'Auckland Transport',
  description:
    'Departure board for the Auckland Transport integration, with platforms, live delays, occupancy and service alerts.',
  preview: true,
  documentationURL: 'https://github.com/SeitzDaniel/auckland_transport',
});

console.info(
  `%c Auckland Transport Card %c ${CARD_VERSION} `,
  'background: #0d47a1; color: #fff; border-radius: 3px 0 0 3px; padding: 2px 4px;',
  'background: #1976d2; color: #fff; border-radius: 0 3px 3px 0; padding: 2px 4px;',
);
