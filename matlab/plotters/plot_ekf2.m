function plot_ekf2(ac_data)
%PLOT_EKF2 EKF2 innovation test ratios and, from INS_EKF2_EXT, what the log has of:
%   GPS drift (all versions); vibration metrics (logs before pprzlink 2026-11); and the EKF2 status of newer logs:
%   position/velocity accuracy, height above ground and height biases, the emergency (GSF) yaw estimate, and the
%   measured magnetic field against the world magnetic model. Panels without data are left out.
    e = ac_data.INS_EKF2;
    x = struct();
    if pprz.has(ac_data, 'INS_EKF2_EXT')
        x = ac_data.INS_EKF2_EXT;
    end
    has = @(varargin) all(cellfun(@(n) isfield(x, n), varargin));
    panels = {@innovations};
    if has('accel_vibe_hf', 'gyro_vibe_hf', 'gyro_vibe_coning'), panels{end + 1} = @vibration; end
    if has('gps_drift_h', 'gps_drift_v'), panels{end + 1} = @drift; end
    if has('pos_acc_h', 'pos_acc_v', 'vel_acc_h', 'vel_acc_v'), panels{end + 1} = @accuracy; end
    if has('hagl') || has('baro_bias') || has('gnss_hgt_bias'), panels{end + 1} = @heights; end
    if has('yaw_gsf'), panels{end + 1} = @gsf_yaw; end
    if has('mag_inclination', 'mag_inclination_ref'), panels{end + 1} = @inclination; end
    if has('mag_strength', 'mag_strength_ref'), panels{end + 1} = @strength; end
    axs = pprz.tiles(numel(panels), 'INS EKF2');
    for k = 1:numel(panels)
        panels{k}(axs(k), e, x);
    end
end

function innovations(ax, e, ~)
    fields = {'innov_mag', 'Magnetometer'; 'innov_vel', 'Velocity'; 'innov_pos', 'Position'; ...
        'innov_hgt', 'Height'; 'innov_tas', 'Airspeed'; 'innov_hagl', 'Height AGL'; 'innov_beta', 'Sideslip'; ...
        'innov_flow', 'Optical flow'};
    for k = 1:size(fields, 1)
        if ~isfield(e, fields{k, 1})
            continue
        end
        v = double(e.(fields{k, 1}));
        if any(v ~= 0) && max(abs(v)) < 1e6  % unused ones can hold garbage
            plot(ax, e.t, v, 'DisplayName', fields{k, 2});
        end
    end
    ylabel(ax, 'Innovation test ratio');
    legend(ax, 'Location', 'eastoutside');
end

function vibration(ax, ~, x)
    pprz.series(ax, x.t, [x.accel_vibe_hf, x.gyro_vibe_hf, x.gyro_vibe_coning], ...
        {'Accel high freq.', 'Gyro high freq.', 'Gyro coning'});
    ylabel(ax, 'Vibration metric');
end

function drift(ax, ~, x)
    cols = [x.gps_drift_h, x.gps_drift_v];
    names = {'Horizontal', 'Vertical'};
    if isfield(x, 'gps_drift_h_filt')
        cols = [x.gps_drift_h, x.gps_drift_h_filt, x.gps_drift_v];
        names = {'Horizontal', 'Horizontal filtered', 'Vertical'};
    end
    pprz.series(ax, x.t, cols, names);
    ylabel(ax, 'GPS drift [m/s]');
end

function accuracy(ax, ~, x)
    pprz.series(ax, x.t, [x.pos_acc_h, x.pos_acc_v, x.vel_acc_h, x.vel_acc_v], ...
        {'Position horizontal [m]', 'Position vertical [m]', 'Velocity horizontal [m/s]', 'Velocity vertical [m/s]'});
    ylabel(ax, 'Accuracy (1σ)');
end

function heights(ax, ~, x)
    fields = {'hagl', 'Height above ground'; 'baro_bias', 'Baro height bias'; 'gnss_hgt_bias', 'GNSS height bias'};
    for k = 1:size(fields, 1)
        if isfield(x, fields{k, 1}) && any(~isnan(double(x.(fields{k, 1}))))
            plot(ax, x.t, double(x.(fields{k, 1})), 'DisplayName', fields{k, 2});
        end
    end
    ylabel(ax, 'Height [m]');
    legend(ax, 'Location', 'best');
end

function gsf_yaw(ax, ~, x)
    % Emergency yaw estimator: NaN when not available. The export keeps radians (no alt_unit_coef).
    yaw = rad2deg(double(x.yaw_gsf));
    plot(ax, x.t, yaw, 'DisplayName', 'GSF yaw');
    if isfield(x, 'yaw_gsf_std')
        std_ = rad2deg(double(x.yaw_gsf_std));
        plot(ax, x.t, yaw + std_, ':', 'DisplayName', '±1σ');
        plot(ax, x.t, yaw - std_, ':', 'HandleVisibility', 'off');
    end
    ylabel(ax, 'GSF yaw [deg]');
    legend(ax, 'Location', 'best');
end

function inclination(ax, ~, x)
    pprz.series(ax, x.t, [x.mag_inclination, x.mag_inclination_ref], {'Measured', 'World magnetic model'});
    ylabel(ax, 'Magnetic inclination [deg]');
end

function strength(ax, ~, x)
    pprz.series(ax, x.t, [x.mag_strength, x.mag_strength_ref], {'Measured', 'World magnetic model'});
    ylabel(ax, 'Magnetic field [gauss]');
end
