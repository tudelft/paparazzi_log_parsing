function list = pprz_plots()
%PPRZ_PLOTS The plots pprz_plot and pprz_view offer, as a struct array with
%   name (for pprz_plot), group, title, needs (messages; any one is enough) and fn (called as fn(f.data, f)).
%   Add a plot by writing plotters/plot_xxx.m and a line here.
    p = @(name, group, title, needs, fn) struct('name', name, 'group', group, 'title', title, ...
        'needs', {needs}, 'fn', fn);
    list = [
        p('overview',       'Overview',            'Flight overview',               {'ROTORCRAFT_FP', 'ENERGY', 'ROTORCRAFT_STATUS'}, @plot_overview)
        p('track',          'Overview',            'Track / map',                   {'GPS_INT', 'ROTORCRAFT_FP'}, @(d, f) plot_track(d))
        p('status',         'Overview',            'Rotorcraft status',             {'ROTORCRAFT_STATUS'}, @plot_rotorcraft_status)
        p('settings',       'Overview',            'Setting changes',               {}, @plot_settings)
        p('euler',          'Attitude',            'Euler angles',                  {'AHRS_REF_QUAT'}, @(d, f) plot_eul(d))
        p('stab',           'Attitude',            'Stabilisation attitude',        {'STAB_ATTITUDE'}, @(d, f) plot_stab_attitude(d))
        p('stab_indi',      'Attitude',            'Stabilisation INDI',            {'STAB_ATTITUDE_INDI'}, @(d, f) plot_stab_attitude_indi(d))
        p('indi_rotwing',   'Attitude',            'INDI rotating wing',            {'STAB_ATTITUDE'}, @(d, f) plot_indi_rotwing(d))
        p('rates',          'Attitude',            'Body rates / acceleration',     {'BODY_RATES_ACCEL'}, @(d, f) plot_body_rates_accel(d))
        p('position',       'Guidance',            'Position, angles and speed',    {'ROTORCRAFT_FP'}, @(d, f) plot_rotorcraft_fp(d))
        p('guidance',       'Guidance',            'Guidance',                      {'GUIDANCE'}, @(d, f) plot_guidance(d))
        p('guidance_indi',  'Guidance',            'Guidance INDI hybrid',          {'GUIDANCE_INDI_HYBRID'}, @(d, f) plot_guidance_indi_hybrid(d))
        p('hybrid',         'Guidance',            'Hybrid guidance',               {'HYBRID_GUIDANCE'}, @(d, f) plot_hybrid_guidance(d))
        p('hover_loop',     'Guidance',            'Hover loop',                    {'HOVER_LOOP'}, @(d, f) plot_hover_loop(d))
        p('cmd',            'Guidance',            'Rotorcraft commands',           {'ROTORCRAFT_CMD'}, @(d, f) plot_rotorcraft_cmd(d))
        p('gps',            'Navigation sensors',  'GPS',                           {'GPS_INT'}, @(d, f) plot_gps(d))
        p('ekf2',           'Navigation sensors',  'GPS / EKF2',                    {'INS_EKF2'}, @(d, f) plot_ekf2(d))
        p('optitrack',      'Navigation sensors',  'External pose (OptiTrack)',     {'EXTERNAL_POSE_DOWN'}, @(d, f) plot_optitrack(d))
        p('airspeed',       'Navigation sensors',  'Airspeed',                      {'AIR_DATA', 'AIRSPEED_RAW'}, @(d, f) plot_airspeed(d))
        p('lidar',          'Navigation sensors',  'Lidar',                         {'LIDAR'}, @(d, f) plot_lidar(d))
        p('ground',         'Navigation sensors',  'Ground detection',              {'GROUND_DETECT'}, @(d, f) plot_ground_detect(d))
        p('imu',            'IMU',                 'IMU raw',                       {'IMU_ACCEL', 'IMU_GYRO', 'IMU_MAG'}, @(d, f) plot_imu(d))
        p('imu_scaled',     'IMU',                 'IMU scaled',                    {'IMU_ACCEL_SCALED', 'IMU_GYRO_SCALED', 'IMU_MAG_SCALED'}, @(d, f) plot_imu_scaled(d))
        p('imu_fft',        'IMU',                 'IMU spectrum (FFT)',            {'IMU_ACCEL', 'IMU_GYRO'}, @(d, f) plot_imu_fft(d))
        p('actuators',      'Actuators & power',   'Actuators',                     {'ACTUATORS'}, @(d, f) plot_actuators(d))
        p('esc',            'Actuators & power',   'ESC telemetry',                 {'ESC'}, @(d, f) plot_esc(d))
        p('t4',             'Actuators & power',   'Serial actuators (T4)',         {'SERIAL_ACT_T4_IN'}, @(d, f) plot_serial_act_t4(d))
        p('energy',         'Actuators & power',   'Energy',                        {'ENERGY'}, @(d, f) plot_energy(d))
        p('power',          'Actuators & power',   'Power devices',                 {'POWER_DEVICE'}, @(d, f) plot_powers(d))
        p('eff_mat',        'Effectiveness',       'Effectiveness matrix (G)',      {'EFF_MAT_G'}, @(d, f) plot_eff_mat(d))
        p('eff_mat_stab',   'Effectiveness',       'Effectiveness matrix (stab)',   {'EFF_MAT_STAB'}, @(d, f) plot_eff_mat_stab(d))
        p('rotwing',        'Rotating wing',       'Rotating wing state',           {'ROTATING_WING_STATE'}, @(d, f) plot_rotwing_state(d))
        p('rc',             'Radio & errors',      'RC controls and switches',      {'ROTORCRAFT_RADIO_CONTROL'}, @(d, f) plot_rc_controls(d))
        p('errors',         'Radio & errors',      'I2C errors',                    {'I2C_ERRORS'}, @(d, f) plot_errors(d))
        ];
end
