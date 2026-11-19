function failures = check_message_versions()
%CHECK_MESSAGE_VERSIONS Run the plotters and helpers on synthetic messages in their old and new pprzlink form.
%   failures = check_message_versions()   in MATLAB or Octave (octave-cli --eval check_message_versions)
%   Logs from every pprzlink version are analysed side by side, so a plot must work whether a message is in its
%   old form, its new form or missing. Drawing functions are replaced by stand-ins (tests/stubs) that check what
%   is passed and draw nothing, so this runs without a display. Add the old and new form here when a message changes.
    here = fileparts(mfilename('fullpath'));
    root = fileparts(here);
    added = {root, fullfile(root, 'plotters'), fullfile(here, 'stubs')};
    addpath(added{:});  % stubs last added, so first on the path
    cleanup = onCleanup(@() rmpath(added{:}));
    global PPRZ_TEST_LINES %#ok<GVMIS>
    failures = 0;
    n = 50;
    t = (0:n - 1)' * 0.1;

    % ESC: one message per motor (ESC) and one message with arrays per actuator (ESC_TELEMETRY)
    old.ESC = struct('t', repelem(t, 2), 'motor_id', repmat([0; 1], n, 1), 'rpm', 3000 + rand(2 * n, 1), ...
        'amps', rand(2 * n, 1), 'bat_volts', 16 * ones(2 * n, 1), 'motor_volts', 15 * ones(2 * n, 1), ...
        'temperature', 40 * ones(2 * n, 1), 'temperature_dev', 35 * ones(2 * n, 1), 'power', 10 * ones(2 * n, 1), ...
        'energy', zeros(2 * n, 1), 'node_id', zeros(2 * n, 1));
    new.ESC_TELEMETRY = struct('t', t, 'idx', repmat([0 1 2], n, 1), 'age', [zeros(n, 2), 2.55 * ones(n, 1)], ...
        'rpm', 4000 + rand(n, 3), 'current', rand(n, 3), 'voltage', 16 * ones(n, 3), 'temperature', 40 * ones(n, 3), ...
        'motor_temperature', 60 * ones(n, 3), 'output', 50 * ones(n, 3), 'errors', zeros(n, 3));
    failures = failures + check('pprz.esc', @() esc_fields(old, new, n));
    failures = failures + check('plot_esc ESC', @() plot_esc(old), 14);
    failures = failures + check('plot_esc ESC_TELEMETRY', @() plot_esc(new), 12);  % motor 2 stale: left out
    gyro = struct('t', t, 'gp', randn(n, 1), 'gq', randn(n, 1), 'gr', randn(n, 1));
    d = old; d.IMU_GYRO = gyro;
    failures = failures + check('pprz_effectiveness ESC', @() pprz_effectiveness(struct('data', d, 'title', 'x'), ...
        'input', 'rpm', 'plot', false));
    d = new; d.IMU_GYRO = gyro;
    failures = failures + check('pprz_effectiveness ESC_TELEMETRY', @() pprz_effectiveness(struct('data', d, ...
        'title', 'x'), 'input', 'rpm', 'plot', false));
    m = 2000;
    imu = struct('t', (0:m - 1)' / 500, 'ax', randn(m, 1), 'ay', randn(m, 1), 'az', randn(m, 1));
    for src = {old, new}
        d = src{1};
        d.IMU_ACCEL = imu;
        names = fieldnames(src{1});
        failures = failures + check(['plot_vibration ' names{1}], @() plot_vibration(struct('data', d, 'title', 'x')), ...
            [], '1× rotor');
    end

    % SERVO_TELEMETRY (newer logs only)
    sv.SERVO_TELEMETRY = struct('t', t, 'idx', repmat([4 5], n, 1), 'age', zeros(n, 2), 'position', 0.1 * ones(n, 2), ...
        'speed', zeros(n, 2), 'force', ones(n, 2), 'current', ones(n, 2), 'voltage', 7.4 * ones(n, 2), ...
        'temperature', 30 * ones(n, 2), 'errors', zeros(n, 2));
    failures = failures + check('plot_servo', @() plot_servo(sv), 10);

    % INS_EKF2_EXT: vibration metrics (older) or EKF2 status (newer)
    ek.INS_EKF2 = struct('t', t, 'innov_mag', rand(n, 1), 'innov_vel', rand(n, 1));
    ekold = ek;
    ekold.INS_EKF2_EXT = struct('t', t, 'gps_drift_h', rand(n, 1), 'gps_drift_v', rand(n, 1), ...
        'gps_drift_h_filt', rand(n, 1), 'gps_drift_blocked', zeros(n, 1), 'gyro_vibe_coning', rand(n, 1), ...
        'gyro_vibe_hf', rand(n, 1), 'accel_vibe_hf', rand(n, 1));
    eknew = ek;
    eknew.INS_EKF2_EXT = struct('t', t, 'gps_drift_h', rand(n, 1), 'gps_drift_v', rand(n, 1), ...
        'gps_drift_h_filt', rand(n, 1), 'gps_drift_blocked', zeros(n, 1), 'control_mode_upper', zeros(n, 1), ...
        'pos_acc_h', rand(n, 1), 'pos_acc_v', rand(n, 1), 'vel_acc_h', rand(n, 1), 'vel_acc_v', rand(n, 1), ...
        'hagl', NaN(n, 1), 'baro_bias', rand(n, 1), 'gnss_hgt_bias', rand(n, 1), 'yaw_gsf', rand(n, 1), ...
        'yaw_gsf_std', 0.1 * ones(n, 1), 'mag_inclination', 67 * ones(n, 1), 'mag_inclination_ref', 67.2 * ones(n, 1), ...
        'mag_strength', 0.49 * ones(n, 1), 'mag_strength_ref', 0.5 * ones(n, 1));
    failures = failures + check('plot_ekf2 without INS_EKF2_EXT', @() plot_ekf2(ek), 2);
    failures = failures + check('plot_ekf2 older INS_EKF2_EXT', @() plot_ekf2(ekold), 8);
    failures = failures + check('plot_ekf2 newer INS_EKF2_EXT', @() plot_ekf2(eknew), 18);

    % DATALINK_REPORT without and with uplink errors; LINK_STATS (newer logs only)
    dl.DATALINK_REPORT = struct('t', t, 'uplink_lost_time', zeros(n, 1), 'uplink_nb_msgs', (1:n)', ...
        'downlink_nb_msgs', (1:n)', 'downlink_rate', 1000 * ones(n, 1), 'uplink_rate', 5 * ones(n, 1), ...
        'downlink_ovrn', zeros(n, 1));
    dlnew = dl;
    dlnew.DATALINK_REPORT.uplink_err = mod((0:n - 1)' * 7, 256);
    dlnew.DATALINK_REPORT.uplink_ovrn = zeros(n, 1);
    failures = failures + check('plot_datalink older', @() plot_datalink(dl), 3);
    failures = failures + check('plot_datalink newer', @() plot_datalink(dlnew), 5);
    failures = failures + check('pprz.counter_steps', @() assert(isequal(pprz.counter_steps([250 254 3 3 10], 256)', ...
        [0 4 5 0 7])));
    ls.LINK_STATS = struct('t', [t; t], 'hop', [zeros(n, 1); ones(n, 1)], 'uptime', [t; t], ...
        'down_rx_err', [zeros(n, 1); (1:n)'], 'up_rx_err', zeros(2 * n, 1), 'down_tx_err', zeros(2 * n, 1), ...
        'up_tx_err', zeros(2 * n, 1), 'down_max_gap', 100 * ones(2 * n, 1), 'up_max_gap', 200 * ones(2 * n, 1), ...
        'rssi', [127 * ones(n, 1); -70 * ones(n, 1)], 'rssi_ac', -75 * ones(2 * n, 1), ...
        'battery', [255 * ones(n, 1); 80 * ones(n, 1)], 'temperature', 127 * ones(2 * n, 1));
    failures = failures + check('plot_link_stats', @() plot_link_stats(ls));

    % AIR_DATA without and with sensor ids; BARO_RAW as abs/diff (older) or per sensor (newer)
    ba.AIR_DATA = struct('t', t, 'pressure', 101325 * ones(n, 1), 'diff_p', zeros(n, 1), 'temp', 20 * ones(n, 1), ...
        'qnh', 1013 * ones(n, 1), 'amsl_baro', 10 * ones(n, 1), 'airspeed', zeros(n, 1), 'tas', zeros(n, 1));
    baold = ba;
    baold.BARO_RAW = struct('t', t, 'abs', 101300 * ones(n, 1), 'diff', zeros(n, 1));
    banew = ba;
    banew.AIR_DATA.baro_id = ones(n, 1);
    banew.AIR_DATA.airspeed_id = zeros(n, 1);
    banew.BARO_RAW = struct('t', [t; t], 'sensor_id', [ones(n, 1); 2 * ones(n, 1)], 'raw', 101300 * ones(2 * n, 1), ...
        'pressure', 101320 * ones(2 * n, 1), 'offset', 20 * ones(2 * n, 1), 'temperature', [25 * ones(n, 1); NaN(n, 1)]);
    failures = failures + check('plot_baro AIR_DATA only', @() plot_baro(ba), 2);
    failures = failures + check('plot_baro older BARO_RAW', @() plot_baro(baold), 3);
    failures = failures + check('plot_baro newer BARO_RAW', @() plot_baro(banew), 10);
    failures = failures + check('plot_airspeed', @() plot_airspeed(banew), 1);

    list = pprz_plots();
    failures = failures + check('pprz_plots', @() assert(all(ismember({'esc', 'servo', 'baro', 'link_stats', 'ekf2'}, ...
        {list.name}))));
    fprintf('%d failure(s)\n', failures);
end

function failed = check(name, fn, lines, expect)
    % fn must run; with lines, draw that many lines; with expect, draw a line of that name
    global PPRZ_TEST_LINES %#ok<GVMIS>
    PPRZ_TEST_LINES = {};
    failed = 1;
    try
        fn();
        if nargin > 2 && ~isempty(lines) && numel(PPRZ_TEST_LINES) ~= lines
            error('drew %d lines, expected %d', numel(PPRZ_TEST_LINES), lines);
        end
        if nargin > 3 && ~any(strcmp(PPRZ_TEST_LINES, expect))
            error('no line named %s', expect);
        end
        fprintf('ok   %s\n', name);
        failed = 0;
    catch err
        fprintf('FAIL %s: %s\n', name, err.message);
    end
end

function esc_fields(old, new, n)
    e = pprz.esc(old);
    assert(strcmp(e.source, 'ESC') && numel(e.t) == 2 * n && all(e.voltage == 15) && all(e.supply_voltage == 16));
    e = pprz.esc(new);
    assert(strcmp(e.source, 'ESC_TELEMETRY') && numel(e.t) == 3 * n && issorted(e.t));
    assert(all(isnan(e.rpm(e.id == 2))), 'a value with age 255 must be NaN');
    assert(all(isnan(e.supply_voltage)));
    e = pprz.esc(struct());
    assert(isempty(e.t));
    one.ESC_TELEMETRY = struct('t', (0:n - 1) * 0.1, 'idx', zeros(1, n), 'rpm', 1000 * ones(1, n), 'current', ones(1, n));
    e = pprz.esc(one);  % a single actuator can come out as rows
    assert(numel(e.t) == n && all(e.rpm == 1000));
    s = pprz.servo(old);
    assert(isempty(s.t));
end
